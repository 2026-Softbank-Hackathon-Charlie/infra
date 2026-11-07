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

    def test_platform_apps_add_only_shared_proxyclasses_and_policies(self):
        # ADR 0063: shared objects for the proxies of every platform app's database.
        for cluster in ('eks', 'gke'):
            with self.subTest(cluster=cluster):
                values = values_for(cluster)
                values['platformApps'] = {'enabled': True}
                result = self.render(values)
                self.assertEqual(result.returncode, 0, result.stderr)
                docs = [doc for doc in yaml.safe_load_all(result.stdout) if doc]
                key = lambda d: (d['kind'], d['metadata']['name'])
                base = {key(d): d for d in self.documents[cluster]}
                added = {key(d): d for d in docs if key(d) not in base}
                # The existing objects are unchanged.
                self.assertEqual({key(d): d for d in docs if key(d) in base}, base)
                self.assertEqual(sorted(added), [
                    ('NetworkPolicy', 'platform-app-db-egress-proxy'),
                    ('NetworkPolicy', 'platform-app-db-ingress-proxy'),
                    ('ProxyClass', f'hopcharlie-app-db-egress-{cluster}'),
                    ('ProxyClass', f'hopcharlie-app-db-ingress-{cluster}')])
                for role in ('ingress', 'egress'):
                    pod = added[('ProxyClass', f'hopcharlie-app-db-{role}-{cluster}')]['spec']['statefulSet']['pod']
                    self.assertEqual(pod['labels'], {'hopcharlie.dev/db-proxy': role})
                    policy = added[('NetworkPolicy', f'platform-app-db-{role}-proxy')]
                    self.assertEqual(policy['metadata']['namespace'], NAMESPACE)
                    self.assertEqual(policy['spec']['podSelector'], {'matchLabels': {'hopcharlie.dev/db-proxy': role}})
                    egress = policy['spec']['egress']
                    # DNS anywhere too: GKE NodeLocal DNSCache (ADR 0052).
                    self.assertIn({'ports': [{'protocol': 'UDP', 'port': 53}, {'protocol': 'TCP', 'port': 53}]}, egress)
                apps = {'hopcharlie.dev/db-topology': 'true'}
                egress_in = added[('NetworkPolicy', 'platform-app-db-egress-proxy')]['spec']['ingress']
                self.assertEqual(egress_in[0]['from'], [{'namespaceSelector': {'matchLabels': apps}, 'podSelector': {
                    'matchExpressions': [{'key': 'cnpg.io/cluster', 'operator': 'Exists'}]}}])
                ingress_out = added[('NetworkPolicy', 'platform-app-db-ingress-proxy')]['spec']['egress'][-1]
                self.assertEqual(ingress_out, {'to': [{'namespaceSelector': {'matchLabels': apps}, 'podSelector': {
                    'matchLabels': {'cnpg.io/instanceRole': 'primary'}}}], 'ports': [{'protocol': 'TCP', 'port': 5432}]})
        values = values_for('management')
        values['platformApps'] = {'enabled': True}
        self.assertNotEqual(self.render(values).returncode, 0)

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


ONPREM_VALUES = {
    'enabled': True,
    'clusterId': 'onprem',
    'ingress': {'tag': 'tag:hop-db-onprem'},
    'egress': {'tag': 'tag:hop-db-client-onprem', 'peers': {}},
    'platformApps': {'enabled': True},
    # k3s serves the Kubernetes API on 6443, not 443 as EKS and GKE do.
    'kubeApiServerPort': 6443,
}


