#!/usr/bin/env python3
"""Render contracts; real admission decisions are tested separately in kind."""
import json
import os
from pathlib import Path
import subprocess
import tempfile
import unittest

import yaml

ROOT = Path(__file__).resolve().parents[2]
CHART = ROOT / 'helm/charts/hopcharlie-platform'


def render(enabled):
    values = json.loads((ROOT / 'clusters/aws-dev-management/values/hopcharlie-platform.yaml').read_text(encoding='utf-8'))
    values['api']['onpremRegistration'] = {'enabled': enabled, 'tailnetDomain': 'tail567fe8.ts.net',
        'profile': {'podSecurity': 'restricted', 'networkPolicy': {'enabled': True}},
        'agentImage': 'example.test/agent@sha256:' + '1'*64,
        'tailscaleImage': 'example.test/tailscale@sha256:' + '2'*64,
        'helloImage': 'example.test/hello@sha256:' + '3'*64}
    with tempfile.TemporaryDirectory() as directory:
        path = Path(directory) / 'values.json'
        path.write_text(json.dumps(values), encoding='utf-8')
        result = subprocess.run([os.environ.get('HELM', 'helm'), 'template', 'platform', str(CHART),
            '-n', 'charlie-platform', '-f', str(path)], capture_output=True, text=True, encoding='utf-8')
    if result.returncode:
        raise AssertionError(result.stderr)
    return [d for d in yaml.safe_load_all(result.stdout) if d]


class RegistrationTests(unittest.TestCase):
    def test_api_identity_is_opt_in(self):
        for enabled in (False, True):
            docs = render(enabled)
            deployments = {d['metadata']['name']: d for d in docs if d['kind'] == 'Deployment'}
            api = deployments['hopcharlie-api']['spec']['template']['spec']
            self.assertEqual(api['serviceAccountName'], 'hopcharlie-platform-api' if enabled else 'hopcharlie-platform')
            for name in ('hopcharlie-gateway', 'hopcharlie-postgres'):
                if name in deployments:
                    self.assertEqual(deployments[name]['spec']['template']['spec']['serviceAccountName'], 'hopcharlie-platform')
            policies = [d for d in docs if d['kind'] == 'ValidatingAdmissionPolicy' and d['metadata']['name'].startswith('hopcharlie-onprem')]
            self.assertEqual(len(policies), 3 if enabled else 0)

    def test_no_secret_read_or_cluster_rbac(self):
        docs = render(True)
        roles = [d for d in docs if d['kind'] == 'Role' and d['metadata']['name'].startswith('hopcharlie-onprem')]
        self.assertEqual({d['metadata']['namespace'] for d in roles}, {'argocd', 'hopcharlie-db-network'})
        secret_rule = next(r for d in roles for r in d['rules'] if 'secrets' in r['resources'])
        self.assertEqual(set(secret_rule['verbs']), {'create', 'update', 'delete'})
        self.assertFalse(any(d['kind'] in ('ClusterRole', 'ClusterRoleBinding') for d in docs))


if __name__ == '__main__':
    unittest.main()
