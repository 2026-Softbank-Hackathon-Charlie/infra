#!/usr/bin/env python3
"""Offline checks of the app routing scripts (ADR 0036) against fake aws/kubectl/helm
calls: what they change, what they refuse, and that the tokens stay off argv and output."""
import base64
import contextlib
import importlib.util
import io
import json
from pathlib import Path
import shutil
import subprocess
import tempfile
import unittest
from unittest.mock import patch

ROOT = Path(__file__).resolve().parents[2]
TOKEN = 'eyJfake.platform-token.sig'
ENDPOINT = 'https://ABCDEF.gr7.ap-northeast-2.eks.amazonaws.com'


def load(name):
    spec = importlib.util.spec_from_file_location(name.replace('-', '_'), ROOT / 'scripts' / f'{name}.py')
    module = importlib.util.module_from_spec(spec)
    spec.loader.exec_module(module)
    return module


def done(args, stdout='', code=0, stderr=''):
    return subprocess.CompletedProcess(args, code, stdout, stderr)


class Calls:
    """Records every command; answer(args, input) returns a CompletedProcess."""

    def __init__(self, answer):
        self.answer, self.log = answer, []

    def __call__(self, args, input=None, check=False, **_):
        self.log.append((list(args), input))
        result = self.answer(list(args), input)
        if check and result.returncode:
            raise subprocess.CalledProcessError(result.returncode, args)
        return result

    def has(self, *words):
        return [a for a, _ in self.log if all(w in a for w in words)]