class OnpremPlatformAppsTests(unittest.TestCase):
    """The laptop k3s has no static moimroom database: platform app proxies only."""

    def test_onprem_renders_only_the_shared_platform_app_objects(self):
        result = DatabaseNetworkTests.render(ONPREM_VALUES)
        self.assertEqual(result.returncode, 0, result.stderr)
        docs = [d for d in yaml.safe_load_all(result.stdout) if d]
        self.assertEqual(sorted((d['kind'], d['metadata']['name']) for d in docs), [
            ('NetworkPolicy', 'db-network-default-deny'),
            ('NetworkPolicy', 'db-network-operator'),
            ('NetworkPolicy', 'platform-app-db-egress-proxy'),
            ('NetworkPolicy', 'platform-app-db-ingress-proxy'),
            ('NetworkPolicy', 'platform-app-web-ingress-proxy'),
            ('ProxyClass', 'hopcharlie-app-db-egress-onprem'),
            ('ProxyClass', 'hopcharlie-app-db-ingress-onprem'),
            ('ProxyClass', 'hopcharlie-app-web-ingress-onprem'),
        ])
        for doc in docs:
            if doc['kind'] == 'NetworkPolicy':
                self.assertEqual(doc['metadata']['namespace'], NAMESPACE)
        # The same shared policies as the cloud workload clusters.
        gke = dict(values_for('gke'), platformApps={'enabled': True})
        cloud = {(d['kind'], d['metadata']['name']): d for d in yaml.safe_load_all(DatabaseNetworkTests.render(gke).stdout) if d}
        # The same shared policies as the cloud workload clusters, except that every
        # rule that reaches the Kubernetes API also opens the k3s API port.
        api_443 = {'to': [{'ipBlock': {'cidr': '0.0.0.0/0'}}], 'ports': [{'protocol': 'TCP', 'port': 443}]}
        api_k3s = {'to': [{'ipBlock': {'cidr': '0.0.0.0/0'}}],
                   'ports': [{'protocol': 'TCP', 'port': 443}, {'protocol': 'TCP', 'port': 6443}]}
        for doc in docs:
            if doc['kind'] == 'NetworkPolicy' and doc['metadata']['name'] != 'platform-app-web-ingress-proxy':
                expected = cloud[('NetworkPolicy', doc['metadata']['name'])]
                expected = json.loads(json.dumps(expected).replace(json.dumps(api_443), json.dumps(api_k3s)))
                self.assertEqual(doc, expected)
        operator = next(d for d in docs if d['metadata']['name'] == 'db-network-operator')
        self.assertIn(api_k3s, operator['spec']['egress'])

    def test_onprem_web_ingress_proxy_is_labelled_and_reaches_only_app_namespaces(self):
        docs = {(d['kind'], d['metadata']['name']): d
                for d in yaml.safe_load_all(DatabaseNetworkTests.render(ONPREM_VALUES).stdout) if d}
        pod = docs[('ProxyClass', 'hopcharlie-app-web-ingress-onprem')]['spec']['statefulSet']['pod']
        self.assertEqual(pod['labels'], {'hopcharlie.dev/web-proxy': 'ingress'})
        policy = docs[('NetworkPolicy', 'platform-app-web-ingress-proxy')]['spec']
        self.assertEqual(policy['podSelector'], {'matchLabels': {'hopcharlie.dev/web-proxy': 'ingress'}})
        self.assertEqual(policy['ingress'], [{'from': [{'ipBlock': {'cidr': '0.0.0.0/0'}}],
                                              'ports': [{'protocol': 'UDP', 'port': 41641}]}])
        self.assertEqual(policy['egress'][-1], {
            'to': [{'namespaceSelector': {'matchExpressions': [
                {'key': 'hopcharlie.dev/instance', 'operator': 'Exists'}]}}],
            'ports': [{'protocol': 'TCP'}]})
        # Cloud workload releases do not get the web path objects.
        gke = DatabaseNetworkTests.render(dict(values_for('gke'), platformApps={'enabled': True}))
        self.assertNotIn('web-proxy', gke.stdout)

    def test_kube_api_server_port_must_be_a_port(self):
        for port in (0, 65536, '6443', 443.5):
            with self.subTest(port=port):
                self.assertNotEqual(DatabaseNetworkTests.render(dict(ONPREM_VALUES, kubeApiServerPort=port)).returncode, 0)

    def test_profile_file_is_disabled_and_matches_tested_values(self):
        profile = yaml.safe_load((ROOT / 'helm/db-network/onprem.yaml').read_text(encoding='utf-8'))
        self.assertIs(profile['enabled'], False)
        disabled = DatabaseNetworkTests.render(profile)
        self.assertEqual(disabled.returncode, 0, disabled.stderr)
        self.assertEqual(list(filter(None, yaml.safe_load_all(disabled.stdout))), [])
        profile['enabled'] = True
        self.assertEqual(profile, ONPREM_VALUES)

    def test_onprem_without_platform_apps_or_with_static_paths_fails(self):
        static_peer = {'gke': {'hostname': 'hop-db-onprem-to-gke', 'tailnetFQDN': 'hop-db-gke-x.example-tailnet.ts.net'}}
        cases = [
            dict(ONPREM_VALUES, platformApps={'enabled': False}),
            {k: v for k, v in ONPREM_VALUES.items() if k != 'platformApps'},
            dict(ONPREM_VALUES, localDatabase={'namespace': 'db', 'cluster': 'postgres'}),
            dict(ONPREM_VALUES, ingress={'hostname': 'hop-db-onprem', 'tag': 'tag:hop-db-onprem'}),
            dict(ONPREM_VALUES, egress={'tag': 'tag:hop-db-client-onprem', 'peers': static_peer}),
            dict(ONPREM_VALUES, ingress={'tag': 'tag:hop-db-client-onprem'}),
            dict(ONPREM_VALUES, ingress={}),
        ]
        for values in cases:
            with self.subTest(values=values):
                result = DatabaseNetworkTests.render(values)
                self.assertNotEqual(result.returncode, 0)
                self.assertEqual(result.stdout.strip(), '')
        # The cloud workload profiles cannot name onprem as a static peer.
        values = values_for('gke')
        values['egress']['peers'] = {'onprem': static_peer['gke']}
        self.assertNotEqual(DatabaseNetworkTests.render(values).returncode, 0)


