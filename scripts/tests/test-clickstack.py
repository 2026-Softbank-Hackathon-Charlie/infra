#!/usr/bin/env python3
"""Offline Helm contracts plus pinned operator/OTel integration when cached charts are supplied."""
import copy
import hashlib
import json
import os
from pathlib import Path
import re
import runpy
import subprocess
import tempfile
import unittest

import yaml

ROOT = Path(__file__).resolve().parents[2]
HELM = os.environ.get('HELM', 'helm')
LOCK = json.loads((ROOT / 'helm/images.lock.json').read_text())['images']


def render(chart, release='clickstack', extra=(), values=None):
    args = [HELM, 'template', release, str(chart), '-n', 'observability', '--kube-version', '1.35.0', '--include-crds']
    with tempfile.TemporaryDirectory() as tmp:
        if values is not None:
            p = Path(tmp) / 'values.json'
            p.write_text(json.dumps(values))
            args += ['-f', str(p)]
        result = subprocess.run(args + list(extra), capture_output=True, text=True)
    if result.returncode:
        raise ValueError(result.stderr)
    return [d for d in yaml.safe_load_all(result.stdout) if d]


def image_refs(obj):
    if isinstance(obj, dict):
        image = obj.get('image')
        if isinstance(image, str):
            yield image
        elif isinstance(image, dict) and 'repository' in image and 'tag' in image:
            yield image['repository'] + ':' + image['tag']
        # Community operator configures these generated-container images via env.
        if isinstance(obj.get('name'), str) and obj.get('name') in {'MDB_COMMUNITY_AGENT_IMAGE', 'VERSION_UPGRADE_HOOK_IMAGE', 'READINESS_PROBE_IMAGE'}:
            yield obj['value']
        for v in obj.values():
            yield from image_refs(v)
    elif isinstance(obj, list):
        for v in obj:
            yield from image_refs(v)


def check_images(docs):
    refs = set(image_refs(docs))
    assert refs, 'No images found'
    for ref in refs:
        assert re.fullmatch(r'.+@sha256:[0-9a-f]{64}', ref), 'Unpinned image: ' + ref
        name, digest = ref.rsplit('@', 1)
        assert LOCK.get(name) == digest, 'Image differs from lock: ' + name
    return refs


def validate_structural(value, schema, path='spec'):
    """Check supplied fields/types against pinned CRD structural schemas.

    Kubernetes CEL, defaulting and reconciliation remain deployment gates.
    """
    if schema.get('x-kubernetes-preserve-unknown-fields'):
        return
    expected = schema.get('type')
    types = {'object': dict, 'array': list, 'string': str, 'integer': int, 'number': (int, float), 'boolean': bool}
    if value is None and schema.get('nullable'):
        return
    if expected in types:
        assert isinstance(value, types[expected]), f'{path}: expected {expected}'
    if 'enum' in schema:
        assert value in schema['enum'], f'{path}: unsupported enum value'
    if isinstance(value, dict):
        assert all(k in value for k in schema.get('required', [])), f'{path}: missing required field'
        properties = schema.get('properties', {})
        for k, v in value.items():
            if k in properties:
                validate_structural(v, properties[k], path + '.' + k)
            elif isinstance(schema.get('additionalProperties'), dict):
                validate_structural(v, schema['additionalProperties'], path + '.' + k)
            elif properties:
                raise AssertionError(f'{path}: unknown field {k}')
    if isinstance(value, list) and 'items' in schema:
        for i, v in enumerate(value):
            validate_structural(v, schema['items'], f'{path}[{i}]')


