#!/usr/bin/env python3
"""Run the Make entry points against fake AWS/kubectl; never contact a cluster."""
import importlib.util
import contextlib
import io
import json
import os
from pathlib import Path
import shutil
import subprocess
import sys
import tempfile
import unittest
from unittest.mock import patch

ROOT = Path(__file__).resolve().parents[2]
FAKE = r'''#!/usr/bin/env python3
import json, os, pathlib, sys
args = sys.argv[1:]
tool = pathlib.Path(sys.argv[0]).name
with open(os.environ['CALL_LOG'], 'a') as log:
    log.write(json.dumps([tool, *args]) + '\n')
def option(name):
    return args[args.index(name) + 1]
def emit(value):
    print(json.dumps(value))
scenario = os.environ.get('SCENARIO', '')
if tool == 'aws':
    assert option('--region') == 'ap-northeast-2'
    assert option('--profile') == os.environ.get('EXPECTED_PROFILE', 'charlie-admin')
    if scenario == 'cli-fail':
        print('fake expired authentication', file=sys.stderr)
        sys.exit(7)
    if 'get-caller-identity' in args:
        emit({'Account': '999999999999' if scenario == 'account' else '123456789012'})
    elif 'describe-cluster' in args:
        name = option('--name')
        emit({'cluster': {'arn': f'arn:aws:eks:ap-northeast-2:123456789012:cluster/{name}',
              'endpoint': 'https://example.eks.amazonaws.com', 'certificateAuthority': {'data': 'ZmFrZS1jYQ=='},
              'resourcesVpcConfig': {'vpcId': 'vpc-test'}}})
    elif 'describe-instances' in args:
        assert 'Name=tag:Project,Values=charlie' in args
        assert 'Name=tag:Environment,Values=dev' in args
        assert 'Name=tag:Component,Values=access' in args
        assert 'Name=vpc-id,Values=vpc-test' in args
        bridge = {'InstanceId': 'i-test', 'VpcId': 'vpc-other' if scenario == 'vpc' else 'vpc-test', 'State': {'Name': 'running'}}
        bridges = [] if scenario == 'none' else [bridge, bridge] if scenario == 'multiple' else [bridge]
        emit({'Reservations': [{'Instances': bridges}]})
    elif 'describe-instance-information' in args:
        emit({'InstanceInformationList': [{'InstanceId': 'i-test', 'PingStatus': 'Offline' if scenario == 'offline' else 'Online'}]})
    elif 'update-kubeconfig' in args:
        name = option('--name')
        pathlib.Path(option('--kubeconfig')).write_text(json.dumps({'clusters': [{
            'name': f'arn:aws:eks:ap-northeast-2:123456789012:cluster/{name}', 'cluster': {
            'server': 'https://example.eks.amazonaws.com', 'certificate-authority-data': 'wrong' if scenario == 'ca' else 'ZmFrZS1jYQ=='}}]}))
    else:
        assert 'start-session' in args
        assert option('--target') == 'i-test'
        assert option('--document-name') == 'AWS-StartPortForwardingSessionToRemoteHost'
elif tool == 'kubectl':
    assert '--kubeconfig' in args
    if 'view' in args:
        print(pathlib.Path(option('--kubeconfig')).read_text())
    elif 'get' in args:
        if scenario == 'ready-fail':
            print('fake Forbidden: pods access denied', file=sys.stderr)
            sys.exit(9)
        print('ok')
    else:
        assert 'port-forward' in args
        assert option('--address') == '127.0.0.1'
        assert option('--context') == 'charlie-dev-management'
        assert option('-n') == 'argocd'
'''


