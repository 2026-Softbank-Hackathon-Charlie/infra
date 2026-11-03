#!/usr/bin/env python3
"""Check mixed-auth registration without credentials or cluster access."""
import base64
from copy import deepcopy
import importlib.util
import io
import json
from pathlib import Path
from types import SimpleNamespace
import unittest
from unittest.mock import patch

ROOT = Path(__file__).resolve().parents[2]
CA = base64.b64encode(b'-----BEGIN CERTIFICATE-----\ntest\n-----END CERTIFICATE-----\n').decode()
METADATA = {'name': 'charlie-dev-gcp-workload', 'endpoint': '34.64.99.246',
            'status': 'RUNNING', 'masterAuth': {'clusterCaCertificate': CA}}


def existing_config():
    return {'apiVersion': 'v1', 'kind': 'Config', 'current-context': 'management',
            'preferences': {'colors': True},
            'clusters': [{'name': 'management', 'cluster': {'server': 'https://management.example',
                                                          'certificate-authority-data': CA}}],
            'contexts': [{'name': 'management', 'context': {'cluster': 'management', 'user': 'team-oidc'}}],
            'users': [{'name': 'team-oidc', 'user': {'auth-provider': {'name': 'oidc', 'config': {
                'client-secret': 'fixture-client-secret', 'client-id': 'fixture-client'}}}}]}