class KubeconfigTests(unittest.TestCase):
    def setUp(self):
        self.mod = load('put-platform-kubeconfig')
        temp = tempfile.TemporaryDirectory()
        self.addCleanup(temp.cleanup)
        self.root = Path(temp.name)
        (self.root / 'config').mkdir()
        shutil.copyfile(ROOT / 'config/project.json', self.root / 'config/project.json')
        self.tunnel = self.root / '.generated/access/workload.kubeconfig'
        self.tunnel.parent.mkdir(parents=True)
        self.tunnel.write_text('{}')
        self.manifest = self.root / 'clusters/aws-dev-workload/hopcharlie-platform-access.yaml'
        self.manifest.parent.mkdir(parents=True)
        shutil.copyfile(ROOT / 'clusters/aws-dev-workload/hopcharlie-platform-access.yaml', self.manifest)
        self.account, self.parameter, self.whoami, self.stored = '525091405886', '', None, None
        self.secret_access = 'no'

    def answer(self, args, stdin):
        if args[0] == 'aws':
            if 'get-caller-identity' in args:
                return done(args, self.account + '\n')
            if 'describe-cluster' in args:
                return done(args, json.dumps({'cluster': {'endpoint': ENDPOINT, 'certificateAuthority': {'data': 'Q0E='}}}))
            if 'describe-parameters' in args:
                assert 'Key=Path,Values=/hopcharlie/kube' in args
                names = ['/hopcharlie/kube/gke'] + ([self.parameter] if self.parameter else [])
                return done(args, json.dumps({'Parameters': [{'Name': n, 'LastModifiedUser': 'someone'} for n in names]}))
            if 'put-parameter' in args:
                value = args[args.index('--value') + 1]
                self.stored = Path(value.removeprefix('file://')).read_text()
                return done(args, '3\n')
        if 'view' in args:
            return done(args, json.dumps({'clusters': [{'cluster': {
                'server': 'https://127.0.0.1:11443', 'tls-server-name': ENDPOINT[8:].lower()}}]}))
        if 'jsonpath={.data.token}' in args:
            return done(args, base64.b64encode(TOKEN.encode()).decode())
        if 'whoami' in args:
            return done(args, self.whoami or 'system:serviceaccount:charlie-system:hopcharlie-platform')
        if 'can-i' in args:
            rule = args[args.index('can-i') + 1:]
            yes = rule[0] in ('get', 'list') and not any(r in rule for r in ('secrets', 'configmaps'))
            yes = yes or (rule[:2] == ['get', 'secrets'] and self.secret_access == 'yes')
            return done(args, 'yes' if yes else 'no - no RBAC policy matched', 0 if yes else 1)
        if 'diff' in args:
            return done(args, code=1)
        return done(args, 'ok')

    def invoke(self, *argv):
        calls, out = Calls(self.answer), io.StringIO()
        with patch.object(self.mod, 'ROOT', self.root), patch.object(self.mod, 'MANIFEST', self.manifest), \
                patch.object(self.mod.subprocess, 'run', calls), patch.object(self.mod.shutil, 'which', lambda t: t), \
                patch.object(self.mod.time, 'sleep', lambda s: None), patch.dict('os.environ', {'AWS_PROFILE': 'hopcharlie'}), \
                contextlib.redirect_stdout(out), contextlib.redirect_stderr(out):
            code = self.mod.main(list(argv))
        for args, _ in calls.log:
            self.assertFalse(any(TOKEN in a for a in args), args)
        self.assertNotIn(TOKEN, out.getvalue())
        return code, calls, out.getvalue()

    def test_preview_changes_nothing(self):
        code, calls, out = self.invoke()
        self.assertEqual(code, 0, out)
        self.assertFalse(calls.has('apply') or calls.has('put-parameter'))
        self.assertTrue(calls.has('diff', '--server-side'))

    def test_other_account_is_refused_first(self):
        self.account = '111111111111'
        code, calls, out = self.invoke('--apply')
        self.assertEqual(code, 1)
        self.assertEqual(len(calls.log), 1, calls.log)
        self.assertIn('not the hackathon account', out)

    def test_existing_parameter_needs_replace(self):
        self.parameter = '/hopcharlie/kube/eks'
        code, calls, out = self.invoke('--apply')
        self.assertEqual(code, 1)
        self.assertFalse(calls.has('apply'))
        code, calls, out = self.invoke('--apply', '--replace')
        self.assertEqual(code, 0, out)
        self.assertTrue(calls.has('put-parameter', '--overwrite'))

    def test_apply_stores_the_kubeconfig(self):
        code, calls, out = self.invoke('--apply')
        self.assertEqual(code, 0, out)
        stored = json.loads(self.stored)
        self.assertEqual(stored['current-context'], 'eks-hopcharlie')
        self.assertEqual(stored['clusters'][0]['cluster'], {'server': ENDPOINT, 'certificate-authority-data': 'Q0E='})
        self.assertEqual(stored['users'][0]['user'], {'token': TOKEN})
        put = calls.has('put-parameter')[0]
        self.assertEqual(put[put.index('--type') + 1], 'SecureString')
        self.assertNotIn('--overwrite', put)
        self.assertFalse(list((self.root / '.generated/platform-kube').rglob('*.kubeconfig')))

    def test_broader_token_is_not_stored(self):
        self.secret_access = 'yes'
        code, calls, out = self.invoke('--apply')
        self.assertEqual(code, 1)
        self.assertFalse(calls.has('put-parameter'))
        self.whoami, self.secret_access = 'system:serviceaccount:kube-system:other', 'no'
        code, calls, out = self.invoke('--apply')
        self.assertEqual(code, 1)
        self.assertFalse(calls.has('put-parameter'))