ROUTE_VALUES = {
    'enabled': False,
    'managementAppRoutes': {'enabled': True, 'tag': 'tag:hop-db-client-management',
                            'tailnet': 'tail567fe8.ts.net'},
}
PLATFORM_SA = 'system:serviceaccount:charlie-platform:hopcharlie-platform'


class ManagementAppRoutesTests(unittest.TestCase):
    """ADR 0064: the separate management release for platform-created app DB routes."""

    @classmethod
    def setUpClass(cls):
        result = DatabaseNetworkTests.render(ROUTE_VALUES)
        if result.returncode:
            raise AssertionError(result.stderr)
        cls.docs = {(d['kind'], d['metadata']['name']): d for d in yaml.safe_load_all(result.stdout) if d}

    def test_renders_only_route_objects_apart_from_the_live_release(self):
        self.assertEqual(sorted(self.docs), [
            ('NetworkPolicy', 'platform-app-db-egress-proxy'),
            ('NetworkPolicy', 'platform-app-web-egress-proxy'),
            ('ProxyClass', 'hopcharlie-app-db-egress-management'),
            ('ProxyClass', 'hopcharlie-app-web-egress-management'),
            ('Role', 'hopcharlie-platform-app-db-routes'),
            ('RoleBinding', 'hopcharlie-platform-app-db-routes'),
            ('ValidatingAdmissionPolicy', 'hopcharlie-platform-app-db-route-create'),
            ('ValidatingAdmissionPolicy', 'hopcharlie-platform-app-db-route-delete'),
            ('ValidatingAdmissionPolicyBinding', 'hopcharlie-platform-app-db-route-create'),
            ('ValidatingAdmissionPolicyBinding', 'hopcharlie-platform-app-db-route-delete'),
        ])
        for doc in self.docs.values():
            if doc['kind'] in ('NetworkPolicy', 'Role', 'RoleBinding'):
                self.assertEqual(doc['metadata']['namespace'], NAMESPACE)
        # Nothing the live management db-network release owns, and that
        # release renders exactly as before.
        live = yaml.safe_load((ROOT / 'helm/db-network/management.yaml').read_text(encoding='utf-8'))
        live['enabled'] = True
        result = DatabaseNetworkTests.render(live)
        self.assertEqual(result.returncode, 0, result.stderr)
        owned = {(d['kind'], d['metadata']['name']) for d in yaml.safe_load_all(result.stdout) if d}
        self.assertEqual(len(owned), 8)
        self.assertFalse(owned & set(self.docs))
        self.assertNotIn('management-app-routes.yaml', result.stdout)

    def test_profile_file_is_disabled_and_matches_tested_values(self):
        profile = yaml.safe_load((ROOT / 'helm/db-network/management-app-routes.yaml').read_text(encoding='utf-8'))
        self.assertIs(profile['enabled'], False)
        self.assertIs(profile['managementAppRoutes']['enabled'], False)
        disabled = DatabaseNetworkTests.render(profile)
        self.assertEqual(disabled.returncode, 0, disabled.stderr)
        self.assertEqual(list(filter(None, yaml.safe_load_all(disabled.stdout))), [])
        profile['managementAppRoutes']['enabled'] = True
        self.assertEqual(profile, ROUTE_VALUES)
        # The same tailnet as the live static management routes.
        live = yaml.safe_load((ROOT / 'helm/db-network/management.yaml').read_text(encoding='utf-8'))
        for peer in live['egress']['peers'].values():
            self.assertTrue(peer['tailnetFQDN'].endswith('.' + ROUTE_VALUES['managementAppRoutes']['tailnet']))
        self.assertEqual(live['egress']['tag'], ROUTE_VALUES['managementAppRoutes']['tag'])

    def test_proxy_class_labels_pods_for_the_shared_policy(self):
        pod = self.docs[('ProxyClass', 'hopcharlie-app-db-egress-management')]['spec']['statefulSet']['pod']
        self.assertEqual(pod['labels'], {'hopcharlie.dev/db-proxy': 'egress'})
        self.assertEqual(pod['tailscaleContainer']['resources'],
                         {'requests': {'cpu': '50m', 'memory': '128Mi'}, 'limits': {'memory': '512Mi'}})

    def test_policy_admits_the_platform_api_like_the_static_proxies(self):
        policy = self.docs[('NetworkPolicy', 'platform-app-db-egress-proxy')]['spec']
        self.assertEqual(policy['podSelector'], {'matchLabels': {'hopcharlie.dev/db-proxy': 'egress'}})
        self.assertEqual(policy['policyTypes'], ['Ingress', 'Egress'])
        self.assertEqual(policy['ingress'][0], {
            'from': [{'namespaceSelector': {'matchLabels': {'kubernetes.io/metadata.name': 'charlie-platform'}},
                      'podSelector': {'matchLabels': {'app.kubernetes.io/part-of': 'hopcharlie',
                                                      'app.kubernetes.io/component': 'api'}}}],
            'ports': [{'protocol': 'TCP', 'port': 5432}],
        })
        static = DatabaseNetworkTests.render(values_for('management'))
        proxy = next(d['spec'] for d in yaml.safe_load_all(static.stdout)
                     if d and d['kind'] == 'NetworkPolicy' and d['metadata']['name'] == 'db-eks-proxy')
        self.assertEqual(policy['ingress'], proxy['ingress'])
        self.assertEqual(policy['egress'], proxy['egress'])

    def test_web_egress_proxy_admits_only_platform_api_and_gateway_on_80(self):
        pod = self.docs[('ProxyClass', 'hopcharlie-app-web-egress-management')]['spec']['statefulSet']['pod']
        self.assertEqual(pod['labels'], {'hopcharlie.dev/web-proxy': 'egress'})
        policy = self.docs[('NetworkPolicy', 'platform-app-web-egress-proxy')]['spec']
        self.assertEqual(policy['podSelector'], {'matchLabels': {'hopcharlie.dev/web-proxy': 'egress'}})
        self.assertEqual(policy['ingress'][0], {
            'from': [{'namespaceSelector': {'matchLabels': {'kubernetes.io/metadata.name': 'charlie-platform'}},
                      'podSelector': {'matchLabels': {'app.kubernetes.io/part-of': 'hopcharlie'},
                                      'matchExpressions': [{'key': 'app.kubernetes.io/component',
                                                            'operator': 'In', 'values': ['api', 'gateway']}]}}],
            'ports': [{'protocol': 'TCP', 'port': 80}],
        })
        self.assertEqual(policy['egress'], self.docs[('NetworkPolicy', 'platform-app-db-egress-proxy')]['spec']['egress'])

    def test_platform_may_only_get_list_watch_create_delete_services_here(self):
        role = self.docs[('Role', 'hopcharlie-platform-app-db-routes')]
        self.assertEqual(role['rules'], [{'apiGroups': [''], 'resources': ['services'],
                                          'verbs': ['get', 'list', 'watch', 'create', 'delete']}])
        binding = self.docs[('RoleBinding', 'hopcharlie-platform-app-db-routes')]
        self.assertEqual(binding['roleRef'], {'apiGroup': 'rbac.authorization.k8s.io', 'kind': 'Role',
                                              'name': 'hopcharlie-platform-app-db-routes'})
        self.assertEqual(binding['subjects'], [{'kind': 'ServiceAccount', 'name': 'hopcharlie-platform',
                                                'namespace': 'charlie-platform'}])

    def test_admission_create_checks_object_and_delete_checks_old_object(self):
        for op in ('create', 'delete'):
            name = f'hopcharlie-platform-app-db-route-{op}'
            spec = self.docs[('ValidatingAdmissionPolicy', name)]['spec']
            self.assertEqual(spec['failurePolicy'], 'Fail')
            self.assertEqual(spec['matchConstraints']['resourceRules'], [{
                'apiGroups': [''], 'apiVersions': ['v1'], 'operations': [op.upper()], 'resources': ['services']}])
            self.assertEqual(spec['matchConstraints']['namespaceSelector'],
                             {'matchLabels': {'kubernetes.io/metadata.name': NAMESPACE}})
            self.assertEqual(spec['matchConditions'], [{
                'name': 'platform-service-account',
                'expression': f"request.userInfo.username == '{PLATFORM_SA}' || (request.userInfo.username == 'system:serviceaccount:charlie-platform:hopcharlie-platform-api' && !{'object' if op == 'create' else 'oldObject'}.metadata.name.startsWith('hop-k3s-'))"}])
            text = '\n'.join(item['expression'] for item in spec.get('variables', []) + spec['validations'])
            if op == 'create':
                self.assertNotIn('oldObject', text)
                for needle in ("'tag:hop-db-client-management'", "'hopcharlie-app-db-egress-management'",
                               "'ExternalName'", "'placeholder'", '5432', "'hopcharlie-platform'",
                               "object.metadata.name.matches('^db-(eks|gke|onprem|legacy)-[a-z0-9][a-z0-9-]{0,40}$')",
                               "variables.route in ['eks', 'gke', 'onprem', 'legacy']",
                               "'^hop-db-(eks|gke|onprem|legacy)-[a-z0-9]([a-z0-9-]{0,61}[a-z0-9])?\\\\.tail567fe8\\\\.ts\\\\.net$'",
                               "'^hop-db-management-to-(eks|gke|onprem|legacy)-[a-z0-9-]+$'",
                               "'hop-' + object.metadata.name + '.tail567fe8.ts.net'"):
                    self.assertIn(needle, text)
            else:
                self.assertNotIn('object.', text.replace('oldObject.', ''))
                self.assertIn("oldObject.metadata.labels['app.kubernetes.io/managed-by'] == 'hopcharlie-platform'", text)
            binding = self.docs[('ValidatingAdmissionPolicyBinding', name)]['spec']
            self.assertEqual(binding, {'policyName': name, 'validationActions': ['Deny']})

    def test_invalid_route_settings_fail_closed(self):
        routes = ROUTE_VALUES['managementAppRoutes']
        both = values_for('management')
        both['managementAppRoutes'] = routes
        cases = [
            (both, NAMESPACE),
            (ROUTE_VALUES, 'charlie-platform'),
            (dict(ROUTE_VALUES, managementAppRoutes=dict(routes, tailnet='')), NAMESPACE),
            (dict(ROUTE_VALUES, managementAppRoutes=dict(routes, tailnet='example.com')), NAMESPACE),
            (dict(ROUTE_VALUES, managementAppRoutes=dict(routes, tag='plain')), NAMESPACE),
        ]
        for values, namespace in cases:
            with self.subTest(values=values, namespace=namespace):
                result = DatabaseNetworkTests.render(values, namespace)
                self.assertNotEqual(result.returncode, 0)
                self.assertEqual(result.stdout.strip(), '')


if __name__ == '__main__':
    unittest.main()
