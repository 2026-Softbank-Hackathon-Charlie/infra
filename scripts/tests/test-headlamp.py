#!/usr/bin/env python3
"""Render the team dashboard locally and enforce authentication/permission boundaries."""
import importlib.util
import json
import os
from pathlib import Path
import subprocess
import tempfile
import unittest
import yaml

ROOT = Path(__file__).resolve().parents[2]
HELM = os.environ.get('HELM', 'helm')


class HeadlampTests(unittest.TestCase):
    def render(self, chart, values):
        with tempfile.TemporaryDirectory() as temp:
            path = Path(temp) / 'values.json'
            path.write_text(json.dumps(values), encoding='utf-8')
            result = subprocess.run([HELM, 'template', 'headlamp', str(ROOT / chart),
                                     '--kube-version', '1.35.0', '--namespace', 'headlamp', '-f', str(path)],
                                    capture_output=True, text=True)
            self.assertEqual(result.returncode, 0, result.stderr)
        return [d for d in yaml.safe_load_all(result.stdout) if d]

    def test_web_uses_individual_oidc_and_private_cluster_addresses(self):
        values = {'oidc': {'issuerURL': 'https://cognito-idp.ap-northeast-2.amazonaws.com/test',
                           'clientID': 'test-client'},
                  'clusters': [{'name': 'management', 'endpoint': 'https://management.eks.amazonaws.com', 'ca': 'dGVzdA=='},
                               {'name': 'workload', 'endpoint': 'https://workload.eks.amazonaws.com', 'ca': 'dGVzdA=='}]}
        docs = self.render('helm/charts/headlamp-team', values)
        pod = next(d for d in docs if d['kind'] == 'Deployment')['spec']['template']['spec']
        self.assertFalse(pod['automountServiceAccountToken'])
        container = pod['containers'][0]
        self.assertNotIn('-in-cluster', container['args'])
        self.assertIn('-oidc-use-cookie=true', container['args'])
        self.assertFalse(any('unsafe' in arg for arg in container['args']))
        self.assertIn('@sha256:', container['image'])
        env = {e['name']: e for e in container['env']}
        self.assertIn('secretKeyRef', env['HEADLAMP_CONFIG_OIDC_CLIENT_SECRET']['valueFrom'])
        # The kubeconfig holds the client secret, so it is never rendered into a ConfigMap.
        self.assertFalse(any(d['kind'] == 'ConfigMap' for d in docs))
        volume = next(v for v in pod['volumes'] if v['name'] == 'clusters')
        self.assertEqual(volume['secret']['secretName'], 'headlamp-clusters')
        self.assertEqual(pod['securityContext']['fsGroup'], 101)
        ingress = next(d for d in docs if d['kind'] == 'Ingress')
        self.assertEqual(ingress['metadata']['annotations']['alb.ingress.kubernetes.io/listen-ports'], '[{"HTTPS":443}]')
        self.assertEqual(ingress['spec']['rules'][0]['host'], 'headlamp.hopcharlie.dev')
        self.assertFalse(any(d['kind'] == 'Secret' for d in docs))

    def test_cluster_kubeconfig_carries_oidc_client_secret(self):
        spec = importlib.util.spec_from_file_location('prepare_headlamp', ROOT / 'scripts/prepare-headlamp.py')
        prepare = importlib.util.module_from_spec(spec)
        spec.loader.exec_module(prepare)
        values = {'oidc': {'issuerURL': 'https://cognito-idp.ap-northeast-2.amazonaws.com/test', 'clientID': 'test-client'},
                  'clusters': [{'name': 'management', 'endpoint': 'https://management.eks.amazonaws.com', 'ca': 'dGVzdA=='},
                               {'name': 'workload', 'endpoint': 'https://workload.eks.amazonaws.com', 'ca': 'dGVzdA=='}]}
        kube = prepare.cluster_kubeconfig(values, 'test-secret')
        self.assertEqual([c['name'] for c in kube['contexts']], ['management', 'workload'])
        self.assertEqual({c['context']['user'] for c in kube['contexts']}, {'team-oidc'})
        provider = kube['users'][0]['user']['auth-provider']
        self.assertEqual(provider['name'], 'oidc')
        self.assertEqual(provider['config']['client-secret'], 'test-secret')
        self.assertEqual(provider['config']['client-id'], 'test-client')
        self.assertIn('scope', provider['config'])
        self.assertEqual(provider['config']['scope'], 'profile,email')
        self.assertNotIn('extra-scopes', provider['config'])
        self.assertNotIn('token', kube['users'][0]['user'])
        for broken in ({**values, 'clusters': []}, {**values, 'oidc': {'issuerURL': '', 'clientID': 'x'}}):
            with self.assertRaises(SystemExit):
                prepare.cluster_kubeconfig(broken, 'test-secret')
        with self.assertRaises(SystemExit):
            prepare.cluster_kubeconfig(values, '')

    def test_rbac_cannot_read_secrets_or_mutate_workloads(self):
        docs = self.render('helm/charts/headlamp-team', {'webEnabled': False})
        self.assertEqual({d['kind'] for d in docs}, {'ClusterRole', 'ClusterRoleBinding'})
        role = next(d for d in docs if d['kind'] == 'ClusterRole')
        for rule in role['rules']:
            self.assertFalse({'secrets', 'configmaps', '*', 'pods/exec', 'pods/attach'} & set(rule['resources']))
            if rule['apiGroups'] == ['authorization.k8s.io']:
                self.assertEqual(rule['verbs'], ['create'])
                self.assertTrue(set(rule['resources']) <= {'selfsubjectaccessreviews', 'selfsubjectrulesreviews'})
            else:
                self.assertTrue(set(rule['verbs']) <= {'get', 'list', 'watch'})
        binding = next(d for d in docs if d['kind'] == 'ClusterRoleBinding')
        self.assertEqual(binding['subjects'], [{'kind': 'Group', 'apiGroup': 'rbac.authorization.k8s.io', 'name': 'headlamp:team-viewers'}])

    def test_gitops_is_opt_in_for_both_clusters(self):
        values = json.loads((ROOT / 'helm/gitops/ci/targets-values.yaml').read_text())
        for enabled, expected in ((False, 0), (True, 2)):
            values['headlamp'] = {'enabled': enabled}
            docs = self.render('helm/gitops', values)
            apps = [d for d in docs if d['kind'] == 'Application' and d['metadata']['name'].endswith('-headlamp')]
            self.assertEqual(len(apps), expected)
            for app in apps:
                self.assertEqual(app['spec']['destination']['namespace'], 'headlamp')
                project = next(d for d in docs if d['kind'] == 'AppProject' and d['metadata']['name'] == app['spec']['project'])
                self.assertIn(app['spec']['destination'], project['spec']['destinations'])


if __name__ == '__main__':
    unittest.main()
