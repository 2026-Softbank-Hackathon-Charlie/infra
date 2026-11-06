#!/usr/bin/env python3
"""Render-only checks for the opt-in PostgreSQL Tailscale boundary."""
import json
import os
from pathlib import Path
import subprocess
import tempfile
import unittest

import yaml

ROOT = Path(__file__).resolve().parents[2]
CHART = ROOT / 'helm/charts/hopcharlie-db-network'
NAMESPACE = 'hopcharlie-db-network'
PROFILES = {
    'management': ('eks', 'gke'),
    'eks': ('gke',),
    'gke': ('eks',),
}


def values_for(cluster):
    values = {
        'enabled': True,
        'clusterId': cluster,
        'egress': {
            'tag': f'tag:db-{cluster}-client',
            'peers': {
                peer: {
                    'hostname': f'db-{cluster}-to-{peer}',
                    'tailnetFQDN': f'db-{peer}.example-tailnet.ts.net',
                }
                for peer in PROFILES[cluster]
            },
        },
    }
    if cluster != 'management':
        values['localDatabase'] = {'namespace': 'workload-data', 'cluster': 'postgres-poc'}
        values['ingress'] = {'hostname': f'db-{cluster}', 'tag': f'tag:db-{cluster}'}
    return values


def proxy_labels(service, namespace):
    return {
        'tailscale.com/managed': 'true',
        'tailscale.com/parent-resource': service,
        'tailscale.com/parent-resource-ns': namespace,
        'tailscale.com/parent-resource-type': 'svc',
    }


