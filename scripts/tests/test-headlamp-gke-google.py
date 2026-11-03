#!/usr/bin/env python3
"""Security boundaries for the optional Google-authenticated Headlamp."""
import json
import base64
import importlib.util
import os
import re
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
        for flag in ('-in-cluster=false', '-listen-addr=127.0.0.1',
                     '-proxy-auth=true', '-proxy-auth-token-header=X-Forwarded-Access-Token',
                     '-proxy-auth-group-header=X-Forwarded-Groups'):
            self.assertIn(flag, headlamp['args'])
        self.assertNotIn('ports', headlamp)
        self.assertFalse(any(arg.startswith('-base-url') for arg in headlamp['args']))
        for probe in ('readinessProbe', 'livenessProbe'):
            self.assertEqual(headlamp[probe]['exec']['command'][-1], 'http://127.0.0.1:4466/')
        args = proxy['args']
        self.assertEqual([arg for arg in args if arg.startswith('--cookie-path=')],
                         ['--cookie-path=/'])
        for flag in ('--alpha-config=/etc/oauth2-proxy-config/alpha.yaml', '--proxy-prefix=/gke/oauth2',
                     '--redirect-url=https://headlamp.hopcharlie.dev/gke/oauth2/callback',
                     '--authenticated-emails-file=/etc/oauth2-proxy/allowed-emails',
                     '--cookie-name=__Host-headlamp_gke',
                     '--cookie-refresh=5m', '--cookie-expire=168h',
                     '--cookie-secure=true', '--cookie-httponly=true',
                     '--reverse-proxy=true', '--trusted-proxy-ip=10.40.0.0/16',
                     '--whitelist-domain=headlamp.hopcharlie.dev', '--request-logging=false'):
            self.assertIn(flag, args)
        self.assertFalse(any(arg.startswith('--email-domain') or arg.startswith('--trusted-ip') for arg in args))
        removed_flags = ('provider', 'http-address', 'upstream', 'pass-host-header', 'scope', 'prompt',
                         'pass-access-token', 'pass-authorization-header', 'pass-user-headers',
                         'pass-basic-auth', 'skip-auth-strip-headers', 'cookie-domain')
        self.assertFalse(any(arg.split('=')[0] in {'--' + name for name in removed_flags}
                             for arg in args))
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
        policy = next(x for x in docs if x['kind'] == 'NetworkPolicy')
        self.assertEqual(policy['spec']['podSelector']['matchLabels']['app.kubernetes.io/name'], 'headlamp-gke-google')
        self.assertEqual(policy['spec']['ingress'][0]['ports'][0]['port'], 4180)
        self.assertFalse(any(x['kind'] == 'Secret' for x in docs))

    def test_alpha_config_injects_access_token_and_replaces_spoofed_headers(self):
        result = self.render()
        self.assertEqual(result.returncode, 0, result.stderr)
        docs = [x for x in yaml.safe_load_all(result.stdout) if x]
        config = next(x for x in docs if x['kind'] == 'ConfigMap')
        alpha = yaml.safe_load(config['data']['alpha.yaml'])
        self.assertEqual(alpha['server'], {'bindAddress': '0.0.0.0:4180'})
        self.assertEqual(alpha['upstreamConfig']['upstreams'], [{
            'id': 'headlamp', 'path': '/', 'uri': 'http://127.0.0.1:4466',
            'passHostHeader': False, 'proxyWebSockets': True}])
        self.assertEqual(alpha['providers'], [{
            'id': 'google=${OAUTH2_PROXY_CLIENT_ID}', 'provider': 'google',
            'clientID': '${OAUTH2_PROXY_CLIENT_ID}', 'clientSecret': '${OAUTH2_PROXY_CLIENT_SECRET}',
            'scope': 'openid email profile https://www.googleapis.com/auth/cloud-platform',
            'loginURLParameters': [{'name': 'prompt', 'default': ['consent']}]}])
        headers = {header['name']: header for header in alpha['injectRequestHeaders']}
        claims = {'Authorization': 'access_token', 'X-Forwarded-Access-Token': 'access_token',
                  'X-Forwarded-User': 'user', 'X-Forwarded-Email': 'email',
                  'X-Forwarded-Groups': 'groups', 'X-Forwarded-Preferred-Username': 'preferred_username'}
        self.assertEqual(set(headers), set(claims) | {'Cookie'})
        self.assertEqual(headers['Cookie'], {
            'name': 'Cookie', 'preserveRequestValue': False, 'values': []})
        for name, claim in claims.items():
            source = {'claim': claim}
            if name == 'Authorization':
                source['prefix'] = 'Bearer '
            self.assertEqual(headers[name], {
                'name': name, 'preserveRequestValue': False, 'values': [{'claimSource': source}]})
        self.assertNotIn('id_token', config['data']['alpha.yaml'])
        deployment = next(x for x in docs if x['kind'] == 'Deployment')
        template = deployment['spec']['template']
        self.assertRegex(template['metadata']['annotations']['checksum/proxy-config'], r'^[a-f0-9]{64}$')
        volumes = {volume['name']: volume for volume in template['spec']['volumes']}
        self.assertEqual(volumes['proxy-config']['configMap']['name'], config['metadata']['name'])
        proxy = next(x for x in template['spec']['containers'] if x['name'] == 'oauth2-proxy')
        self.assertIn({'name': 'proxy-config', 'mountPath': '/etc/oauth2-proxy-config', 'readOnly': True},
                      proxy['volumeMounts'])

    def test_only_cluster_api_and_oauth_reach_google_backend(self):
        for cluster, overrides in (('charlie-dev-gcp-workload', None),
                                   ('other-gke', {'clusterName': 'other-gke'}),
                                   ('gke.prod', {'clusterName': 'gke.prod'})):
            with self.subTest(cluster=cluster):
                result = self.render(overrides)
                self.assertEqual(result.returncode, 0, result.stderr)
                docs = [x for x in yaml.safe_load_all(result.stdout) if x]
                pod = next(x for x in docs if x['kind'] == 'Deployment')['spec']['template']['spec']
                proxy = next(x for x in pod['containers'] if x['name'] == 'oauth2-proxy')
                api_routes = [arg.removeprefix('--api-route=') for arg in proxy['args']
                              if arg.startswith('--api-route=')]
                self.assertEqual(api_routes, ['^/clusters/' + cluster.replace('.', r'\.') + '(/|$)'])
                self.assertIsNotNone(re.match(api_routes[0], f'/clusters/{cluster}/api/v1/pods'))
                self.assertIsNotNone(re.match(api_routes[0], f'/clusters/{cluster}'))
                self.assertIsNone(re.match(api_routes[0], f'/clusters/{cluster}-other/api'))
                if '.' in cluster:
                    self.assertIsNone(re.match(api_routes[0], '/clusters/gkeXprod/api'))
                ingress = next(x for x in yaml.safe_load_all(result.stdout)
                               if x and x['kind'] == 'Ingress')
                paths = ingress['spec']['rules'][0]['http']['paths']
                encoded = base64.urlsafe_b64encode(cluster.encode()).decode().rstrip('=')
                self.assertEqual([(x['path'], x['pathType']) for x in paths],
                                 [(f'/encoded-clusters/{encoded}/clear-token', 'Exact'),
                                  (f'/clusters/{cluster}', 'Prefix'),
                                  ('/gke/oauth2', 'Prefix'), ('/gke', 'Prefix')])
                logout_service = paths[0]['backend']['service']
                self.assertEqual(logout_service['port'], {'name': 'use-annotation'})
                logout = json.loads(ingress['metadata']['annotations'][
                    'alb.ingress.kubernetes.io/actions.' + logout_service['name']])
                self.assertEqual(logout, {
                    'type': 'redirect',
                    'redirectConfig': {'protocol': 'HTTPS', 'port': '443', 'host': '#{host}',
                                       'path': '/gke/oauth2/sign_out', 'query': 'rd=%2F',
                                       'statusCode': 'HTTP_302'}})
                for path in paths[1:3]:
                    self.assertEqual(path['backend']['service'],
                                     {'name': 'headlamp-gke-google', 'port': {'number': 80}})
                action_service = paths[3]['backend']['service']
                self.assertEqual(action_service['port'], {'name': 'use-annotation'})
                action = json.loads(ingress['metadata']['annotations'][
                    'alb.ingress.kubernetes.io/actions.' + action_service['name']])
                self.assertEqual(action, {
                    'type': 'redirect',
                    'redirectConfig': {'protocol': 'HTTPS', 'port': '443', 'host': '#{host}',
                                       'path': f'/c/{cluster}/', 'query': '#{query}',
                                       'statusCode': 'HTTP_302'}})

    def test_cluster_name_must_be_a_safe_path_segment(self):
        for cluster in ('', '.', '..', '../gke', 'gke/other', 'gke%2fother',
                        'gke?query=1', 'gke#fragment', 'gke name', 'gke\\other'):
            with self.subTest(cluster=cluster):
                result = self.render({'clusterName': cluster})
                self.assertNotEqual(result.returncode, 0, cluster)
                self.assertIn('clusterName', result.stderr)

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