class AccessTests(unittest.TestCase):
    def setUp(self):
        temp = tempfile.TemporaryDirectory()
        self.addCleanup(temp.cleanup)
        self.root = Path(temp.name).resolve()
        (self.root / 'scripts').mkdir()
        (self.root / 'config').mkdir()
        for name in ('eks-access.py', 'project-config.py'):
            shutil.copyfile(ROOT / 'scripts' / name, self.root / 'scripts' / name)
        shutil.copyfile(ROOT / 'Makefile', self.root / 'Makefile')
        shutil.copyfile(ROOT / 'config/project.json', self.root / 'config/project.json')
        (self.root / 'config/account.local.json').write_text(json.dumps({'aws_account_id': '123456789012'}))
        self.bin = self.root / 'bin'
        self.bin.mkdir()
        for tool in ('aws', 'kubectl', 'session-manager-plugin'):
            target = self.bin / tool
            target.write_text(FAKE)
            target.chmod(0o755)
        self.log = self.root / 'calls.jsonl'
        self.global_config = self.root / 'global-kubeconfig'
        self.global_config.write_text('do not touch\n')
        # No sockets or live tunnels in tests, including the free-port preflight.
        self.runner = self.root / 'python-no-sockets.py'
        self.runner.write_text(
            "import runpy, sys\nfrom unittest.mock import patch\n"
            "sys.argv = sys.argv[1:]\nwith patch('socket.socket'):\n"
            "    runpy.run_path(sys.argv[0], run_name='__main__')\n")
        self.env = {key: os.environ[key] for key in ('PATH', 'HOME') if key in os.environ}
        self.env.update(PATH=str(self.bin) + os.pathsep + self.env['PATH'],
                        CALL_LOG=str(self.log), KUBECONFIG=str(self.global_config))

    def invoke(self, target='aws-tunnel', *variables, expected=0, **env):
        result = subprocess.run(['make', '--no-print-directory', target, f'PYTHON={sys.executable} {self.runner}', *variables],
                                cwd=self.root, env=dict(self.env, **env), text=True, capture_output=True)
        if expected == 0:
            self.assertEqual(result.returncode, 0, result.stdout + result.stderr)
        else:
            self.assertNotEqual(result.returncode, 0, result.stdout + result.stderr)
        self.assertEqual(self.global_config.read_text(), 'do not touch\n')
        return result

    def calls(self):
        return [json.loads(line) for line in self.log.read_text().splitlines()] if self.log.exists() else []

    def kube_path(self, kind='management'):
        return self.root / f'.generated/access/{kind}.kubeconfig'

    def test_two_clusters_and_argo(self):
        for kind, number in (('management', 10443), ('workload', 11443)):
            self.invoke('aws-tunnel', f'CLUSTER={kind}')
            path = self.kube_path(kind)
            document = json.loads(path.read_text())
            cluster = document['clusters'][0]['cluster']
            self.assertEqual(cluster['server'], f'https://127.0.0.1:{number}')
            self.assertEqual(cluster['tls-server-name'], 'example.eks.amazonaws.com')
            self.assertEqual(cluster['certificate-authority-data'], 'ZmFrZS1jYQ==')
            self.assertNotIn('insecure-skip-tls-verify', cluster)
            self.assertEqual(path.stat().st_mode & 0o777, 0o600)
            self.assertEqual(path.parent.stat().st_mode & 0o777, 0o700)
            call = self.calls()[-1]
            params = json.loads(call[call.index('--parameters') + 1])
            self.assertEqual(params, {'host': ['example.eks.amazonaws.com'], 'portNumber': ['443'], 'localPortNumber': [str(number)]})
        self.invoke('argo-port-forward')
        self.assertEqual(self.calls()[-1][-2:], ['svc/argocd-server', '8080:443'])
        self.assertIn(str(self.kube_path()), self.calls()[-1])

    def test_overrides_and_region(self):
        result = self.invoke('aws-tunnel', 'AWS_PROFILE=operator', 'EKS_LOCAL_PORT=12443',
                             EXPECTED_PROFILE='operator', AWS_REGION='us-east-1')
        self.assertIn('Ignoring AWS_REGION', result.stderr)
        self.invoke('argo-port-forward', 'ARGO_LOCAL_PORT=9090')
        self.assertEqual(self.calls()[-1][-1], '9090:443')
        doc = json.loads(self.kube_path().read_text())
        self.assertEqual(doc['users'][0]['user']['exec']['env'][0]['value'], 'operator')
        self.assertEqual(doc['clusters'][0]['cluster']['server'], 'https://127.0.0.1:12443')
        previous_forwards = sum('port-forward' in c for c in self.calls())
        self.invoke('argo-port-forward', 'AWS_PROFILE=other', expected=1)
        self.assertEqual(sum('port-forward' in c for c in self.calls()), previous_forwards)

    def test_cloud_errors_never_start_a_session_or_replace_config(self):
        self.invoke()
        original = self.kube_path().read_bytes()
        for scenario in ('account', 'none', 'multiple', 'vpc', 'offline', 'ca', 'cli-fail'):
            with self.subTest(scenario=scenario):
                self.log.unlink()
                result = self.invoke(expected=1, SCENARIO=scenario)
                self.assertFalse(any('start-session' in c for c in self.calls()))
                self.assertEqual(self.kube_path().read_bytes(), original)
                if scenario == 'cli-fail':
                    self.assertIn('fake expired authentication', result.stderr)

    def test_preflight_rejections_do_not_call_aws(self):
        for variables in (('CLUSTER=nope',), ('EKS_LOCAL_PORT=0',), ('EKS_LOCAL_PORT=65536',),
                          ('EKS_LOCAL_PORT=abc',), ('EKS_LOCAL_PORT=443',)):
            self.invoke('aws-tunnel', *variables, expected=1)
        self.invoke('argo-port-forward', 'CLUSTER=workload', expected=1)
        self.invoke('argo-port-forward', expected=1)
        self.assertEqual(self.calls(), [])

    def test_argo_auth_failure_and_unsafe_config(self):
        self.invoke()
        result = self.invoke('argo-port-forward', expected=1, SCENARIO='ready-fail')
        self.assertIn('Forbidden', result.stderr)
        self.assertFalse(any('port-forward' in c for c in self.calls()))
        document = json.loads(self.kube_path().read_text())
        document['clusters'][0]['cluster']['insecure-skip-tls-verify'] = True
        self.kube_path().write_text(json.dumps(document))
        self.invoke('argo-port-forward', expected=1)
        self.assertFalse(any('port-forward' in c for c in self.calls()))

    def test_busy_port_fails_before_cloud_calls(self):
        spec = importlib.util.spec_from_file_location('eks_access', self.root / 'scripts/eks-access.py')
        module = importlib.util.module_from_spec(spec)
        spec.loader.exec_module(module)
        with patch.object(module.socket, 'socket') as socket_mock:
            socket_mock.return_value.__enter__.return_value.bind.side_effect = OSError('busy')
            with self.assertRaisesRegex(module.AccessError, 'unavailable'):
                module.free_port(10443)
        with patch.object(module.shutil, 'which', return_value=None), \
                patch.object(sys, 'argv', ['eks-access.py', 'tunnel']), \
                patch.dict(os.environ, {}, clear=True), contextlib.redirect_stderr(io.StringIO()):
            self.assertEqual(module.main(), 1)
        self.assertEqual(self.calls(), [])


if __name__ == '__main__':
    unittest.main()