class DatabaseNetworkTests(unittest.TestCase):
    @staticmethod
    def render(values=None, namespace=NAMESPACE):
        with tempfile.TemporaryDirectory(prefix='db-network-render-') as directory:
            path = Path(directory) / 'values.json'
            path.write_text(json.dumps(values or {}), encoding='utf-8')
            return subprocess.run(
                [os.environ.get('HELM', 'helm'), 'template', 'db-network', str(CHART),
                 '--namespace', namespace, '--kube-version', '1.35.0', '-f', str(path)],
                capture_output=True, text=True, encoding='utf-8',
            )

    @classmethod
    def setUpClass(cls):
        cls.documents = {}
        for cluster in PROFILES:
            result = cls.render(values_for(cluster))
            if result.returncode:
                raise AssertionError(result.stderr)
            cls.documents[cluster] = [doc for doc in yaml.safe_load_all(result.stdout) if doc]

    def policy(self, cluster, name, namespace=NAMESPACE):
        return next(doc['spec'] for doc in self.documents[cluster]
                    if doc['kind'] == 'NetworkPolicy'
                    and doc['metadata']['name'] == name
                    and doc['metadata']['namespace'] == namespace)

    def test_disabled_is_empty_even_without_cluster_context(self):
        for values, namespace in (({}, 'default'), ({'enabled': False}, NAMESPACE)):
            with self.subTest(values=values):
                result = self.render(values, namespace)
                self.assertEqual(result.returncode, 0, result.stderr)
                self.assertEqual(list(filter(None, yaml.safe_load_all(result.stdout))), [])

    def test_profiles_only_own_their_service_proxyclass_and_policies(self):
        for cluster, docs in self.documents.items():
            with self.subTest(cluster=cluster):
                self.assertEqual(len(docs), 8 if cluster == 'management' else 9)
                self.assertEqual({doc['kind'] for doc in docs},
                                 {'ProxyClass', 'Service', 'NetworkPolicy'})
                services = [doc for doc in docs if doc['kind'] == 'Service']
                self.assertEqual(len(services), 2)
                for doc in docs:
                    if doc['kind'] != 'ProxyClass':
                        self.assertIn(doc['metadata']['namespace'],
                                      {NAMESPACE, 'charlie-platform', 'workload-data'})

    def test_proxyclass_resources_are_overridable_and_names_do_not_collide(self):
        names = []
        for cluster, docs in self.documents.items():
            proxy = next(doc for doc in docs if doc['kind'] == 'ProxyClass')
            names.append(proxy['metadata']['name'])
            self.assertNotIn('namespace', proxy['metadata'])
            self.assertEqual(proxy['apiVersion'], 'tailscale.com/v1alpha1')
            resources = proxy['spec']['statefulSet']['pod']['tailscaleContainer']['resources']
            self.assertEqual(resources, {'requests': {'cpu': '50m', 'memory': '128Mi'},
                                         'limits': {'memory': '512Mi'}})
            self.assertEqual(proxy['metadata']['name'], f'{NAMESPACE}-{cluster}')
        self.assertEqual(len(set(names)), 3)
        values = values_for('eks')
        values['proxyResources'] = {'requests': {'cpu': '100m', 'memory': '256Mi'},
                                    'limits': {'memory': '1Gi'}}
        result = self.render(values)
        self.assertEqual(result.returncode, 0, result.stderr)
        proxy = next(doc for doc in yaml.safe_load_all(result.stdout)
                     if doc and doc['kind'] == 'ProxyClass')
        self.assertEqual(proxy['spec']['statefulSet']['pod']['tailscaleContainer']['resources'],
                         values['proxyResources'])

    def test_services_only_publish_postgres_and_use_operator_annotations(self):
        for cluster, docs in self.documents.items():
            values = values_for(cluster)
            for service in (doc for doc in docs if doc['kind'] == 'Service'):
                spec = service['spec']
                annotations = service['metadata']['annotations']
                self.assertEqual(spec['ports'],
                                 [{'name': 'postgres', 'port': 5432,
                                   'targetPort': 5432, 'protocol': 'TCP'}])
                self.assertEqual(annotations['tailscale.com/proxy-class'], f'{NAMESPACE}-{cluster}')
                name = service['metadata']['name']
                if name.startswith('db-'):
                    peer = name.removeprefix('db-')
                    self.assertIn(peer, PROFILES[cluster])
                    self.assertEqual(service['metadata']['namespace'], NAMESPACE)
                    self.assertEqual(spec['type'], 'ExternalName')
                    self.assertEqual(spec['externalName'], 'placeholder')
                    self.assertNotIn('selector', spec)
                    self.assertEqual(annotations['tailscale.com/tailnet-fqdn'],
                                     values['egress']['peers'][peer]['tailnetFQDN'])
                    self.assertEqual(annotations['tailscale.com/hostname'],
                                     values['egress']['peers'][peer]['hostname'])
                    self.assertEqual(annotations['tailscale.com/tags'], values['egress']['tag'])
                else:
                    self.assertNotEqual(cluster, 'management')
                    self.assertEqual(name, f'{NAMESPACE}-{cluster}-ingress')
                    self.assertEqual(service['metadata']['namespace'], 'workload-data')
                    self.assertEqual(spec['type'], 'ClusterIP')
                    self.assertEqual(spec['selector'], {'cnpg.io/cluster': 'postgres-poc',
                                                       'cnpg.io/instanceRole': 'primary'})
                    self.assertEqual(annotations['tailscale.com/expose'], 'true')
                    self.assertEqual(annotations['tailscale.com/hostname'], values['ingress']['hostname'])
                    self.assertEqual(annotations['tailscale.com/tags'], values['ingress']['tag'])
                self.assertFalse(name.endswith('-rw'))

    def test_operator_policy_matches_pinned_operator_chart_pod_labels(self):
        # tailscale-operator 1.104.4 Deployment.spec.template.metadata.labels.
        for cluster in PROFILES:
            self.assertEqual(self.policy(cluster, 'db-network-operator')['podSelector'],
                             {'matchLabels': {'app': 'operator'}})

    def test_infra_default_deny_and_operator_connectivity_are_explicit(self):
        for cluster in PROFILES:
            deny = self.policy(cluster, 'db-network-default-deny')
            self.assertEqual(deny, {'podSelector': {}, 'policyTypes': ['Ingress', 'Egress']})
            operator = self.policy(cluster, 'db-network-operator')

            self.assertEqual(operator['policyTypes'], ['Egress'])
            self.assertEqual(operator['egress'][0], {
                'to': [{'namespaceSelector': {'matchLabels': {'kubernetes.io/metadata.name': 'kube-system'}},
                        'podSelector': {'matchLabels': {'k8s-app': 'kube-dns'}}}],
                'ports': [{'protocol': 'UDP', 'port': 53}, {'protocol': 'TCP', 'port': 53}],
            })
            self.assertEqual(operator['egress'][1], {
                'to': [{'ipBlock': {'cidr': '0.0.0.0/0'}}],
                'ports': [{'protocol': 'TCP', 'port': 443}],
            })
            # GKE NodeLocal DNSCache is hostNetwork: DNS must not depend on the kube-dns podSelector.
            self.assertEqual(operator['egress'][2], {
                'ports': [{'protocol': 'UDP', 'port': 53}, {'protocol': 'TCP', 'port': 53}],
            })

    def test_proxy_policy_selects_exact_parent_and_supports_direct_and_derp(self):
        for cluster, docs in self.documents.items():
            for service in (doc for doc in docs if doc['kind'] == 'Service'):
                name, namespace = service['metadata']['name'], service['metadata']['namespace']
                policy = self.policy(cluster, name + '-proxy')
                self.assertEqual(policy['podSelector'], {'matchLabels': proxy_labels(name, namespace)})
                self.assertEqual(set(policy['policyTypes']), {'Ingress', 'Egress'})
                self.assertIn({'from': [{'ipBlock': {'cidr': '0.0.0.0/0'}}],
                               'ports': [{'protocol': 'UDP', 'port': 41641}]}, policy['ingress'])
                self.assertIn({'to': [{'ipBlock': {'cidr': '0.0.0.0/0'}}],
                               'ports': [{'protocol': 'TCP', 'port': 443}]}, policy['egress'])
                # A peer's NAT may select any UDP destination port.
                self.assertIn({'to': [{'ipBlock': {'cidr': '0.0.0.0/0'}}],
                               'ports': [{'protocol': 'UDP'}]}, policy['egress'])
                self.assertIn({
                    'to': [{'namespaceSelector': {'matchLabels': {'kubernetes.io/metadata.name': 'kube-system'}},
                            'podSelector': {'matchLabels': {'k8s-app': 'kube-dns'}}}],
                    'ports': [{'protocol': 'UDP', 'port': 53}, {'protocol': 'TCP', 'port': 53}],
                }, policy['egress'])

    def test_management_client_selectors_match_existing_platform_api_pods(self):
        result = subprocess.run(
            [os.environ.get('HELM', 'helm'), 'template', 'hopcharlie-platform',
             str(ROOT / 'helm/charts/hopcharlie-platform'), '--namespace', 'charlie-platform',
             '-f', str(ROOT / 'clusters/aws-dev-management/values/hopcharlie-platform.yaml')],
            capture_output=True, text=True, encoding='utf-8',
        )
        self.assertEqual(result.returncode, 0, result.stderr)
        api = next(doc for doc in yaml.safe_load_all(result.stdout)
                   if doc and doc['kind'] == 'Deployment'
                   and doc['metadata']['name'] == 'hopcharlie-api')
        labels = api['spec']['template']['metadata']['labels']
        client = self.policy('management', f'{NAMESPACE}-management-client', 'charlie-platform')
        selectors = [client['podSelector']['matchLabels']]
        for peer in PROFILES['management']:
            proxy = self.policy('management', f'db-{peer}-proxy')
            selectors.extend(source['podSelector']['matchLabels']
                             for rule in proxy['ingress']
                             for source in rule['from'] if 'podSelector' in source)
        for selector in selectors:
            self.assertTrue(selector)
            self.assertEqual({key: labels.get(key) for key in selector}, selector)

    def test_clients_and_databases_only_gain_exact_proxy_postgres_allowances(self):
        postgres = [{'protocol': 'TCP', 'port': 5432}]
        for cluster in PROFILES:
            client_namespace = 'charlie-platform' if cluster == 'management' else 'workload-data'
            client_labels = ({'app.kubernetes.io/part-of': 'hopcharlie',
                              'app.kubernetes.io/component': 'api'}
                             if cluster == 'management' else {'cnpg.io/cluster': 'postgres-poc'})
            client = self.policy(cluster, f'{NAMESPACE}-{cluster}-client', client_namespace)
            self.assertEqual(client['podSelector'], {'matchLabels': client_labels})
            self.assertEqual(client['policyTypes'], ['Egress'])
            self.assertEqual(len(client['egress']), 1)
            self.assertEqual(client['egress'][0]['ports'], postgres)
            expected_targets = [
                {'namespaceSelector': {'matchLabels': {'kubernetes.io/metadata.name': NAMESPACE}},
                 'podSelector': {'matchLabels': proxy_labels(f'db-{peer}', NAMESPACE)}}
                for peer in PROFILES[cluster]
            ]
            self.assertCountEqual(client['egress'][0]['to'], expected_targets)
            for peer in PROFILES[cluster]:
                proxy = self.policy(cluster, f'db-{peer}-proxy')
                self.assertIn({
                    'from': [{'namespaceSelector': {'matchLabels': {'kubernetes.io/metadata.name': client_namespace}},
                              'podSelector': {'matchLabels': client_labels}}],
                    'ports': postgres,
                }, proxy['ingress'])
                self.assertEqual(len(proxy['ingress']), 2)
                self.assertEqual(len(proxy['egress']), 4)  # control (kube-dns, 443, any-DNS) + peer UDP
            if cluster != 'management':
                shadow = f'{NAMESPACE}-{cluster}-ingress'
                incoming = self.policy(cluster, shadow + '-proxy')
                self.assertEqual(len(incoming['ingress']), 1)
                self.assertIn({
                    'to': [{'namespaceSelector': {'matchLabels': {'kubernetes.io/metadata.name': 'workload-data'}},
                            'podSelector': {'matchLabels': {'cnpg.io/cluster': 'postgres-poc',
                                                           'cnpg.io/instanceRole': 'primary'}}}],
                    'ports': postgres,
                }, incoming['egress'])
                database = self.policy(cluster, f'{NAMESPACE}-{cluster}-database', 'workload-data')
                self.assertEqual(database, {
                    'podSelector': {'matchLabels': {'cnpg.io/cluster': 'postgres-poc',
                                                   'cnpg.io/instanceRole': 'primary'}},
                    'policyTypes': ['Ingress'],
                    'ingress': [{
                        'from': [{'namespaceSelector': {'matchLabels': {'kubernetes.io/metadata.name': NAMESPACE}},
                                  'podSelector': {'matchLabels': proxy_labels(shadow, 'workload-data')}}],
                        'ports': postgres,
                    }],
                })

    def test_enabled_missing_or_invalid_config_fails_closed(self):
        cases = [
            {'enabled': True},
            {'enabled': True, 'clusterId': 'other'},
            dict(values_for('management'), localDatabase={'namespace': 'db', 'cluster': 'postgres'}),
            dict(values_for('management'), ingress={'hostname': 'db-local', 'tag': 'tag:db'}),
            dict(values_for('eks'), localDatabase={'namespace': '', 'cluster': 'postgres'}),
            dict(values_for('gke'), localDatabase={'namespace': 'db', 'cluster': ''}),
            dict(values_for('eks'), egress={'tag': 'tag:db-client', 'peers': {
                'eks': {'hostname': 'self', 'tailnetFQDN': 'self.example-tailnet.ts.net'}}}),
        ]
        for cluster in PROFILES:
            for path, invalid in (
                (('egress', 'tag'), ''),
                (('egress', 'tag'), 'plain-tag'),
                (('egress', 'peers', PROFILES[cluster][0], 'hostname'), 'not.a-hostname'),
                (('egress', 'peers', PROFILES[cluster][0], 'tailnetFQDN'), '100.64.0.1'),
                (('egress', 'peers', PROFILES[cluster][0], 'tailnetFQDN'), 'db.example.com'),
                (('egress', 'peers', PROFILES[cluster][0], 'tailnetFQDN'), 'https://db.x.ts.net'),
            ):
                values = values_for(cluster)
                target = values
                for key in path[:-1]:
                    target = target[key]
                target[path[-1]] = invalid
                cases.append(values)
        for values in cases:
            with self.subTest(values=values):
                result = self.render(values)
                self.assertNotEqual(result.returncode, 0)
                self.assertEqual(result.stdout.strip(), '')
        wrong_namespace = self.render(values_for('management'), 'charlie-apps')
        self.assertNotEqual(wrong_namespace.returncode, 0)
        values = values_for('management')
        values['egress']['peers']['gke']['hostname'] = values['egress']['peers']['eks']['hostname']
        self.assertNotEqual(self.render(values).returncode, 0)
        values = values_for('eks')
        values['ingress']['hostname'] = values['egress']['peers']['gke']['hostname']
        self.assertNotEqual(self.render(values).returncode, 0)
        values = values_for('eks')
        values['ingress']['tag'] = values['egress']['tag']
        self.assertNotEqual(self.render(values).returncode, 0)


if __name__ == '__main__':
    unittest.main()