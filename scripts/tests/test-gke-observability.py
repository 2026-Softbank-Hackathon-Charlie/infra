#!/usr/bin/env python3
"""GKE preparation contracts in the existing Helm CI; no cloud or cluster access."""
import base64
import copy
import json
import os
from pathlib import Path
import re
import subprocess
import tempfile
import unittest
import yaml

ROOT = Path(__file__).resolve().parents[2]
HELM = os.environ.get('HELM', 'helm')
LOCK = json.loads((ROOT / 'helm/images.lock.json').read_text())['images']

def load(path):
    return yaml.safe_load((ROOT / path).read_text())

def render(chart, values=None, release='check', namespace='observability', extra=()):
    path = Path(chart)
    if not path.is_absolute():
        path = ROOT / path
    with tempfile.TemporaryDirectory() as temp:
        command = [HELM, 'template', release, str(path), '-n', namespace, '--kube-version', '1.35.0' if namespace == 'argocd' else '1.36.0', *extra]
        if values is not None:
            f = Path(temp) / 'values.json'
            f.write_text(json.dumps(values))
            command += ['-f', str(f)]
        result = subprocess.run(command, capture_output=True, text=True)
    if result.returncode:
        raise ValueError(result.stderr)
    return [d for d in yaml.safe_load_all(result.stdout) if d]

def of(docs, kind):
    return [x for x in docs if x['kind'] == kind]

def images(obj):
    if isinstance(obj, dict):
        for k, v in obj.items():
            if k == 'image' and isinstance(v, str):
                yield v
            yield from images(v)
    elif isinstance(obj, list):
        for v in obj:
            yield from images(v)