class RegisterTests(unittest.TestCase):
    def setUp(self):
        self.mod = load('register-hopcharlie-apps')
        temp = tempfile.TemporaryDirectory()
        self.addCleanup(temp.cleanup)
        self.dir = Path(temp.name)
        self.kubeconfig = self.dir / 'management.kubeconfig'
        self.kubeconfig.write_text('{}')
        self.token_file = self.dir / 'token'
        self.token_file.write_text(TOKEN + '\n')
        self.existing, self.app = set(), {'metadata': {'name': 'charlie-management-hopcharlie-apps'}, 'spec': {}}

    def answer(self, args, stdin):
        if '--ignore-not-found' in args:
            kind, name = args[args.index('get') + 1:args.index('get') + 3]
            return done(args, f'{kind}/{name}' if name in self.existing else '')
        if args[0] == 'git':
            return done(args, 'same\n' if 'rev-parse' in args else '')
        if 'get' in args and 'json' in args:
            return done(args, json.dumps(self.app))
        return done(args, 'created')

    def invoke(self, *argv):
        calls, out = Calls(self.answer), io.StringIO()
        with patch.object(self.mod.subprocess, 'run', calls), patch.object(self.mod.shutil, 'which', lambda t: t), \
                patch.object(self.mod, 'render', lambda argo: None), patch.object(self.mod.time, 'sleep', lambda s: None), \
                contextlib.redirect_stdout(out), contextlib.redirect_stderr(out):
            code = self.mod.main([*argv, '--kubeconfig', str(self.kubeconfig)])
        for args, _ in calls.log:
            self.assertFalse(any(TOKEN in a for a in args), args)
        self.assertNotIn(TOKEN, out.getvalue())
        return code, calls, out.getvalue()

    def test_pick_drops_automation(self):
        docs = [{'kind': 'AppProject', 'metadata': {'name': 'charlie-hopcharlie-apps'}},
                {'kind': 'AppProject', 'metadata': {'name': 'charlie-addons-management'}},
                {'kind': 'Application', 'metadata': {'name': 'charlie-management-hopcharlie-platform'}, 'spec': {}},
                {'kind': 'Application', 'metadata': {'name': 'charlie-management-hopcharlie-apps'}, 'operation': {},
                 'spec': {'syncPolicy': {'automated': {'prune': False}, 'syncOptions': ['ServerSideApply=true']}}}]
        project, app = self.mod.pick(docs)
        self.assertEqual(project['metadata']['name'], 'charlie-hopcharlie-apps')
        self.assertEqual(app['spec']['syncPolicy'], {'syncOptions': ['ServerSideApply=true']})
        self.assertNotIn('operation', app)
        with self.assertRaises(self.mod.Refused):
            self.mod.pick(docs[1:])

    def test_repository_token_goes_on_stdin(self):
        code, calls, out = self.invoke('add-repository', '--token-file', str(self.token_file))
        self.assertEqual(code, 0, out)
        creates = [(a, i) for a, i in calls.log if 'create' in a]
        self.assertEqual([a[-3:] for a, _ in creates], [['--dry-run=server', '-f', '-'], ['create', '-f', '-']])
        secret = json.loads(creates[-1][1])
        self.assertEqual(secret['metadata']['labels'], {'argocd.argoproj.io/secret-type': 'repository'})
        self.assertEqual(secret['stringData'], {'type': 'git', 'url': 'https://github.com/2026-Softbank-Hackathon-Charlie/gitops.git',
                                                'username': 'x-access-token', 'password': TOKEN})

    def test_existing_objects_are_not_replaced(self):
        self.existing = {'charlie-gitops-repository'}
        code, calls, out = self.invoke('add-repository', '--token-file', str(self.token_file))
        self.assertEqual(code, 1)
        self.assertFalse(calls.has('create'))
        self.existing = {'charlie-management-hopcharlie-apps'}
        code, calls, out = self.invoke('create')
        self.assertEqual(code, 1)
        self.assertFalse(calls.has('create'))

    def test_sync_needs_credential_and_no_pending_operation(self):
        code, calls, out = self.invoke('sync')
        self.assertEqual(code, 1)
        self.assertIn('add-repository', out)
        self.existing, self.app['operation'] = {'charlie-gitops-repository'}, {'sync': {}}
        code, calls, out = self.invoke('sync')
        self.assertEqual(code, 1)
        self.assertFalse(calls.has('patch'))

    def test_sync_does_not_report_an_old_result(self):
        # The controller never picks the request up: the last operation's Succeeded must not count.
        self.existing = {'charlie-gitops-repository'}
        self.app['metadata']['resourceVersion'] = '42'
        self.app['status'] = {'operationState': {'phase': 'Succeeded', 'startedAt': '2026-11-04T00:00:00Z'}}
        code, calls, out = self.invoke('sync')
        self.assertEqual(code, 1)
        self.assertIn('not started', out)
        patch = json.loads(calls.has('patch')[0][-1])
        self.assertEqual(patch['metadata'], {'resourceVersion': '42'})
        self.assertEqual(patch['operation']['sync'], {'syncOptions': ['ServerSideApply=true']})


if __name__ == '__main__':
    unittest.main()
