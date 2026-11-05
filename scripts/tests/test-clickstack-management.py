#!/usr/bin/env python3
"""Synthetic CI bootstrap contracts. No cloud, Secret API, or real credentials."""
import base64
import contextlib
import copy
import importlib.util
import io
import json
import os
from pathlib import Path
import ssl
import subprocess
import sys
import tempfile
import threading
import unittest
from unittest.mock import patch, MagicMock
from http.server import HTTPServer, BaseHTTPRequestHandler

import yaml

ROOT = Path(__file__).resolve().parents[2]
sys.path.insert(0, str(ROOT / 'scripts'))
import clickstack_bootstrap as b


def load(name, path):
    spec = importlib.util.spec_from_file_location(name, path)
    value = importlib.util.module_from_spec(spec);spec.loader.exec_module(value)
    return value


m = load('management', ROOT / 'scripts/clickstack-management.py')
smoke = load('smoke_test', ROOT / 'scripts/observability-smoke.py')
ENV = {'GITHUB_REPOSITORY': b.REPO, 'GITHUB_REF': 'refs/heads/main', 'GITHUB_EVENT_NAME': 'workflow_dispatch',
       'GITHUB_SHA': 'a' * 40, 'EXPECTED_SHA': 'a' * 40, 'GITHUB_RUN_ID': '123', 'GITHUB_RUN_ATTEMPT': '1',
       'AWS_ACCOUNT_ID': b.ACCOUNT, 'AWS_REGION': b.REGION, 'AWS_ROLE_ARN': b.ROLE,
       'CLICKSTACK_ACTION': 'deploy', 'CONFIRM_EXECUTION': 'true', 'GH_TOKEN': 'synthetic-never-print'}
TARGETS = {k: {'name': 'charlie-dev-' + k, 'endpoint': f'https://{k}.eks.amazonaws.com',
              'region': b.REGION, 'vpc_id': 'vpc-0123456789abcdef0'} for k in ('management', 'workload')}


class Store:
    def __init__(self):
        self.objects, self.writes = {}, []

    def get(self, name):
        return copy.deepcopy(self.objects.get(name))

    def create(self, obj):
        name = obj['metadata']['name']
        if name in self.objects:
            raise b.Rejected('create race')
        self.writes.append(name);self.objects[name] = copy.deepcopy(obj)

    def bundle(self):
        return b.Bundle(self.get, self.create, ROOT).build()


def entry(run='123', attempt='1'):
    return {'principalArn': b.ROLE, 'type': 'STANDARD', 'kubernetesGroups': [],
            'tags': {b.OWNER_KEY: b.OWNER, b.RUN_KEY: run, b.ATTEMPT_KEY: attempt, b.REPO_KEY: b.REPO.replace('/', '.')}}