class RegistrationTests(unittest.TestCase):
    def module(self):
        path = ROOT / 'scripts/register-headlamp-gke.py'
        self.assertTrue(path.exists(), 'GKE registration helper is not implemented')
        spec = importlib.util.spec_from_file_location('register_headlamp_gke', path)
        module = importlib.util.module_from_spec(spec)
        spec.loader.exec_module(module)
        return module

    def test_preserves_aws_identity_and_adds_credentialless_context(self):
        module = self.module()
        original = existing_config()
        result = module.merge_cluster(original, METADATA)
        self.assertEqual(original, existing_config())
        for key in ('clusters', 'contexts', 'users'):
            self.assertEqual(result[key][0], original[key][0])
        self.assertEqual(result['current-context'], original['current-context'])
        self.assertEqual(result['preferences'], original['preferences'])
        self.assertEqual(result['users'][-1]['user'], {})
        self.assertEqual(result['contexts'][-1]['context']['cluster'], METADATA['name'])
        self.assertEqual(result['clusters'][-1]['cluster']['server'], 'https://34.64.99.246')
        self.assertNotIn('insecure-skip-tls-verify', result['clusters'][-1]['cluster'])

    def test_repeated_registration_is_idempotent(self):
        module = self.module()
        result = module.merge_cluster(existing_config(), METADATA)
        self.assertEqual(module.merge_cluster(result, METADATA), result)

    def test_refuses_collisions_and_credentials_in_gke_context(self):
        module = self.module()
        added = module.merge_cluster(existing_config(), METADATA)
        for kind, field, value in [('clusters', 'cluster', {'server': 'https://other.example'}),
                                   ('contexts', 'context', {'cluster': 'management', 'user': 'team-oidc'}),
                                   ('users', 'user', {'token': 'fixture-personal-token'})]:
            broken = deepcopy(added)
            broken[kind][-1][field] = value
            with self.assertRaises(ValueError):
                module.merge_cluster(broken, METADATA)

    def test_rejects_unsafe_or_incomplete_public_metadata(self):
        module = self.module()
        invalid = [{**METADATA, 'endpoint': 'http://34.64.99.246'},
                   {**METADATA, 'endpoint': '127.0.0.1'},
                   {**METADATA, 'name': ''},
                   {**METADATA, 'masterAuth': {'clusterCaCertificate': 'not-base64'}},
                   {**METADATA, 'status': 'PROVISIONING'}]
        for metadata in invalid:
            with self.subTest(metadata=metadata), self.assertRaises(ValueError):
                module.merge_cluster(existing_config(), metadata)

    def test_preview_never_calls_kubectl_or_reads_secret(self):
        module = self.module()
        with patch.object(module.Path, 'read_text', return_value=json.dumps(METADATA)), \
                patch.object(module.subprocess, 'run') as run, patch('sys.stdout', new_callable=io.StringIO) as out:
            module.main(['--metadata', 'public.json'])
        run.assert_not_called()
        self.assertIn(METADATA['name'], out.getvalue())
        self.assertNotIn('fixture-client-secret', out.getvalue())

    def test_install_requires_explicit_kubeconfig_and_context_before_access(self):
        module = self.module()
        with patch.object(module.subprocess, 'run') as run, patch('sys.stderr', new_callable=io.StringIO):
            with self.assertRaises(SystemExit):
                module.main(['--metadata', 'public.json', '--install-secret'])
        run.assert_not_called()

    def test_update_keeps_secret_fields_and_uses_stdin_then_restarts(self):
        module = self.module()
        secret = {'apiVersion': 'v1', 'kind': 'Secret', 'type': 'Opaque',
                  'metadata': {'name': 'headlamp-clusters', 'namespace': 'headlamp',
                               'resourceVersion': '42', 'labels': {'preserve': 'yes'}},
                  'data': {'config': base64.b64encode(json.dumps(existing_config()).encode()).decode(),
                           'unrelated': 'cHJlc2VydmU='}}
        responses = [SimpleNamespace(returncode=0, stdout=json.dumps(secret)),
                     SimpleNamespace(returncode=0, stdout=''), SimpleNamespace(returncode=0, stdout='')]
        with patch.object(module.subprocess, 'run', side_effect=responses) as run, \
                patch('sys.stdout', new_callable=io.StringIO) as out:
            module.install(METADATA, Path('management.kubeconfig'), 'charlie-dev-management')
        calls = run.call_args_list
        self.assertEqual(len(calls), 3)
        self.assertIn('replace', calls[1].args[0])
        self.assertEqual(calls[1].args[0][-2:], ['-f', '-'])
        payload = json.loads(calls[1].kwargs['input'])
        self.assertEqual(payload['metadata'], secret['metadata'])
        self.assertEqual(payload['data']['unrelated'], secret['data']['unrelated'])
        self.assertNotIn('fixture-client-secret', ' '.join(calls[1].args[0]))
        self.assertNotIn('fixture-client-secret', out.getvalue())
        self.assertIn('restart', calls[2].args[0])
        updated = json.loads(base64.b64decode(payload['data']['config']))
        unchanged = deepcopy(payload)
        unchanged['data']['config'] = base64.b64encode(json.dumps(updated).encode()).decode()
        with patch.object(module.subprocess, 'run', return_value=SimpleNamespace(returncode=0, stdout=json.dumps(unchanged))) as run:
            module.install(METADATA, Path('management.kubeconfig'), 'charlie-dev-management')
        self.assertEqual(run.call_count, 1)

    def test_failed_update_never_restarts_or_prints_secret(self):
        module = self.module()
        secret = {'apiVersion': 'v1', 'kind': 'Secret',
                  'metadata': {'name': 'headlamp-clusters', 'namespace': 'headlamp', 'resourceVersion': '42'},
                  'data': {'config': base64.b64encode(json.dumps(existing_config()).encode()).decode()}}
        responses = [SimpleNamespace(returncode=0, stdout=json.dumps(secret)),
                     SimpleNamespace(returncode=1, stdout='fixture-client-secret', stderr='fixture-client-secret')]
        with patch.object(module.subprocess, 'run', side_effect=responses) as run, \
                patch('sys.stdout', new_callable=io.StringIO) as out:
            with self.assertRaises(SystemExit) as error:
                module.install(METADATA, Path('management.kubeconfig'), 'charlie-dev-management')
        self.assertEqual(run.call_count, 2)
        self.assertNotIn('fixture-client-secret', str(error.exception) + out.getvalue())


if __name__ == '__main__':
    unittest.main()
