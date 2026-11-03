#!/usr/bin/env python3
"""Exercise Google Headlamp kubeconfig migration without cloud access."""
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
SECRET = 'headlamp-gke-google-clusters'
CA = base64.b64encode(b'-----BEGIN CERTIFICATE-----\ntest\n-----END CERTIFICATE-----\n').decode()
METADATA = {'name': 'charlie-dev-gcp-workload', 'endpoint': '34.64.99.246',
            'status': 'RUNNING', 'masterAuth': {'clusterCaCertificate': CA}}


def old_config():
    return {'apiVersion': 'v1', 'kind': 'Config', 'current-context': 'gke',
            'preferences': {'colors': True},
            'clusters': [{'name': 'gke', 'cluster': {'server': 'https://34.64.99.246',
                                                  'certificate-authority-data': CA}}],
            'contexts': [{'name': 'gke', 'context': {'cluster': 'gke', 'user': 'google'}}],
            'users': [{'name': 'google', 'user': {}}]}


def secret_for(config):
    return {'apiVersion': 'v1', 'kind': 'Secret', 'type': 'Opaque',
            'metadata': {'name': SECRET, 'namespace': 'headlamp', 'resourceVersion': '42',
                         'labels': {'preserve': 'yes'}},
            'data': {'config': base64.b64encode(json.dumps(config).encode()).decode(),
                     'unrelated': 'cHJlc2VydmU='}}