class ContextTests(unittest.TestCase):
    def test_explicit_context_and_confirmation(self):
        self.assertEqual(b.context(ENV), ('deploy', 'a' * 40, '123'))
        for key, value in [('GITHUB_REF', 'refs/heads/feature'), ('GITHUB_EVENT_NAME', 'push'),
                           ('GITHUB_REPOSITORY', 'other/repo'), ('EXPECTED_SHA', 'b' * 40),
                           ('AWS_ACCOUNT_ID', '000000000000'), ('AWS_REGION', 'us-east-1'),
                           ('AWS_ROLE_ARN', 'other'), ('CONFIRM_EXECUTION', 'false'),
                           ('GITHUB_RUN_ID', '../123'), ('GITHUB_RUN_ATTEMPT', 'bad')]:
            with self.subTest(key=key), self.assertRaises(b.Rejected):
                b.context(dict(ENV, **{key: value}))
        self.assertEqual(b.context(dict(ENV, CLICKSTACK_ACTION='preflight', CONFIRM_EXECUTION='false'))[0], 'preflight')
        with self.assertRaises(b.Rejected):
            b.context(dict(ENV, CLICKSTACK_ACTION='cleanup-access'))

    def test_context_cli_fails_without_cloud_calls_or_payload_output(self):
        result = subprocess.run([sys.executable, str(ROOT / 'scripts/clickstack-management.py'), '--check-context'],
            env=dict(os.environ, **dict(ENV, GITHUB_REF='refs/heads/feature')), text=True, capture_output=True)
        self.assertNotEqual(result.returncode, 0)
        self.assertNotIn('synthetic-never-print', result.stdout + result.stderr)

    def test_preflight_never_creates_mapping_or_calls_kubernetes(self):
        with contextlib.ExitStack() as stack, tempfile.TemporaryDirectory() as directory:
            work = Path(directory) / 'clickstack-123.preflight';work.mkdir(mode=0o700)
            env = dict(ENV, CLICKSTACK_ACTION='preflight', CONFIRM_EXECUTION='false', RUNNER_TEMP=directory, CLICKSTACK_WORK_DIR=str(work))
            stack.enter_context(patch.dict(os.environ, env, clear=True))
            stack.enter_context(patch.object(sys, 'argv', ['clickstack-management.py']))
            aws = MagicMock();aws.entry.return_value = None
            stack.enter_context(patch.object(m, 'AWS', return_value=aws))
            stack.enter_context(patch.object(m, 'aws_preflight', return_value=TARGETS))
            stack.enter_context(patch.object(m, 'render', return_value=[]))
            stack.enter_context(patch.object(m, 'operator_crds', return_value=['crd'] * 9))
            kube = stack.enter_context(patch.object(m, 'Kube'))
            with contextlib.redirect_stdout(io.StringIO()):
                m.main()
            aws.create_access.assert_not_called();aws.cleanup.assert_not_called();kube.assert_not_called()

    def test_raw_cli_failure_never_prints_credentials(self):
        result = subprocess.CompletedProcess([], 1, 'synthetic-password', 'Unauthorized synthetic-password')
        with patch.object(m.subprocess, 'run', return_value=result):
            with self.assertRaises(m.CommandFailed) as caught:
                m.Commands().run(['kubectl', 'get', 'nodes'])
        self.assertTrue(caught.exception.authorization_denied)
        self.assertNotIn('synthetic-password', str(caught.exception))


