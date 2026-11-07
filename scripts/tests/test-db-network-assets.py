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

    def test_management_operational_profile_is_disabled_and_scoped(self):
        profile = ASSETS / 'management.yaml'
        self.assertTrue(profile.is_file(), 'management operational profile is missing')
        values = yaml.safe_load(profile.read_text(encoding='utf-8'))
        self.assertIs(values['enabled'], False)
        self.assertEqual(values['clusterId'], 'management')
        self.assertNotIn('localDatabase', values)
        self.assertNotIn('ingress', values)
        spec = importlib.util.spec_from_file_location('db_network_tests', ROOT / 'scripts/tests/test-db-network.py')
        module = importlib.util.module_from_spec(spec)
        spec.loader.exec_module(module)
        disabled = module.DatabaseNetworkTests.render(values)
        self.assertEqual(disabled.returncode, 0, disabled.stderr)
        self.assertEqual(list(filter(None, yaml.safe_load_all(disabled.stdout))), [])
        values['enabled'] = True
        result = module.DatabaseNetworkTests.render(values)
        self.assertEqual(result.returncode, 0, result.stderr)
        docs = [doc for doc in yaml.safe_load_all(result.stdout) if doc]
        kinds = [doc['kind'] for doc in docs]
        self.assertEqual(sorted(kinds), sorted(['ProxyClass'] + ['Service'] * 2 + ['NetworkPolicy'] * 5))
        services = {doc['metadata']['name']: doc for doc in docs if doc['kind'] == 'Service'}
        self.assertEqual(set(services), {'db-eks', 'db-gke'})
        client_labels = {'app.kubernetes.io/part-of': 'hopcharlie', 'app.kubernetes.io/component': 'api'}
        client_source = {
            'namespaceSelector': {'matchLabels': {'kubernetes.io/metadata.name': 'charlie-platform'}},
            'podSelector': {'matchLabels': client_labels},
        }
        policies = {doc['metadata']['name']: doc for doc in docs if doc['kind'] == 'NetworkPolicy'}
        client = policies['hopcharlie-db-network-management-client']
        self.assertEqual(client['metadata']['namespace'], 'charlie-platform')
        self.assertEqual(client['spec']['podSelector'], {'matchLabels': client_labels})
        destinations = []
        for peer, service in services.items():
            target = peer.removeprefix('db-')
            self.assertEqual(service['metadata']['namespace'], NAMESPACE)
            self.assertEqual(service['metadata']['annotations'], {
                'tailscale.com/tailnet-fqdn': f'hop-db-{target}-moimroom.tail567fe8.ts.net',
                'tailscale.com/hostname': f'hop-db-management-to-{target}',
                'tailscale.com/tags': 'tag:hop-db-client-management',
                'tailscale.com/proxy-class': f'{NAMESPACE}-management',
            })
            proxy_labels = module.proxy_labels(peer, NAMESPACE)
            proxy = policies[peer + '-proxy']
            self.assertEqual(proxy['metadata']['namespace'], NAMESPACE)
            self.assertEqual(proxy['spec']['podSelector'], {'matchLabels': proxy_labels})
            self.assertEqual(proxy['spec']['ingress'][0], {
                'from': [client_source], 'ports': [{'protocol': 'TCP', 'port': 5432}],
            })
            destinations.append({
                'namespaceSelector': {'matchLabels': {'kubernetes.io/metadata.name': NAMESPACE}},
                'podSelector': {'matchLabels': proxy_labels},
            })
        self.assertEqual(client['spec']['egress'], [{
            'to': destinations, 'ports': [{'protocol': 'TCP', 'port': 5432}],
        }])

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

    def test_active_management_allowlist_only_adds_db_network_namespace(self):
        values = yaml.safe_load((ROOT / 'clusters/aws-dev-management/values/hopcharlie-platform.yaml').read_text(encoding='utf-8'))
        self.assertEqual(values['api']['dsnHostSuffixes'], '.ts.net,.hopcharlie-db-network.svc.cluster.local')

    def test_onprem_is_a_paired_database_environment(self):
        platform = yaml.safe_load((ROOT / 'clusters/aws-dev-management/values/hopcharlie-platform.yaml').read_text(encoding='utf-8'))
        templates = dict(t.split('=', 1) for t in platform['api']['appDbTopology']['hostTemplates'].split(','))
        self.assertEqual(templates, {
            env: f'db-{env}-{{instance}}.{NAMESPACE}.svc.cluster.local' for env in ('eks', 'gke', 'onprem')
        })
        profiles = yaml.safe_load((ROOT / 'clusters/aws-dev-management/values/hopcharlie-apps.yaml').read_text(encoding='utf-8'))
        self.assertLessEqual(set(templates), set(profiles['environments']))
        onprem = profiles['environments']['onprem']
        self.assertEqual(onprem['cluster'], {'name': 'charlie-dev-onprem-workload'})
        profile = onprem['profile']
        self.assertEqual(profile['postgres']['storageClass'], 'local-path')
        self.assertIs(profile['postgres']['backup']['enabled'], False)
        self.assertIs(profile['ingress']['enabled'], False)
        self.assertEqual(profile['publicService']['type'], 'ClusterIP')
        self.assertIs(profile['observability']['enabled'], False)
        # The app chart's proxies use the shared objects of the onprem db-network release.
        release = yaml.safe_load((ASSETS / 'onprem.yaml').read_text(encoding='utf-8'))
        self.assertEqual(profile['dbNetwork'], {
            'enabled': True,
            'tailnetDomain': 'tail567fe8.ts.net',
            'ingressTag': release['ingress']['tag'],
            'egressTag': release['egress']['tag'],
            'ingressProxyClass': 'hopcharlie-app-db-ingress-onprem',
            'egressProxyClass': 'hopcharlie-app-db-egress-onprem',
            'proxyNamespace': NAMESPACE,
        })


if __name__ == '__main__':
    unittest.main()