class ClickStackTests(unittest.TestCase):
    @classmethod
    def setUpClass(cls):
        cls.docs = render(ROOT / 'helm/charts/clickstack')
        cls.by_kind = {}
        for d in cls.docs:
            cls.by_kind.setdefault(d['kind'], []).append(d)
        fixture = json.loads((ROOT / 'helm/gitops/ci/targets-values.yaml').read_text())
        cls.fixture = fixture
        enabled = copy.deepcopy(fixture)
        enabled['observability'] = {'enabled': True, 'agentTargets': ['management', 'workload'], 'gatewayTargets': ['management', 'workload']}
        cls.gitops = render(ROOT / 'helm/gitops', values=enabled)

    def test_langfuse_default_off(self):
        config = yaml.safe_load(next(d for d in self.docs if d['kind'] == 'ConfigMap' and d['metadata']['name'] == 'clickstack-otel-custom-config')['data']['custom.config.yaml'])
        self.assertNotIn('traces/langfuse-app', config['service']['pipelines'])
        self.assertNotIn('otlphttp/langfuse-app', config['exporters'])
        self.assertNotIn('LANGFUSE_APP_AUTHORIZATION', yaml.safe_dump(self.docs))

    def test_langfuse_exact_identity_and_private_attributes(self):
        values = json.loads((ROOT / 'clusters/aws-dev-management/values/clickstack.yaml').read_text())
        docs = render(ROOT / 'helm/charts/clickstack', values=values)
        config = yaml.safe_load(next(d for d in docs if d['kind'] == 'ConfigMap' and d['metadata']['name'] == 'clickstack-otel-custom-config')['data']['custom.config.yaml'])
        original = yaml.safe_load(next(d for d in self.docs if d['kind'] == 'ConfigMap' and d['metadata']['name'] == 'clickstack-otel-custom-config')['data']['custom.config.yaml'])
        for name in ('traces', 'logs'):
            self.assertEqual(config['service']['pipelines'][name], original['service']['pipelines'][name])
        pipeline = config['service']['pipelines']['traces/langfuse-app']
        self.assertEqual(pipeline['receivers'], ['otlp/hyperdx'])
        self.assertEqual(pipeline['exporters'], ['otlphttp/langfuse-app'])
        self.assertEqual(config['processors']['filter/langfuse-app'], {'error_mode': 'propagate', 'traces': {'span': ['resource.attributes["k8s.namespace.name"] != "moimroom-mig" or resource.attributes["service.name"] != "moimroom-mig-web"']}})
        privacy = config['processors']['transform/langfuse-private']
        self.assertEqual(privacy['error_mode'], 'propagate')
        statement = privacy['trace_statements'][0]['statements'][0]
        allowed = json.loads(statement.removeprefix('keep_keys(attributes, ').removesuffix(')'))
        self.assertIn('langfuse.observation.metadata.hopcharlie.app', allowed)
        self.assertIn('gen_ai.usage.output_tokens', allowed)
        for key in ('langfuse.observation.input', 'langfuse.observation.output', 'http.url', 'http.request.header.authorization', 'gen_ai.prompt', 'gen_ai.completion'):
            self.assertNotIn(key, allowed)
        self.assertIn('set(status.message, "")', privacy['trace_statements'][0]['statements'])
        for statement in ('set(events, nil)', 'set(links, nil)', 'set(trace_state, "")', 'set(name, "moimroom-request")', 'set(instrumentation_scope.name, "hopcharlie.app.llm")', 'keep_keys(instrumentation_scope.attributes, [])'):
            self.assertIn(statement, privacy['trace_statements'][0]['statements'])
        exporter = config['exporters']['otlphttp/langfuse-app']
        self.assertEqual(exporter['traces_endpoint'], 'http://langfuse-web.langfuse.svc.cluster.local:3000/api/public/otel/v1/traces')
        self.assertEqual(exporter['headers']['Authorization'], '${env:LANGFUSE_APP_AUTHORIZATION}')
        self.assertEqual(exporter['sending_queue']['storage'], 'file_storage/queue')
        self.assertEqual(exporter['retry_on_failure']['max_elapsed_time'], '60s')

    def test_langfuse_secret_and_egress_scope(self):
        values = json.loads((ROOT / 'clusters/aws-dev-management/values/clickstack.yaml').read_text())
        docs = render(ROOT / 'helm/charts/clickstack', values=values)
        collector = next(d for d in docs if d['kind'] == 'Deployment' and d['metadata']['name'] == 'clickstack-otel-collector')
        env = {e['name']: e for e in collector['spec']['template']['spec']['containers'][0]['env']}
        self.assertEqual(env['LANGFUSE_APP_AUTHORIZATION']['valueFrom']['secretKeyRef'], {'name': 'moimroom-mig-langfuse-exporter-auth', 'key': 'authorization'})
        self.assertIn('checksum/langfuse', collector['spec']['template']['metadata']['annotations'])
        content = next(d for d in docs if d['kind'] == 'ConfigMap' and d['metadata']['name'] == 'clickstack-otel-custom-config')['data']['custom.config.yaml'].rstrip('\n')
        self.assertEqual(collector['spec']['template']['metadata']['annotations']['checksum/langfuse-config'], hashlib.sha256(content.encode()).hexdigest())
        for d in docs:
            if d['kind'] == 'Deployment' and d['metadata']['name'] != 'clickstack-otel-collector':
                self.assertNotIn('LANGFUSE_APP_AUTHORIZATION', yaml.safe_dump(d))
        policy = next(d for d in docs if d['kind'] == 'NetworkPolicy' and d['metadata']['name'] == 'clickstack-langfuse-egress')['spec']
        self.assertEqual(policy['podSelector'], {'matchLabels': {'app.kubernetes.io/name': 'clickstack-collector'}})
        self.assertEqual(policy['egress'], [
            {'to': [{'namespaceSelector': {'matchLabels': {'kubernetes.io/metadata.name': 'kube-system'}}, 'podSelector': {'matchLabels': {'k8s-app': 'kube-dns'}}}], 'ports': [{'protocol': 'UDP', 'port': 53}, {'protocol': 'TCP', 'port': 53}]},
            {'to': [{'podSelector': {'matchLabels': {'app.kubernetes.io/name': 'clickhouse-server'}}}], 'ports': [{'protocol': 'TCP', 'port': 9000}]},
            {'to': [{'ipBlock': {'cidr': '172.20.238.122/32'}}, {'namespaceSelector': {'matchLabels': {'kubernetes.io/metadata.name': 'langfuse'}}, 'podSelector': {'matchLabels': {'app.kubernetes.io/name': 'langfuse', 'app.kubernetes.io/instance': 'langfuse', 'app.kubernetes.io/component': 'web'}}}], 'ports': [{'protocol': 'TCP', 'port': 3000}]},
        ])

    def test_langfuse_invalid_identity_rejected(self):
        values = json.loads((ROOT / 'clusters/aws-dev-management/values/clickstack.yaml').read_text())
        for field in ('namespace', 'serviceName'):
            for invalid in ('', 'moimroom-mig" or true', 'other/app'):
                with self.subTest(field=field, invalid=invalid):
                    changed = copy.deepcopy(values)
                    changed['langfuse'][field] = invalid
                    with self.assertRaises(ValueError):
                        render(ROOT / 'helm/charts/clickstack', values=changed)

    def test_langfuse_config_content_changes_rollout_checksum(self):
        values = json.loads((ROOT / 'clusters/aws-dev-management/values/clickstack.yaml').read_text())
        original = render(ROOT / 'helm/charts/clickstack', values=values)
        # Keep all values identical while changing only the rendered transform template.
        with tempfile.TemporaryDirectory() as tmp:
            import shutil
            chart = Path(tmp) / 'chart'
            shutil.copytree(ROOT / 'helm/charts/clickstack', chart)
            config = chart / 'templates/otel-collector/custom-config.yaml'
            text = config.read_text()
            self.assertIn('moimroom-generation', text)
            config.write_text(text.replace('moimroom-generation', 'moimroom-generation-revision'))
            changed = render(chart, values=values)
        def checksum(docs):
            return next(d for d in docs if d['kind'] == 'Deployment' and d['metadata']['name'] == 'clickstack-otel-collector')['spec']['template']['metadata']['annotations']['checksum/langfuse-config']
        self.assertNotEqual(checksum(original), checksum(changed))

    def test_langfuse_service_ip_rejects_subnets_and_invalid_values(self):
        values = json.loads((ROOT / 'clusters/aws-dev-management/values/clickstack.yaml').read_text())
        for invalid in ('0.0.0.0/0', '172.20.0.0/16', '256.20.1.1', 'localhost', '172.20.1.1"', '8.8.8.8', '127.0.0.1'):
            with self.subTest(ip=invalid):
                changed = copy.deepcopy(values)
                changed['langfuse']['serviceClusterIP'] = invalid
                with self.assertRaises(ValueError):
                    render(ROOT / 'helm/charts/clickstack', values=changed)

    def test_langfuse_service_binding_fails_closed(self):
        check = runpy.run_path(str(ROOT / 'scripts/check-langfuse-service-binding.py'))['check_binding']
        config = json.loads((ROOT / 'clusters/aws-dev-management/values/clickstack.yaml').read_text())['langfuse']
        service = {'metadata': {'name': 'langfuse-web', 'namespace': 'langfuse', 'uid': config['serviceUID']}, 'spec': {'clusterIP': config['serviceClusterIP'], 'selector': {'app.kubernetes.io/name': 'langfuse', 'app.kubernetes.io/instance': 'langfuse', 'app': 'web'}, 'ports': [{'port': 3000, 'protocol': 'TCP'}]}}
        self.assertTrue(check(config, service)['matched'])
        for target, field, value in [('metadata', 'uid', 'different'), ('metadata', 'namespace', 'other'), ('spec', 'clusterIP', '172.20.1.1'), ('spec', 'selector', {}), ('spec', 'ports', [{'port': 443}])]:
            changed = copy.deepcopy(service)
            changed[target][field] = value
            with self.subTest(field=field), self.assertRaises(ValueError):
                check(config, changed)

    def test_langfuse_policy_binding_does_not_restart_collector(self):
        values = json.loads((ROOT / 'clusters/aws-dev-management/values/clickstack.yaml').read_text())
        original = render(ROOT / 'helm/charts/clickstack', values=values)
        changed = copy.deepcopy(values)
        changed['langfuse']['serviceClusterIP'] = '172.20.1.1'
        changed['langfuse']['serviceUID'] = '00000000-0000-0000-0000-000000000001'
        updated = render(ROOT / 'helm/charts/clickstack', values=changed)
        for kind, name in [('Deployment', 'clickstack-otel-collector'), ('ConfigMap', 'clickstack-otel-custom-config')]:
            before = next(d for d in original if d['kind'] == kind and d['metadata']['name'] == name)
            after = next(d for d in updated if d['kind'] == kind and d['metadata']['name'] == name)
            self.assertEqual(before, after)

    def test_no_secret_material_in_render(self):
        self.assertNotIn('Secret', self.by_kind)
        text = yaml.safe_dump(self.docs)
        for forbidden in ('otelcollectorpass', 'FIXTURE_READ_PASSWORD', 'xxxxxxxx-xxxx', 'MONGO_URI: mongodb://'):
            self.assertNotIn(forbidden, text)
        for d in self.by_kind.get('ConfigMap', []):
            self.assertNotIn('MONGO_URI', d.get('data', {}))
            self.assertNotIn('DEFAULT_CONNECTIONS', d.get('data', {}))
        for d in self.by_kind.get('Deployment', []):
            for c in d['spec']['template']['spec']['containers']:
                for e in c.get('env', []):
                    if any(k in e['name'] for k in ('PASSWORD', 'SECRET', 'TOKEN', 'MONGO_URI', 'API_KEY', 'DEFAULT_CONNECTIONS')):
                        self.assertIn('secretKeyRef', e.get('valueFrom', {}), e['name'])

    def test_reject_credentials_in_values(self):
        for values in ({'hyperdx': {'secrets': {'MONGODB_PASSWORD': 'SYNTHETIC-DO-NOT-RENDER'}}},
                       {'hyperdx': {'config': {'MONGO_URI': 'SYNTHETIC-DO-NOT-RENDER'}}},
                       {'hyperdx': {'deployment': {'defaultConnections': 'SYNTHETIC-DO-NOT-RENDER'}}},
                       {'hyperdx': {'config': {'TOKEN_ENCRYPTION_KEY': 'SYNTHETIC-DO-NOT-RENDER'}}},
                       {'hyperdx': {'config': {'NEXT_PUBLIC_API_KEY': 'SYNTHETIC-DO-NOT-RENDER'}}},
                       {'hyperdx': {'deployment': {'env': [{'name': 'MONGO_URI', 'value': 'SYNTHETIC-DO-NOT-RENDER'}]}}}):
            with self.assertRaises(ValueError):
                render(ROOT / 'helm/charts/clickstack', values=values)

    def test_required_secret_references_fail_closed(self):
        for name in ('runtime', 'collector', 'clickhouse', 'mongodb', 'sources', 'otlpTLS'):
            with self.assertRaises(ValueError):
                render(ROOT / 'helm/charts/clickstack', values={'existingSecrets': {name: ''}})
        app = next(d for d in self.by_kind['Deployment'] if d['metadata']['name'] != 'clickstack-otel-collector')
        env = {e['name']: e for e in app['spec']['template']['spec']['containers'][0]['env']}
        for key in ('MONGO_URI', 'EXPRESS_SESSION_SECRET', 'TOKEN_ENCRYPTION_KEY', 'HYPERDX_API_KEY'):
            self.assertEqual(env[key]['valueFrom']['secretKeyRef']['key'], key)

    def test_external_secret_names_propagate(self):
        docs = render(ROOT / 'helm/charts/clickstack', values={'existingSecrets': {'clickhouse': 'fixture-ch', 'mongodb': 'fixture-mongo'}})
        ch = next(d for d in docs if d['kind'] == 'ClickHouseCluster')['spec']
        self.assertEqual(ch['settings']['defaultUserPassword']['secret']['name'], 'fixture-ch')
        self.assertTrue(all(e['valueFrom']['secretKeyRef']['name'] == 'fixture-ch' for e in ch['containerTemplate']['env']))
        mongo = next(d for d in docs if d['kind'] == 'MongoDBCommunity')
        self.assertEqual(mongo['spec']['users'][0]['passwordSecretRef']['name'], 'fixture-mongo')
        for user in ch['settings']['extraUsersConfig']['users'].values():
            self.assertIn('@from_env', user['password'][0])

    def test_images_include_generated_database_pods(self):
        refs = check_images(self.docs)
        self.assertTrue(any('mongodb-community-server' in r for r in refs))
        self.assertTrue(any('clickhouse-server' in r for r in refs))
        self.assertTrue(any('clickhouse-keeper' in r for r in refs))
        damaged = copy.deepcopy(self.docs)
        ch = next(d for d in damaged if d['kind'] == 'ClickHouseCluster')
        ch['spec']['containerTemplate']['image']['tag'] = 'latest'
        with self.assertRaises(AssertionError):
            check_images(damaged)

    def test_collector_bootstrap_auth_tls_queue(self):
        collector = next(d for d in self.by_kind['Deployment'] if d['metadata']['name'] == 'clickstack-otel-collector')
        c = collector['spec']['template']['spec']['containers'][0]
        self.assertEqual(c['command'], ['/bin/sh', '-ec'])
        self.assertEqual(c['args'], ['migrate /etc/otel/schema/seed; exec /otelcontribcol --config /etc/otelcol-contrib/custom/custom.config.yaml'])
        env = {e['name']: e for e in c['env']}
        self.assertIn('secretKeyRef', env['OTLP_AUTH_TOKEN']['valueFrom'])
        cm = next(d for d in self.by_kind['ConfigMap'] if d['metadata']['name'] == 'clickstack-otel-custom-config')
        config = yaml.safe_load(cm['data']['custom.config.yaml'])
        self.assertNotIn('grpc', config['receivers']['otlp/hyperdx']['protocols'])
        self.assertIn('tls', config['receivers']['otlp/hyperdx']['protocols']['http'])
        self.assertIn('bearertokenauth/hyperdx', config['service']['extensions'])
        self.assertLess(config['processors']['memory_limiter/central']['limit_mib'], 512)
        self.assertEqual(set(config['service']['pipelines']), {'logs', 'traces'})
        for pipeline in config['service']['pipelines'].values():
            for kind in ('receivers', 'processors', 'exporters'):
                self.assertTrue(pipeline[kind])
                for component in pipeline[kind]:
                    self.assertIn(component, config[kind])
        for extension in config['service']['extensions']:
            self.assertIn(extension, config['extensions'])
        exporter = config['exporters']['clickhouse']
        for key in ('database', 'endpoint', 'username', 'password', 'timeout', 'create_schema', 'json'):
            self.assertIn(key, exporter)
        self.assertNotIn('ttl', exporter)
        self.assertEqual(config['exporters']['clickhouse']['sending_queue']['storage'], 'file_storage/queue')
        self.assertEqual(collector['spec']['strategy']['type'], 'Recreate')

    def test_additional_cluster_tokens_are_secret_references(self):
        docs = render(ROOT / 'helm/charts/clickstack', values={'additionalIngestTokenKeys': ['gke-token']})
        c = next(d for d in docs if d['kind'] == 'Deployment' and d['metadata']['name'] == 'clickstack-otel-collector')
        env = {e['name']: e for e in c['spec']['template']['spec']['containers'][0]['env']}
        self.assertEqual(env['OTLP_EXTRA_TOKEN_0']['valueFrom']['secretKeyRef']['key'], 'gke-token')
        cm = next(d for d in docs if d['kind'] == 'ConfigMap' and d['metadata']['name'] == 'clickstack-otel-custom-config')
        config = yaml.safe_load(cm['data']['custom.config.yaml'])
        self.assertIn('Bearer ${env:OTLP_EXTRA_TOKEN_0}', config['extensions']['bearertokenauth/hyperdx']['tokens'])
        with self.assertRaises(ValueError):
            render(ROOT / 'helm/charts/clickstack', values={'additionalIngestTokenKeys': ['invalid key']})

    def test_prometheus_is_external_and_front_flags_are_public_only(self):
        cm = next(d for d in self.by_kind['ConfigMap'] if d['metadata']['name'] == 'clickstack-config')
        self.assertEqual(cm['data']['ENABLE_PROMQL'], 'true')
        self.assertEqual(cm['data']['NEXT_PUBLIC_ENABLE_PROMQL'], 'true')
        public = {k: v for k, v in cm['data'].items() if k.startswith('NEXT_PUBLIC_')}
        self.assertEqual(public, {'NEXT_PUBLIC_ENABLE_PROMQL': 'true'})
        self.assertEqual(cm['data']['HYPERDX_APP_PORT'], '3000')
        self.assertEqual(cm['data']['HYPERDX_API_PORT'], '8000')
        self.assertNotIn('CLICKHOUSE_PROMETHEUS_METRICS_ENDPOINT', cm['data'])
        connections = json.loads((ROOT / 'helm/charts/clickstack/ci/connections.json').read_text())
        self.assertTrue(next(c for c in connections if c['name'] == 'Management Prometheus')['isPrometheusEndpoint'])
        self.assertEqual(connections[1]['host'], 'http://monitoring-prometheus.observability.svc:9090')
        sources = json.loads((ROOT / 'helm/charts/clickstack/ci/sources.json').read_text())
        self.assertEqual({s['kind'] for s in sources}, {'log', 'trace', 'promql'})
        self.assertEqual(sources[0]['traceSourceId'], sources[1]['name'])
        self.assertEqual(sources[1]['logSourceId'], sources[0]['name'])
        # HyperDX 2.39.1 requires a nonempty default table projection for both
        # log and trace sources; a missing value triggers a UI validation warning.
        defaults = {
            'log': 'Timestamp,ServiceName,SeverityText,Body',
            'trace': 'Timestamp,ServiceName,StatusCode,round(Duration/1e6),SpanName',
        }
        for source in sources:
            if source['kind'] in defaults:
                self.assertEqual(source.get('defaultTableSelectExpression'), defaults[source['kind']])


    def test_smoke_payloads_are_correlated_and_sending_is_guarded(self):
        import importlib.util
        spec = importlib.util.spec_from_file_location('smoke', ROOT / 'scripts/observability-smoke.py')
        smoke = importlib.util.module_from_spec(spec)
        spec.loader.exec_module(smoke)
        logs, traces = smoke.payloads(100, 10)
        records = logs['resourceLogs'][0]['scopeLogs'][0]['logRecords']
        spans = traces['resourceSpans'][0]['scopeSpans'][0]['spans']
        self.assertEqual(len(records), 100)
        self.assertEqual(len(spans), 10)
        span_ids = {(s['traceId'], s['spanId']) for s in spans}
        self.assertTrue(all((r['traceId'], r['spanId']) in span_ids for r in records))
        for endpoint in ('http://fixture.invalid', 'https://user:pass@fixture.invalid', 'https://fixture.invalid?token=x'):
            with self.assertRaises(ValueError):
                smoke.send(endpoint, None, 'SYNTHETIC', logs, traces)
        with self.assertRaises(ValueError):
            smoke.send('https://fixture.invalid', None, '', logs, traces)
        with self.assertRaises(ValueError):
            smoke.payloads(100000, 10)

    def test_services_private_and_volumes_persistent(self):
        self.assertNotIn('Ingress', self.by_kind)
        for d in self.by_kind['Service']:
            self.assertEqual(d['spec']['type'], 'ClusterIP')
        self.assertEqual(self.by_kind['PersistentVolumeClaim'][0]['spec']['resources']['requests']['storage'], '2Gi')
        ch = self.by_kind['ClickHouseCluster'][0]['spec']
        self.assertEqual((ch['shards'], ch['replicas']), (1, 1))
        mongo = self.by_kind['MongoDBCommunity'][0]['spec']
        self.assertEqual(mongo['members'], 1)
        self.assertEqual({x['metadata']['name'] for x in mongo['statefulSet']['spec']['volumeClaimTemplates']}, {'data-volume', 'logs-volume'})

    def test_gitops_opt_in_manual_isolated_and_no_double_collectors(self):
        base = render(ROOT / 'helm/gitops', values=self.fixture)
        self.assertFalse(any(d['metadata']['name'].startswith('charlie-observability') or 'clickstack' in d['metadata']['name'] for d in base))
        new = [d for d in self.gitops if d.get('spec', {}).get('project') == 'charlie-observability']
        self.assertEqual(len(new), 6)
        for d in new:
            self.assertNotIn('automated', d['spec']['syncPolicy'])
            self.assertNotIn('finalizers', d['metadata'])
        old_by_name = {d['metadata']['name']: d for d in base}
        for d in self.gitops:
            if d['metadata']['name'] in old_by_name:
                self.assertEqual(d, old_by_name[d['metadata']['name']])
        bad = copy.deepcopy(self.fixture)
        bad['observability'] = {'enabled': True, 'agentTargets': ['management'], 'gatewayTargets': ['management']}
        bad['addons']['management'].append('opentelemetry-collector')
        with self.assertRaises(ValueError):
            render(ROOT / 'helm/gitops', values=bad)

    def test_gitops_projects_allow_new_resource_contract(self):
        project = next(d for d in self.gitops if d['metadata']['name'] == 'charlie-observability')['spec']
        self.assertNotIn({'group': '*', 'kind': '*'}, project['namespaceResourceWhitelist'])
        self.assertNotIn({'group': '', 'kind': 'Secret'}, project['namespaceResourceWhitelist'])
        allowed = {(p['group'], p['kind']) for p in project['namespaceResourceWhitelist']}
        for d in self.docs:
            group = d['apiVersion'].rpartition('/')[0]
            self.assertIn((group, d['kind']), allowed)

    def test_platform_egress_is_opt_in_and_narrow(self):
        values = json.loads((ROOT / 'clusters/aws-dev-management/values/hopcharlie-platform.yaml').read_text())
        values['ingress']['enabled'] = False
        base = render(ROOT / 'helm/charts/hopcharlie-platform', 'hopcharlie-platform', values=values)
        values.setdefault('observability', {})['enabled'] = True
        enabled = render(ROOT / 'helm/charts/hopcharlie-platform', 'hopcharlie-platform', values=values)
        policy = next(d for d in enabled if d['metadata']['name'] == 'hopcharlie-otel-egress')
        self.assertEqual([d for d in enabled if d is not policy], base)
        target = policy['spec']['egress'][0]
        self.assertEqual(target['ports'], [{'protocol': 'TCP', 'port': 4318}])
        self.assertEqual(target['to'][0]['namespaceSelector']['matchLabels'], {'kubernetes.io/metadata.name': 'observability'})
        self.assertEqual(target['to'][0]['podSelector']['matchLabels']['app.kubernetes.io/instance'], 'observability-gateway')

    def test_operator_pinned_render_and_generated_helpers(self):
        path = os.environ.get('CLICKSTACK_OPERATORS_CHART')
        if not path:
            self.skipTest('Set CLICKSTACK_OPERATORS_CHART for pinned operator integration')
        values = json.loads((ROOT / 'clusters/aws-dev-management/values/clickstack-operators.yaml').read_text())
        docs = render(path, 'clickstack-operators', values=values)
        crd_by_kind = {d['spec']['names']['kind']: d for d in docs if d['kind'] == 'CustomResourceDefinition'}
        for resource in self.docs:
            if resource['kind'] in crd_by_kind:
                version = resource['apiVersion'].split('/')[-1]
                crd = crd_by_kind[resource['kind']]
                schema = next(v['schema']['openAPIV3Schema'] for v in crd['spec']['versions'] if v['name'] == version)
                validate_structural(resource['spec'], schema['properties']['spec'])
        refs = check_images(docs)
        self.assertTrue(any('mongodb-agent' in r for r in refs))
        self.assertTrue(any('readinessprobe' in r for r in refs))
        self.assertTrue(any('post-start-hook' in r for r in refs))
        roles = next(d for d in docs if d['kind'] == 'Deployment' and 'mongodb' in d['metadata']['name'])
        env = {e['name']: e for e in roles['spec']['template']['spec']['containers'][0]['env']}
        self.assertEqual(env['WATCH_NAMESPACE']['value'], 'observability')
        crds = {d['metadata']['name'] for d in docs if d['kind'] == 'CustomResourceDefinition'}
        self.assertTrue({'clickhouseclusters.clickhouse.com', 'keeperclusters.clickhouse.com', 'mongodbcommunity.mongodbcommunity.mongodb.com'} <= crds)

    def test_agent_gateway_render_contract(self):
        path = os.environ.get('OTEL_CHART')
        if not path:
            self.skipTest('Set OTEL_CHART for pinned agent/gateway integration')
        for purpose, namespace in [('management', 'charlie-platform'), ('workload', 'charlie-apps')]:
            for name, release in [('opentelemetry-collector', 'observability-agent'), ('otel-gateway', 'observability-gateway')]:
                values = json.loads((ROOT / f'clusters/aws-dev-{purpose}/values/{name}.yaml').read_text())
                docs = render(path, release, values=values)
                check_images(docs)
                cm = next(d for d in docs if d['kind'] == 'ConfigMap')
                config = yaml.safe_load(cm['data']['relay'])
                self.assertNotIn('metrics', config['service']['pipelines'])
                self.assertEqual(config['exporters']['otlphttp/central']['tls']['ca_file'], '/etc/otel/client/ca.crt')
                self.assertEqual(config['exporters']['otlphttp/central']['sending_queue']['storage'], 'file_storage/queue')
                if name == 'opentelemetry-collector':
                    self.assertEqual(config['receivers']['filelog']['include'], [f'/var/log/pods/{namespace}_*/*/*.log'])
                    self.assertIn('file_storage', config['service']['extensions'])
                    self.assertNotIn('kubeletstats', config['receivers'])
                else:
                    deployment = next(d for d in docs if d['kind'] == 'Deployment')
                    for c in deployment['spec']['template']['spec']['containers']:
                        self.assertFalse(any(p.get('hostPort') for p in c.get('ports', [])))
                    self.assertIn('tls', config['receivers']['otlp']['protocols']['http'])
                    self.assertEqual(config['receivers']['otlp']['protocols']['http']['auth']['authenticator'], 'bearertokenauth/server')
                    self.assertNotIn('ClusterRole', {d['kind'] for d in docs})


if __name__ == '__main__':
    unittest.main()
