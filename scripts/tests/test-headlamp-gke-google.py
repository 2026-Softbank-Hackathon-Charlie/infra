#!/usr/bin/env python3
"""Security boundaries for the optional Google-authenticated Headlamp."""
import json
import importlib.util
import os
from pathlib import Path
import subprocess
import tempfile
import unittest

import yaml

ROOT = Path(__file__).resolve().parents[2]
CHART = ROOT / 'helm/charts/headlamp-gke-google'
IMAGE = ('quay.io/oauth2-proxy/oauth2-proxy:v7.15.5@sha256:'
         '8498b0d0ef0a7b29686414000a08aee467f02d0299c9ed1e006a8f33fc017916')


class GoogleHeadlampTests(unittest.TestCase):
    def render(self, overrides=None):
        values = {'proxy': {'image': IMAGE, 'trustedProxyCIDRs': ['10.40.0.0/16']},
                  'oauth': {'existingSecret': 'google-oauth'},
                  'clustersSecret': 'gke-cluster'}
        if overrides:
            values.update(overrides)
        with tempfile.TemporaryDirectory() as temp:
            path = Path(temp) / 'values.json'
            path.write_text(json.dumps(values), encoding='utf-8')
            result = subprocess.run([os.environ.get('HELM', 'helm'), 'template', 'gke',
                                     str(CHART), '--namespace', 'headlamp', '-f', str(path)],
                                    capture_output=True, text=True, encoding='utf-8')
        return result

    def test_authenticated_proxy_is_only_network_reachable_container(self):
        result = self.render()
        self.assertEqual(result.returncode, 0, result.stderr)
        docs = [x for x in yaml.safe_load_all(result.stdout) if x]
        spec = importlib.util.spec_from_file_location('check_helm', ROOT / 'scripts/check-helm.py')
        checker = importlib.util.module_from_spec(spec)
        spec.loader.exec_module(checker)
        self.assertIn(IMAGE, checker.check_images(docs))
        pod = next(x for x in docs if x['kind'] == 'Deployment')['spec']['template']['spec']
        self.assertFalse(pod['automountServiceAccountToken'])
        containers = {x['name']: x for x in pod['containers']}
        headlamp, proxy = containers['headlamp'], containers['oauth2-proxy']
        for flag in ('-in-cluster=false', '-base-url=/gke', '-listen-addr=127.0.0.1',
                     '-proxy-auth=true', '-proxy-auth-token-header=X-Forwarded-Access-Token',
                     '-proxy-auth-group-header=X-Forwarded-Groups'):
            self.assertIn(flag, headlamp['args'])
        self.assertNotIn('ports', headlamp)
        args = proxy['args']
        for flag in ('--provider=google', '--proxy-prefix=/gke/oauth2',
                     '--upstream=http://127.0.0.1:4466', '--pass-access-token=true',
                     '--pass-authorization-header=true',
                     '--pass-user-headers=true', '--skip-auth-strip-headers=true',
                     '--authenticated-emails-file=/etc/oauth2-proxy/allowed-emails',
                     '--cookie-refresh=5m', '--cookie-expire=168h', '--cookie-path=/gke',
                     '--cookie-secure=true', '--cookie-httponly=true',
                     '--reverse-proxy=true', '--trusted-proxy-ip=10.40.0.0/16',
                     '--whitelist-domain=headlamp.hopcharlie.dev', '--request-logging=false'):
            self.assertIn(flag, args)
        self.assertFalse(any(arg.startswith('--email-domain') or arg.startswith('--trusted-ip') for arg in args))
        self.assertIn('https://www.googleapis.com/auth/cloud-platform', ' '.join(args))
        env = {x['name']: x['valueFrom']['secretKeyRef'] for x in proxy['env']}
        self.assertEqual({x['key'] for x in env.values()}, {'client-id', 'client-secret', 'cookie-secret'})
        self.assertTrue(all(x['name'] == 'google-oauth' for x in env.values()))
        volumes = {x['name']: x for x in pod['volumes']}
        self.assertEqual(volumes['oauth']['secret']['secretName'], 'google-oauth')
        self.assertEqual(volumes['clusters']['secret']['secretName'], 'gke-cluster')
        service = next(x for x in docs if x['kind'] == 'Service')
        self.assertEqual(service['spec']['ports'][0]['targetPort'], 'proxy')
        ingress = next(x for x in docs if x['kind'] == 'Ingress')
        self.assertEqual(ingress['metadata']['annotations']['alb.ingress.kubernetes.io/group.order'], '19')
        self.assertEqual(ingress['metadata']['annotations']['alb.ingress.kubernetes.io/healthcheck-path'], '/ping')
        self.assertEqual(ingress['spec']['rules'][0]['http']['paths'][0]['path'], '/gke')
        policy = next(x for x in docs if x['kind'] == 'NetworkPolicy')
        self.assertEqual(policy['spec']['podSelector']['matchLabels']['app.kubernetes.io/name'], 'headlamp-gke-google')
        self.assertEqual(policy['spec']['ingress'][0]['ports'][0]['port'], 4180)
        self.assertFalse(any(x['kind'] == 'Secret' for x in docs))

    def test_missing_security_settings_fail_closed(self):
        for override in ({'proxy': {'image': ''}},
                         {'proxy': {'image': IMAGE, 'trustedProxyCIDRs': []}},
                         {'proxy': {'image': IMAGE, 'trustedProxyCIDRs': ['10.40.0.0/0']}},
                         {'proxy': {'image': IMAGE, 'trustedProxyCIDRs': ['10.40.0.0/99']}},
                         {'proxy': {'image': IMAGE, 'trustedProxyCIDRs': ['10.40.999.0/16']}},
                         {'proxy': {'image': IMAGE, 'trustedProxyCIDRs': ['10.40.0.0/00']}},
                         {'proxy': {'image': 'quay.io/oauth2-proxy/oauth2-proxy:v7.15.5@sha256:' + 'a' * 64,
                                    'trustedProxyCIDRs': ['10.40.0.0/16']}},
                         {'proxy': {'image': 'quay.io/oauth2-proxy/oauth2-proxy:v7.15.0',
                                    'trustedProxyCIDRs': ['10.40.0.0/16']}},
                         {'oauth': {'existingSecret': ''}}, {'clustersSecret': ''}):
            self.assertNotEqual(self.render(override).returncode, 0, override)


if __name__ == '__main__':
    unittest.main()