class GKEObservability(unittest.TestCase):
    def root(self):
        v = load('helm/gitops/ci/targets-values.yaml')
        v['observability'] = {'enabled': True, 'automatic': {'enabled': True}, 'agentTargets': ['management', 'workload'], 'gatewayTargets': ['management', 'workload']}
        return v

    def test_preparation_is_off_and_aws_root_objects_are_identical(self):
        v = self.root()
        before = render('helm/gitops', v, namespace='argocd')
        v['observability']['externalTargets'] = load('clusters/gcp-dev-workload/values/observability-activation.yaml')['observability']['externalTargets']
        self.assertFalse(v['observability']['externalTargets']['gke']['enabled'])
        after = render('helm/gitops', v, namespace='argocd')
        self.assertEqual(before, after)
        self.assertNotIn('gke', v['targets'])
        self.assertFalse([d for d in after if d['metadata']['name'].startswith('charlie-gke-')])
        apps = load('clusters/aws-dev-management/values/hopcharlie-apps.yaml')['environments']
        self.assertFalse(apps['gke']['profile']['observability']['enabled'])
        self.assertFalse(load('clusters/gcp-dev-workload/values/app-observability.yaml')['environments']['gke']['profile']['observability']['enabled'])
        for role, directory in [('gke', 'gcp-dev-workload'), ('management', 'aws-dev-management')]:
            values = load(f'clusters/{directory}/values/observability-transport.yaml')
            self.assertFalse(values['enabled'])
            self.assertEqual(render('helm/charts/observability-transport', values), [])

    def test_external_targets_are_manual_and_do_not_create_an_aws_baseline(self):
        v = self.root()
        old = render('helm/gitops', v, namespace='argocd')
        gke = load('clusters/gcp-dev-workload/values/observability-activation.yaml')['observability']['externalTargets']
        gke['gke']['enabled'] = True
        v['observability']['externalTargets'] = gke
        docs = render('helm/gitops', v, namespace='argocd')
        original = {d['metadata']['name']: d for d in old}
        self.assertEqual([d for d in docs if d['metadata']['name'] in original], old)
        extra = [d for d in docs if d['metadata']['name'] not in original]
        self.assertEqual(len(of(extra, 'Application')), 6)
        self.assertEqual(len(of(extra, 'AppProject')), 1)
        self.assertNotIn('charlie-gke-baseline', [d['metadata']['name'] for d in docs])
        for app in of(extra, 'Application'):
            self.assertEqual(app['spec']['project'], 'charlie-observability-gke')
            self.assertNotIn('automated', app['spec'].get('syncPolicy', {}))
            self.assertNotIn('CreateNamespace=true', app['spec']['syncPolicy']['syncOptions'])
        app = next(d for d in extra if d['metadata']['name'] == 'charlie-gke-kube-prometheus-stack')
        source = app['spec']['sources'][0]
        self.assertEqual((source['chart'], source['targetRevision']), ('kube-prometheus-stack', '91.8.2'))
        self.assertEqual(source['helm']['releaseName'], 'monitoring')
        self.assertEqual(source['helm']['valueFiles'], ['$values/clusters/gcp-dev-workload/values/kube-prometheus-stack.yaml'])
        self.assertEqual(app['spec']['destination']['server'], gke['gke']['endpoint'])
        self.assertNotIn('onprem', json.dumps(extra))
        # Reserved keys/paths cannot route arbitrary directories or environments.
        for patch in [{'valuesDir': 'clusters/aws-dev-workload'}, {'endpoint': 'http://unsafe.invalid'}]:
            bad = copy.deepcopy(v)
            bad['observability']['externalTargets']['gke'].update(patch)
            with self.assertRaises(ValueError):
                render('helm/gitops', bad, namespace='argocd')

    def test_query_service_mode_preserves_aws_and_drops_aws_annotations_on_gke(self):
        aws = load('clusters/aws-dev-workload/values/observability-boundary.yaml')
        before = render('helm/charts/observability-boundary', aws)
        explicit = copy.deepcopy(aws)
        explicit['metrics']['service'] = {'type': 'LoadBalancer'}
        self.assertEqual(before, render('helm/charts/observability-boundary', explicit))
        gke = render('helm/charts/observability-boundary', load('clusters/gcp-dev-workload/values/observability-boundary.yaml'))
        service = of(gke, 'Service')[0]
        self.assertEqual(service['spec']['type'], 'ClusterIP')
        self.assertNotIn('annotations', service['metadata'])
        self.assertNotIn('loadBalancerSourceRanges', service['spec'])
        for role in of(gke, 'ClusterRole'):
            for rule in role['rules']:
                self.assertNotIn('secrets', rule.get('resources', []))
                self.assertNotIn('pods/log', rule.get('resources', []))
        extra = next(r for r in of(gke, 'ClusterRole') if r['metadata']['name'] == 'monitoring-gke-kube-state-metrics')
        self.assertNotIn('configmaps', json.dumps(extra))

    def test_dedicated_transport_ports_selectors_and_fixed_tls_name(self):
        for role, directory in [('gke', 'gcp-dev-workload'), ('management', 'aws-dev-management')]:
            v = load(f'clusters/{directory}/values/observability-transport.yaml')
            v['enabled'] = True
            docs = render('helm/charts/observability-transport', v)
            self.assertEqual(len(of(docs, 'ProxyClass')), 2)
            self.assertFalse(of(docs, 'Secret'))
            services = {s['metadata']['name']: s for s in of(docs, 'Service')}
            self.assertFalse(any(s['spec']['type'] in ['LoadBalancer', 'NodePort'] for s in services.values()))
            text = json.dumps(docs)
            self.assertNotIn('tag:hop-db', text)
            self.assertNotIn('5432', text)
            if role == 'management':
                self.assertIn('observability-gke-metrics', services)
                self.assertEqual(services['observability-gke-metrics']['spec']['ports'][0]['port'], 443)
                self.assertEqual(services['observability-central-ingress']['spec']['ports'][0]['port'], 4318)
                self.assertEqual(services['observability-central-ingress']['spec']['selector'], {'app.kubernetes.io/name': 'clickstack-collector'})
            else:
                self.assertEqual(services['observability-central']['spec']['ports'][0]['port'], 4318)
                self.assertEqual(services['observability-gke-query-ingress']['spec']['ports'][0]['targetPort'], 8443)
            ingress = next(x for x in of(docs, 'NetworkPolicy') if x['metadata']['name'] == 'observability-from-tailnet')
            peer = ingress['spec']['ingress'][0]['from'][0]
            self.assertEqual(set(peer), {'namespaceSelector', 'podSelector'})
            self.assertEqual(peer['podSelector']['matchLabels']['tailscale.com/parent-resource-ns'], 'observability')
        contract = load('clusters/gcp-dev-workload/observability-contract.json')
        self.assertEqual(contract['hyperdxPrometheusURL'], 'https://observability-gke-metrics.observability.svc.cluster.local:443')
        self.assertIn('observability-gke-metrics.observability.svc.cluster.local', contract['queryCertificateDNSNames'])
        tokens = load('clusters/aws-dev-management/values/clickstack-gke.yaml')['additionalIngestTokenKeys']
        self.assertEqual(tokens, ['WORKLOAD_OTLP_AUTH_TOKEN', 'GKE_OTLP_AUTH_TOKEN'])

    def test_pinned_gke_collectors_identity_storage_and_discovery(self):
        all_docs = []
        for variable, file, release in [('ALLOY_CHART', 'alloy.yaml', 'observability-agent'), ('OTEL_CHART', 'otel-gateway-automatic.yaml', 'observability-gateway'), ('KPS_CHART', 'kube-prometheus-stack.yaml', 'monitoring')]:
            self.assertIn(variable, os.environ)
            docs = render(Path(os.environ[variable]), load('clusters/gcp-dev-workload/values/' + file), release)
            all_docs.extend(docs)
            for image in images(docs):
                name, digest = image.rsplit('@', 1)
                if name.endswith('grafana/alloy'):
                    name += ':v1.21.3'
                self.assertEqual(LOCK.get(name), digest, image)
            if variable == 'ALLOY_CHART':
                ds = of(docs, 'DaemonSet')[0]
                self.assertFalse(any(v.get('persistentVolumeClaim') for v in ds['spec']['template']['spec']['volumes']))
                self.assertTrue(any(v.get('hostPath', {}).get('path') == '/var/lib/charlie-alloy' for v in ds['spec']['template']['spec']['volumes']))
                content = next(d for d in of(docs, 'ConfigMap') if 'config.alloy' in d.get('data', {}))['data']['config.alloy']
                self.assertIn('charlie-dev-gcp-workload', content)
                self.assertIn('"cloud.provider"], "gcp"', content)
                self.assertNotIn('ap-northeast-2', content)
            elif variable == 'OTEL_CHART':
                pvc = of(docs, 'PersistentVolumeClaim')[0]
                self.assertEqual(pvc['spec']['storageClassName'], 'charlie-gcp-balanced')
                config = yaml.safe_load(of(docs, 'ConfigMap')[0]['data']['relay'])
                attrs = {a['key']: a['value'] for a in config['processors']['resource/cluster']['attributes']}
                self.assertEqual(attrs['cloud.provider'], 'gcp')
                self.assertEqual(attrs['cloud.region'], 'asia-northeast3')
                self.assertEqual(attrs['cloud.availability_zone'], 'asia-northeast3-a')
                self.assertIsNone(config['service']['pipelines']['metrics'])
                upstream = config['exporters']['otlphttp/central']['tls']
                self.assertEqual(upstream['server_name_override'], 'clickstack-otel-collector.observability.svc')
                self.assertFalse(upstream.get('insecure_skip_verify', False))
            else:
                prom = of(docs, 'Prometheus')[0]['spec']
                self.assertEqual(prom['serviceMonitorSelector']['matchLabels'], {'release': 'monitoring'})
                self.assertEqual(prom['podMonitorSelector']['matchLabels'], {'observability.hopcharlie.dev/monitor': 'cnpg'})
                self.assertEqual(prom['storage']['volumeClaimTemplate']['spec']['storageClassName'], 'charlie-gcp-balanced')
                monitors = of(docs, 'ServiceMonitor')
                for name in ['monitoring-kube-state-metrics', 'monitoring-node-exporter']:
                    monitor = next(x for x in monitors if x['metadata']['name'] == name)
                    self.assertEqual(monitor['metadata']['labels']['release'], 'monitoring')
                self.assertFalse(any(s.get('type') == 'kubernetes.io/service-account-token' for s in of(docs, 'Secret')))
                config = load('clusters/gcp-dev-workload/values/kube-prometheus-stack.yaml')['prometheus']['prometheusSpec']['additionalScrapeConfigs']
                for scrape in config:
                    self.assertFalse(scrape['tls_config']['insecure_skip_verify'])
                    self.assertEqual(scrape['authorization']['credentials_file'], '/var/run/secrets/kubernetes.io/serviceaccount/token')
                    self.assertIn('/proxy/metrics', scrape['relabel_configs'][-1]['replacement'])
                # The only rendered Secret holds public scrape configuration, no bearer value.
                for secret in of(docs, 'Secret'):
                    self.assertEqual(set(secret.get('data', {})), {'additional-scrape-configs.yaml'})
                    text = base64.b64decode(secret['data']['additional-scrape-configs.yaml']).decode()
                    self.assertNotIn('insecure_skip_verify: true', text)
                    self.assertNotIn('bearer_token:', text)
        self.assertFalse(of(all_docs, 'Job'))
        self.assertFalse(of(all_docs, 'Alertmanager'))

if __name__ == '__main__':
    unittest.main()
