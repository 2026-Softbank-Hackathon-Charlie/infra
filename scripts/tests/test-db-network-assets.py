#!/usr/bin/env python3
"""Offline contracts shared by the DB network examples, ACLs and app guardrails."""
import importlib.util
import json
from pathlib import Path
import unittest

import yaml

ROOT = Path(__file__).resolve().parents[2]
ASSETS = ROOT / 'helm/db-network'
NAMESPACE = 'hopcharlie-db-network'


class DatabaseNetworkAssetsTests(unittest.TestCase):
    def test_operator_credentials_are_external(self):
        values = yaml.safe_load((ASSETS / 'operator-values.yaml').read_text(encoding='utf-8'))
        self.assertEqual(values['fullnameOverride'], 'operator')
        self.assertEqual(values['oauth'], {'clientId': '', 'clientSecret': ''})
        self.assertNotIn('oauthSecretVolume', values)

    def test_examples_are_disabled_and_render_with_explicit_enable(self):
        spec = importlib.util.spec_from_file_location('db_network_tests', ROOT / 'scripts/tests/test-db-network.py')
        module = importlib.util.module_from_spec(spec)
        spec.loader.exec_module(module)
        for cluster in ('management', 'eks', 'gke'):
            with self.subTest(cluster=cluster):
                values = yaml.safe_load((ASSETS / f'{cluster}.yaml.example').read_text(encoding='utf-8'))
                self.assertIs(values['enabled'], False)
                self.assertEqual(values['clusterId'], cluster)
                disabled = module.DatabaseNetworkTests.render(values)
                self.assertEqual(disabled.returncode, 0, disabled.stderr)
                self.assertFalse(list(yaml.safe_load_all(disabled.stdout)))
                values['enabled'] = True
                result = module.DatabaseNetworkTests.render(values)
                self.assertEqual(result.returncode, 0, result.stderr)
                docs = [d for d in yaml.safe_load_all(result.stdout) if d]
                services = {d['metadata']['name']: d for d in docs if d['kind'] == 'Service'}
                for peer in values['egress']['peers']:
                    self.assertEqual(services['db-' + peer]['metadata']['namespace'], NAMESPACE)

    def test_tailnet_fragment_is_database_only(self):
        policy = json.loads((ASSETS / 'policy-fragment.json.example').read_text(encoding='utf-8'))
        self.assertEqual(len(policy['grants']), 3)
        pairs = set()
        for grant in policy['grants']:
            self.assertEqual(grant['ip'], ['tcp:5432'])
            pairs.update((src, dst) for src in grant['src'] for dst in grant['dst'])
        self.assertEqual(pairs, {
            ('tag:hop-db-client-eks', 'tag:hop-db-gke'),
            ('tag:hop-db-client-gke', 'tag:hop-db-eks'),
            ('tag:hop-db-client-management', 'tag:hop-db-eks'),
            ('tag:hop-db-client-management', 'tag:hop-db-gke'),
        })
        for cluster in ('management', 'eks', 'gke'):
            self.assertEqual(policy['tagOwners']['tag:hop-db-client-' + cluster], ['tag:hop-db-operator-' + cluster])

    def test_operator_namespace_is_protected_from_app_deployment(self):
        apps = yaml.safe_load((ROOT / 'helm/charts/hopcharlie-apps/values.yaml').read_text(encoding='utf-8'))
        access = yaml.safe_load((ROOT / 'helm/charts/gcp-argocd-access/values.yaml').read_text(encoding='utf-8'))
        self.assertIn(NAMESPACE, apps['deniedNamespaces'])
        self.assertIn(NAMESPACE, access['dynamicApps']['protectedNamespaces'])
        self.assertEqual(set(apps['deniedNamespaces']), set(access['dynamicApps']['protectedNamespaces']))

    def test_catalog_uses_local_services_and_an_explicit_allowlist(self):
        catalog = yaml.safe_load((ASSETS / 'apps.yaml.example').read_text(encoding='utf-8'))
        values = yaml.safe_load((ASSETS / 'platform-values.yaml.example').read_text(encoding='utf-8'))
        self.assertEqual(values['api']['dsnHostSuffixes'], '.ts.net,.hopcharlie-db-network.svc.cluster.local')
        envs = catalog['apps'][0]['environments']
        self.assertEqual(set(envs), {'eks', 'gke'})
        for name, env in envs.items():
            self.assertEqual(env['dbHost'], f'db-{name}.{NAMESPACE}.svc.cluster.local')
            self.assertTrue(env['passwordEnv'].endswith('_PGPASSWORD'))
            self.assertNotIn('password', env)
            self.assertNotIn('bucket', env)


if __name__ == '__main__':
    unittest.main()