class BundleTests(unittest.TestCase):
    def setUp(self):
        self.store = Store()

    def test_canonical_first_create_only_and_idempotent(self):
        values = self.store.bundle()
        original = copy.deepcopy(values)
        self.assertEqual(set(values), set(b.SECRET_NAMES))
        self.assertEqual(self.store.writes[:4], ['clickstack-bootstrap-ca', 'clickstack-clickhouse-auth', 'clickstack-mongodb-auth', 'clickstack-collector-auth'])
        self.assertEqual(self.store.bundle(), original)
        self.assertEqual(len(self.store.writes), 11)
        self.assertEqual(values['clickstack-clickhouse-auth']['ingest-password'], values['clickstack-collector-auth']['CLICKHOUSE_PASSWORD'])
        self.assertEqual(values['observability-client-auth']['OTLP_AUTH_TOKEN'], values['clickstack-collector-auth']['OTLP_AUTH_TOKEN'])
        self.assertEqual(len(base64.b64decode(values['clickstack-runtime']['TOKEN_ENCRYPTION_KEY'])), 32)
        self.assertNotIn('FIXTURE_READ_PASSWORD', values['clickstack-ui-config']['connections.json'])

    def test_partial_bundle_resumes_using_canonical_credentials(self):
        real_create = self.store.create
        def interrupted(obj):
            if len(self.store.writes) == 4:
                raise b.Rejected('synthetic interruption')
            real_create(obj)
        with self.assertRaises(b.Rejected):
            b.Bundle(self.store.get, interrupted, ROOT).build()
        canonical = copy.deepcopy(self.store.objects)
        self.store.bundle()
        for name, obj in canonical.items():
            self.assertEqual(self.store.objects[name], obj)

    def test_foreign_or_mixed_bundle_fails_before_any_new_write(self):
        self.store.bundle()
        for field, value in ((b.OWNER_KEY, 'other-owner'), (b.BUNDLE_KEY, '00000000-0000-0000-0000-000000000000')):
            snapshot = copy.deepcopy(self.store.objects)
            self.store.objects['clickstack-demo-login']['metadata']['labels'][field] = value
            self.store.writes.clear()
            with self.assertRaises(b.Rejected):
                self.store.bundle()
            self.assertEqual(self.store.writes, [])
            self.store.objects = snapshot

    def test_missing_canonical_does_not_rotate_live_dependents(self):
        self.store.bundle();del self.store.objects['clickstack-clickhouse-auth'];self.store.writes.clear()
        with self.assertRaises(b.Rejected):
            self.store.bundle()
        self.assertEqual(self.store.writes, [])

    def test_invalid_derived_password_and_key_rejected(self):
        self.store.bundle()
        obj = self.store.objects['observability-client-auth']
        obj['data']['OTLP_AUTH_TOKEN'] = base64.b64encode(b'synthetic-wrong-value').decode()
        with self.assertRaises(b.Rejected):
            self.store.bundle()

    def test_tls_private_key_and_san_validation(self):
        ca = b.certificate_pair()
        data = b.encode_pair(b.certificate_pair(ca, 'central.observability.svc'))
        b.validate_pair(data, ca, 'central.observability.svc')
        with self.assertRaises(b.Rejected):
            b.validate_pair(data, ca, 'wrong.observability.svc')
        data['tls.key'] = b.encode_pair(b.certificate_pair(ca, 'central.observability.svc'))['tls.key']
        with self.assertRaises(b.Rejected):
            b.validate_pair(data, ca, 'central.observability.svc')


class AccessTests(unittest.TestCase):
    def test_existing_entry_is_never_expanded(self):
        aws = m.AWS(MagicMock());aws.entry = MagicMock(return_value=entry());aws.get = MagicMock()
        with self.assertRaises(b.Rejected):
            aws.create_access('123')
        aws.get.assert_not_called()

    def test_cleanup_exact_owner_attempt_and_policy(self):
        aws = m.AWS(MagicMock());aws.get = MagicMock(return_value={'associatedAccessPolicies': [{'policyArn': b.POLICY, 'accessScope': {'type': 'cluster', 'namespaces': []}}]})
        aws.entry = MagicMock(side_effect=[entry(), None])
        with patch.dict(os.environ, ENV):
            aws.cleanup('123', finalizer=True)
        self.assertEqual(aws.get.call_args_list[-1].args[1], 'delete-access-entry')

    def test_other_owner_old_attempt_and_extra_policy_preserved(self):
        for obj in (entry('other'), entry('123', '2')):
            aws = m.AWS(MagicMock());aws.entry = MagicMock(return_value=obj);aws.get = MagicMock()
            with patch.dict(os.environ, ENV):
                aws.cleanup('123', finalizer=True)
            aws.get.assert_not_called()
        aws = m.AWS(MagicMock());aws.entry = MagicMock(return_value=entry());aws.get = MagicMock(return_value={'associatedAccessPolicies': [{'policyArn': 'unapproved', 'accessScope': {'type': 'cluster'}}]})
        with self.assertRaises(b.Rejected):
            aws.cleanup('123')
        self.assertEqual(len(aws.get.call_args_list), 1)

    def test_contract_failure_is_not_retried(self):
        probe = MagicMock(side_effect=b.Rejected('bad revision'))
        with self.assertRaises(b.Rejected):
            m.wait(probe, 30, 'timeout')
        self.assertEqual(probe.call_count, 1)

    def test_authorization_recheck_distinguishes_network_failure(self):
        kube = m.Kube(MagicMock(), Path('/synthetic/config'))
        kube.run = MagicMock(side_effect=m.CommandFailed('denied', authorization_denied=True))
        self.assertTrue(kube.access_revoked())
        kube.run = MagicMock(side_effect=m.CommandFailed('network'))
        with self.assertRaises(m.CommandFailed):
            kube.access_revoked()