class GoogleConfigTests(unittest.TestCase):
    def module(self):
        path = ROOT / 'scripts/prepare-headlamp-google.py'
        self.assertTrue(path.exists(), 'Google kubeconfig helper is not implemented')
        spec = importlib.util.spec_from_file_location('prepare_headlamp_google', path)
        module = importlib.util.module_from_spec(spec)
        spec.loader.exec_module(module)
        return module

    def test_new_config_uses_actual_name_and_no_credentials(self):
        module = self.module()
        result = module.prepare_config(None, METADATA)
        name = METADATA['name']
        self.assertEqual(result['current-context'], name)
        self.assertEqual(result['clusters'], [{'name': name, 'cluster': old_config()['clusters'][0]['cluster']}])
        self.assertEqual(result['contexts'], [{'name': name, 'context': {'cluster': name, 'user': 'google'}}])
        self.assertEqual(result['users'], [{'name': 'google', 'user': {}}])
        self.assertEqual(module.prepare_config(result, METADATA), result)

    def test_preserves_old_alias_current_context_and_preferences(self):
        module = self.module()
        original = old_config()
        result = module.prepare_config(original, METADATA)
        self.assertEqual(original, old_config())
        self.assertEqual(result['current-context'], 'gke')
        self.assertEqual(result['preferences'], original['preferences'])
        for kind in ('clusters', 'contexts', 'users'):
            self.assertEqual(result[kind][0], original[kind][0])
        self.assertEqual([entry['name'] for entry in result['clusters']], ['gke', METADATA['name']])
        self.assertEqual([entry['name'] for entry in result['contexts']], ['gke', METADATA['name']])
        self.assertEqual(module.prepare_config(result, METADATA), result)

    def test_conflicts_and_authentication_fail_closed(self):
        module = self.module()
        variants = []
        for key in ('token', 'tokenFile', 'auth-provider', 'exec', 'client-certificate-data'):
            config = old_config()
            config['users'][0]['user'][key] = 'credential-fixture'
            variants.append(config)
        for kind in ('clusters', 'contexts', 'users'):
            config = old_config()
            config[kind].append(deepcopy(config[kind][0]))
            variants.append(config)
            config = old_config()
            config[kind][0]['name'] = 'management'
            variants.append(config)
        for field, value in [('server', 'https://other.example'),
                             ('certificate-authority-data', 'b3RoZXI='),
                             ('insecure-skip-tls-verify', True), ('proxy-url', 'http://proxy.example')]:
            config = old_config()
            config['clusters'][0]['cluster'][field] = value
            variants.append(config)
        config = old_config()
        config['contexts'][0]['context']['user'] = 'aws-oidc'
        variants.append(config)
        config = old_config()
        config['current-context'] = 'missing'
        variants.append(config)
        config = old_config()
        config['contexts'] = []
        variants.append(config)
        for config in variants:
            with self.subTest(config=config), self.assertRaises(ValueError):
                module.prepare_config(config, METADATA)

    def test_public_metadata_is_validated_by_existing_registration_contract(self):
        module = self.module()
        for invalid in ({**METADATA, 'endpoint': '127.0.0.1'},
                        {**METADATA, 'status': 'PROVISIONING'},
                        {**METADATA, 'name': '../management'},
                        {**METADATA, 'masterAuth': {'clusterCaCertificate': 'invalid'}}):
            with self.subTest(metadata=invalid), self.assertRaises(ValueError):
                module.prepare_config(None, invalid)

    def test_offline_preview_emits_names_only_and_never_calls_kubectl(self):
        module = self.module()
        with patch.object(module.Path, 'read_text', return_value=json.dumps(METADATA)), \
                patch.object(module.subprocess, 'run') as run, patch('sys.stdout', new_callable=io.StringIO) as out:
            module.main(['--metadata', 'public.json'])
        run.assert_not_called()
        self.assertIn(SECRET, out.getvalue())
        self.assertIn(METADATA['name'], out.getvalue())
        self.assertNotIn(METADATA['endpoint'], out.getvalue())
        self.assertNotIn(CA, out.getvalue())

    def test_install_requires_explicit_target_before_reading_inputs(self):
        module = self.module()
        for flags in ([], ['--context', 'management'], ['--kubeconfig', 'access.yaml']):
            with patch.object(module.Path, 'read_text') as read, patch.object(module.subprocess, 'run') as run, \
                    patch('sys.stderr', new_callable=io.StringIO), self.assertRaises(SystemExit):
                module.main(['--metadata', 'public.json', '--install-secret', *flags])
            run.assert_not_called()
            read.assert_not_called()

    def test_absent_secret_is_created_via_stdin_without_restart(self):
        module = self.module()
        responses = [SimpleNamespace(returncode=0, stdout=''), SimpleNamespace(returncode=0, stdout='created')]
        with patch.object(module.subprocess, 'run', side_effect=responses) as run, \
                patch('sys.stdout', new_callable=io.StringIO):
            module.install(METADATA, Path('access.yaml'), 'management')
        self.assertEqual(run.call_count, 2)
        first, second = run.call_args_list
        self.assertIn('--ignore-not-found', first.args[0])
        self.assertIn(SECRET, first.args[0])
        self.assertNotIn('headlamp-clusters', first.args[0])
        self.assertIn('create', second.args[0])
        self.assertEqual(second.args[0][-2:], ['-f', '-'])
        payload = json.loads(second.kwargs['input'])
        self.assertEqual(payload['metadata'], {'name': SECRET, 'namespace': 'headlamp'})
        self.assertEqual(json.loads(base64.b64decode(payload['data']['config'])), module.prepare_config(None, METADATA))

    def test_existing_secret_replaced_with_resource_version_and_metadata_preserved(self):
        module = self.module()
        secret = secret_for(old_config())
        responses = [SimpleNamespace(returncode=0, stdout=json.dumps(secret)),
                     SimpleNamespace(returncode=0, stdout='replaced')]
        with patch.object(module.subprocess, 'run', side_effect=responses) as run, \
                patch('sys.stdout', new_callable=io.StringIO):
            module.install(METADATA, Path('access.yaml'), 'management')
        self.assertEqual(run.call_count, 2)
        replace = run.call_args_list[1]
        self.assertIn('replace', replace.args[0])
        payload = json.loads(replace.kwargs['input'])
        self.assertEqual(payload['metadata'], secret['metadata'])
        self.assertEqual(payload['data']['unrelated'], secret['data']['unrelated'])
        self.assertEqual(json.loads(base64.b64decode(payload['data']['config']))['current-context'], 'gke')
        self.assertFalse(any('restart' in call.args[0] for call in run.call_args_list))

    def test_idempotent_install_performs_no_write(self):
        module = self.module()
        secret = secret_for(module.prepare_config(old_config(), METADATA))
        with patch.object(module.subprocess, 'run', return_value=SimpleNamespace(returncode=0, stdout=json.dumps(secret))) as run, \
                patch('sys.stdout', new_callable=io.StringIO):
            module.install(METADATA, Path('access.yaml'), 'management')
        self.assertEqual(run.call_count, 1)

    def test_invalid_secret_and_conflicting_target_never_write(self):
        module = self.module()
        variants = []
        for key, value in [('kind', 'ConfigMap'), ('type', 'kubernetes.io/tls'), ('immutable', True)]:
            secret = secret_for(old_config())
            secret[key] = value
            variants.append(secret)
        for key, value in [('name', 'headlamp-clusters'), ('namespace', 'default'), ('resourceVersion', '')]:
            secret = secret_for(old_config())
            secret['metadata'][key] = value
            variants.append(secret)
        for encoded in ('not-base64', base64.b64encode(b'null').decode(),
                        base64.b64encode(b'[]').decode(), base64.b64encode(b'').decode()):
            secret = secret_for(old_config())
            secret['data']['config'] = encoded
            variants.append(secret)
        secret = secret_for(old_config())
        secret['stringData'] = {'config': 'unexpected override'}
        variants.append(secret)
        for secret in variants:
            with self.subTest(secret=secret), \
                    patch.object(module.subprocess, 'run', return_value=SimpleNamespace(returncode=0, stdout=json.dumps(secret))) as run, \
                    self.assertRaises(SystemExit):
                module.install(METADATA, Path('access.yaml'), 'management')
            self.assertEqual(run.call_count, 1)

    def test_command_failure_suppresses_secret_output(self):
        module = self.module()
        responses = [SimpleNamespace(returncode=0, stdout=json.dumps(secret_for(old_config()))),
                     SimpleNamespace(returncode=1, stdout='credential-fixture', stderr='credential-fixture')]
        with patch.object(module.subprocess, 'run', side_effect=responses) as run, \
                patch('sys.stdout', new_callable=io.StringIO) as out, self.assertRaises(SystemExit) as error:
            module.install(METADATA, Path('access.yaml'), 'management')
        self.assertEqual(run.call_count, 2)
        self.assertNotIn('credential-fixture', str(error.exception) + out.getvalue())


if __name__ == '__main__':
    unittest.main()