class RenderTests(unittest.TestCase):
    def test_management_only_and_fixed_sources(self):
        docs = m.render(m.Commands(), TARGETS, 'a' * 40)
        self.assertEqual(len(docs), 5)
        self.assertEqual(next(x for x in docs if x['kind'] == 'AppProject')['spec']['destinations'], [{'server': TARGETS['management']['endpoint'], 'namespace': 'observability'}])

    def test_target_outside_destination_is_rejected(self):
        with tempfile.TemporaryDirectory() as directory:
            p = Path(directory) / 'values.json'
            p.write_text(json.dumps({'targets': TARGETS, 'observability': {'enabled': True, 'agentTargets': ['workload'], 'gatewayTargets': ['management'], 'destinationTargets': ['management']}}))
            result = subprocess.run(['helm', 'template', 'charlie', str(ROOT / 'helm/gitops'), '--kube-version', '1.35.0', '-f', str(p)], capture_output=True)
        self.assertNotEqual(result.returncode, 0)

    def test_sync_binds_each_source_and_stops_failed_operation(self):
        docs = m.render(m.Commands(), TARGETS, 'a' * 40)
        doc = next(d for d in docs if d['metadata']['name'] == b.APPS[0])
        app = copy.deepcopy(doc);app['metadata']['labels'] = {b.OWNER_KEY: b.OWNER}
        kube = MagicMock();kube.get.side_effect = lambda *a: app
        def patch_operation(*args, obj):
            app['status'] = {'operationState': {'phase': 'Succeeded', 'operation': obj['operation'],
                'syncResult': {'revisions': ['1.1.0', 'a' * 40]}}}
        kube.run.side_effect = patch_operation
        m.sync(kube, b.APPS[0], expected=doc)
        self.assertEqual(kube.run.call_args.kwargs['obj']['operation']['sync']['revisions'], ['1.1.0', 'a' * 40])
        app['spec']['sources'][1]['targetRevision'] = 'b' * 40
        with self.assertRaises(b.Rejected):
            m.sync(kube, b.APPS[0], expected=doc)

    def test_restart_allowlist_protects_prometheus(self):
        with self.assertRaises(b.Rejected):
            m.Kube(MagicMock(), Path('synthetic')).restart('statefulset', 'prometheus-monitoring-prometheus')


class FlowTests(unittest.TestCase):
    def run_flow(self, fail_at=None, cleanup_error=False):
        with tempfile.TemporaryDirectory() as directory, contextlib.ExitStack() as stack:
            directory = Path(directory)
            work = directory / 'clickstack-123.synthetic';work.mkdir(mode=0o700)
            (work / 'kubeconfig').write_text('{}')
            env = dict(ENV, RUNNER_TEMP=str(directory), CLICKSTACK_WORK_DIR=str(work))
            stack.enter_context(patch.dict(os.environ, env, clear=True))
            stack.enter_context(patch.object(sys, 'argv', ['clickstack-management.py']))
            aws = MagicMock();aws.entry.return_value = None
            if cleanup_error:
                aws.cleanup.side_effect = b.Rejected('synthetic cleanup failure')
            stack.enter_context(patch.object(m, 'AWS', return_value=aws))
            commands = MagicMock();stack.enter_context(patch.object(m, 'Commands', return_value=commands))
            stack.enter_context(patch.object(m, 'aws_preflight', return_value=TARGETS))
            docs = [{'kind': 'Application', 'metadata': {'name': name}} for name in b.APPS]
            stack.enter_context(patch.object(m, 'render', return_value=docs))
            stack.enter_context(patch.object(m, 'operator_crds', return_value=['crd-' + str(i) for i in range(9)]))
            stack.enter_context(patch.object(m, 'collision_checks'))
            kube = MagicMock();kube.health.return_value = 3.11;kube.ready_workloads.return_value = True
            def get(kind, *args, **kwargs):
                if kind == 'crd' and args[0] == 'applications.argoproj.io':
                    return {'spec': {'versions': [{'name': 'v1alpha1', 'schema': {'openAPIV3Schema': {'properties': {'operation': {'properties': {
                        'info': {}, 'sync': {'properties': {'revision': {}, 'revisions': {}, 'resources': {}}}}}}}}}]}}
                if kind == 'crd':
                    return {'status': {'conditions': [{'type': 'Established', 'status': 'True'}]}}
                if kind == 'deployments':
                    return {'items': [{'metadata': {'name': 'operator-' + str(i), 'labels': {'app.kubernetes.io/instance': b.APPS[0]}}} for i in range(2)]}
                return {'items': ['synthetic']}
            kube.get.side_effect = get
            stack.enter_context(patch.object(m, 'Kube', return_value=kube))
            processes = MagicMock();processes.session = 'synthetic-owned-session'
            stack.enter_context(patch.object(m, 'Processes', return_value=processes))
            stack.enter_context(patch.object(m, 'listener', return_value=True))
            bundle = MagicMock();bundle.build.return_value = {'synthetic': 'not-operational'}
            stack.enter_context(patch.object(m, 'Bundle', return_value=bundle))
            runtime = stack.enter_context(patch.object(m, 'verify_runtime', return_value=['stable-source']))
            def synced(kube, app, **kwargs):
                if fail_at == app:
                    raise b.Rejected('synthetic stage failure')
            synced_mock = stack.enter_context(patch.object(m, 'sync', side_effect=synced))
            with contextlib.redirect_stdout(io.StringIO()), contextlib.redirect_stderr(io.StringIO()):
                if fail_at or cleanup_error:
                    with self.assertRaises(b.Rejected):
                        m.main()
                else:
                    m.main()
            self.assertEqual(aws.cleanup.call_count, 1)
            processes.close.assert_called_once()
            return [call.args[1] for call in synced_mock.call_args_list], kube.restart.call_count, runtime.call_count

    def test_success_orders_sync_restart_and_cleanup(self):
        apps, restarts, runtime = self.run_flow()
        self.assertEqual(apps, [b.APPS[0], b.APPS[1], b.APPS[1], b.APPS[2], b.APPS[3]])
        self.assertEqual(restarts, 3);self.assertEqual(runtime, 2)

    def test_failed_operator_never_syncs_database_or_later_apps(self):
        apps, restarts, runtime = self.run_flow(fail_at=b.APPS[0])
        self.assertEqual(apps, [b.APPS[0]])
        self.assertEqual(restarts, 0);self.assertEqual(runtime, 0)

    def test_failed_database_never_syncs_gateway_or_agent(self):
        apps, restarts, runtime = self.run_flow(fail_at=b.APPS[1])
        self.assertEqual(apps, [b.APPS[0], b.APPS[1]])
        self.assertEqual(restarts, 0);self.assertEqual(runtime, 0)

    def test_access_cleanup_failure_still_closes_tunnel_and_fails(self):
        self.run_flow(cleanup_error=True)


class WorkflowTests(unittest.TestCase):
    def test_only_manual_main_can_get_oidc_and_cleanup_always(self):
        workflow = yaml.safe_load((ROOT / '.github/workflows/clickstack-management.yml').read_text())
        job = workflow['jobs']['bootstrap'];steps = job['steps']
        self.assertEqual(job['if'], "github.event_name == 'workflow_dispatch' && github.ref == 'refs/heads/main'")
        self.assertEqual(job['concurrency'], {'group': 'charlie-terraform-apply', 'cancel-in-progress': False})
        guard = next(i for i,s in enumerate(steps) if s.get('name') == 'Reject unapproved context before AWS authentication')
        auth = next(i for i,s in enumerate(steps) if s.get('uses', '').startswith('aws-actions/configure-aws-credentials'))
        self.assertLess(guard, auth)
        self.assertEqual(workflow['jobs']['bootstrap-contracts'].get('permissions', {}).get('id-token'), None)
        self.assertIn('always()', next(s for s in steps if s.get('name') == 'Always finalize owned access')['if'])
        self.assertNotIn('upload-artifact', json.dumps(workflow))


class InstallerTests(unittest.TestCase):
    def test_corrupt_tool_hash_stops_before_extract_or_execute(self):
        installer = load('installer', ROOT / 'scripts/install-clickstack-tools.py')
        with tempfile.TemporaryDirectory() as directory, patch.dict(os.environ, {'RUNNER_TEMP': directory, 'GITHUB_RUN_ID': '123'}), \
                patch.object(installer.sys, 'platform', 'linux'), patch.object(installer.platform, 'machine', return_value='x86_64'), \
                patch.object(installer.urllib.request, 'urlopen', return_value=io.BytesIO(b'synthetic-corrupt-release')), \
                patch.object(installer.subprocess, 'run') as execute:
            with self.assertRaises(ValueError):
                installer.main()
            execute.assert_not_called()


class LocalTLS(unittest.TestCase):
    def test_loopback_forward_still_verifies_service_dns_and_rejects_redirect(self):
        ca = b.certificate_pair();leaf = b.encode_pair(b.certificate_pair(ca, 'collector.observability.svc'))
        captured = []
        class Handler(BaseHTTPRequestHandler):
            redirect = False
            def log_message(self, *args):
                pass
            def do_POST(self):
                self.rfile.read(int(self.headers['Content-Length']))
                captured.append(self.headers.get('Authorization'))
                self.send_response(302 if self.redirect else 200)
                if self.redirect:
                    self.send_header('Location', 'https://unapproved.example/')
                self.end_headers();self.wfile.write(b'{}')
        with tempfile.TemporaryDirectory() as directory:
            directory = Path(directory)
            for key, value in dict(leaf, **{'ca.crt': b.encode_pair(ca, ca=True)['ca.crt']}).items():
                (directory / key).write_text(value);(directory / key).chmod(0o600)
            server = HTTPServer(('127.0.0.1', 0), Handler)
            context = ssl.SSLContext(ssl.PROTOCOL_TLS_SERVER);context.load_cert_chain(directory/'tls.crt', directory/'tls.key')
            server.socket = context.wrap_socket(server.socket, server_side=True)
            thread = threading.Thread(target=server.serve_forever, daemon=True);thread.start()
            try:
                endpoint = f'https://collector.observability.svc:{server.server_port}'
                smoke.send(endpoint, str(directory/'ca.crt'), 'synthetic-only', {}, {}, connect_address='127.0.0.1')
                self.assertEqual(len(captured), 2)
                with self.assertRaises(Exception):
                    smoke.send(endpoint.replace('collector.', 'wrong.'), str(directory/'ca.crt'), 'synthetic-only', {}, {}, connect_address='127.0.0.1')
                Handler.redirect = True
                with self.assertRaises(Exception):
                    smoke.send(endpoint, str(directory/'ca.crt'), 'synthetic-only', {}, {}, connect_address='127.0.0.1')
                self.assertEqual(len(captured), 3)  # No redirected request was made.
                with self.assertRaises(ValueError):
                    smoke.LoopbackHTTPS(ssl.create_default_context(), '10.0.0.1')
            finally:
                server.shutdown();server.server_close();thread.join()


if __name__ == '__main__':
    unittest.main()
