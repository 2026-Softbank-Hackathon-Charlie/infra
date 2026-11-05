#!/usr/bin/env python3
"""Offline contracts for temporary CNPG RBAC; subprocess is always replaced."""
import contextlib
import copy
import importlib.util
import io
import json
from pathlib import Path
import subprocess
import unittest
from unittest.mock import patch

ROOT = Path(__file__).resolve().parents[2]
SCRIPT = ROOT / 'scripts/db-migration-access.py'
NAMESPACE = 'app-example'
CLUSTER = 'postgres-example'
SESSION = 'transfer-47'
NAME = 'db-migration-' + SESSION
LEAK = 'eyJfake.private-kubeconfig-credential.signature'


class FakeKubectl:
    def __init__(self):
        self.calls = []
        self.store = {}
        self.fail_binding = False
        self.binding_created_on_failure = False
        self.before_delete = None
        self.sequence = 0
        self.add({'apiVersion': 'v1', 'kind': 'Namespace', 'metadata': {'name': NAMESPACE}})
        self.add({'apiVersion': 'v1', 'kind': 'ServiceAccount',
                  'metadata': {'name': 'hopcharlie-platform', 'namespace': 'charlie-system'}})
        self.add({'apiVersion': 'postgresql.cnpg.io/v1', 'kind': 'Cluster',
                  'metadata': {'name': CLUSTER, 'namespace': NAMESPACE}})

    def add(self, document):
        document = copy.deepcopy(document)
        self.sequence += 1
        metadata = document['metadata']
        metadata.setdefault('uid', f'uid-{self.sequence}')
        metadata.setdefault('resourceVersion', str(self.sequence))
        self.store[self.key(document)] = document
        return document

    @staticmethod
    def key(document):
        metadata = document['metadata']
        return document['kind'].lower(), metadata.get('namespace'), metadata['name']

    def run(self, args, input=None, **kwargs):
        args = list(args)
        self.calls.append((args, input))
        assert args[0] == 'kubectl', args
        assert args[args.index('--context') + 1] == 'operator-context', args
        assert args[args.index('--kubeconfig') + 1] == 'operator.kubeconfig', args
        assert kwargs['capture_output'] and kwargs['text'], kwargs

        def done(document=None, code=0, stderr=''):
            output = json.dumps(document) if document is not None else ''
            return subprocess.CompletedProcess(args, code, output, stderr)

        if 'get' in args:
            index = args.index('get')
            kind, name = args[index + 1:index + 3]
            kind = {'clusters.postgresql.cnpg.io': 'cluster',
                    'roles.rbac.authorization.k8s.io': 'role',
                    'rolebindings.rbac.authorization.k8s.io': 'rolebinding'}.get(kind, kind)
            namespace = args[args.index('--namespace') + 1] if '--namespace' in args else None
            value = self.store.get((kind, namespace, name))
            if value is None and '--ignore-not-found' not in args:
                return done(code=1, stderr='NotFound: ' + LEAK)
            return done(value)
        if 'create' in args:
            assert args[args.index('-f') + 1] == '-', args
            document = json.loads(input)
            assert document['kind'] in ('Role', 'RoleBinding'), document
            if self.key(document) in self.store:
                return done(code=1, stderr='AlreadyExists: ' + LEAK)
            if document['kind'] == 'RoleBinding' and self.fail_binding:
                if self.binding_created_on_failure:
                    self.add(document)
                return done(code=1, stderr='Forbidden: ' + LEAK)
            return done(self.add(document))
        if 'delete' in args:
            assert args[args.index('-f') + 1] == '-', args
            uri = args[args.index('--raw') + 1]
            parts = uri.split('/')
            assert parts[:5] == ['', 'apis', 'rbac.authorization.k8s.io', 'v1', 'namespaces'], uri
            assert len(parts) == 8, uri
            namespace, resource, name = parts[5:]
            assert resource in ('roles', 'rolebindings'), resource
            key = (resource[:-1], namespace, name)
            body = json.loads(input)
            assert body['apiVersion'] == 'v1' and body['kind'] == 'DeleteOptions', body
            assert set(body) == {'apiVersion', 'kind', 'preconditions'}, body
            assert set(body['preconditions']) == {'uid', 'resourceVersion'}, body
            if self.before_delete:
                hook, self.before_delete = self.before_delete, None
                hook(key)
            actual = self.store.get(key)
            if actual is None:
                return done(code=1, stderr='NotFound: ' + LEAK)
            if any(actual['metadata'][field] != value for field, value in body['preconditions'].items()):
                return done(code=1, stderr='Conflict: ' + LEAK)
            del self.store[key]
            return done({'kind': 'Status', 'status': 'Success'})
        raise AssertionError(f'Unexpected command: {args}')

    def mutations(self, verb=None):
        return [(args, body) for args, body in self.calls
                if any(word in args for word in ((verb,) if verb else ('create', 'delete', 'apply', 'replace', 'patch')))]


class MigrationAccessTests(unittest.TestCase):
    def setUp(self):
        self.assertTrue(SCRIPT.is_file(), 'The temporary CNPG access helper has not been implemented.')
        spec = importlib.util.spec_from_file_location('db_migration_access', SCRIPT)
        self.mod = importlib.util.module_from_spec(spec)
        spec.loader.exec_module(self.mod)
        self.kube = FakeKubectl()

    def invoke(self, command=None, **overrides):
        values = {'context': 'operator-context', 'kubeconfig': 'operator.kubeconfig',
                  'namespace': NAMESPACE, 'cluster': CLUSTER, 'session': SESSION}
        values.update(overrides)
        argv = [] if command is None else [command]
        for key, value in values.items():
            argv.append('--' + key + '=' + value)
        stdout, stderr = io.StringIO(), io.StringIO()
        with patch.object(self.mod.subprocess, 'run', self.kube.run), \
                contextlib.redirect_stdout(stdout), contextlib.redirect_stderr(stderr):
            code = self.mod.main(argv)
        self.assertNotIn(LEAK, stdout.getvalue() + stderr.getvalue())
        for args, _ in self.kube.calls:
            self.assertFalse(any(LEAK in arg for arg in args), args)
            self.assertFalse(any(word in args for word in ('apply', 'replace', '--force', 'secrets', 'secret', 'config', 'token')))
        return code, stdout.getvalue(), stderr.getvalue()

    def rendered(self):
        code, output, error = self.invoke('render')
        self.assertEqual(code, 0, error)
        return json.loads(output)['items']

    def seed_grant(self):
        for document in self.rendered():
            self.kube.add(document)
        self.kube.calls.clear()

    def test_render_is_offline_and_exact(self):
        with patch('builtins.open', side_effect=AssertionError('render must not read local files')):
            code, output, error = self.invoke()
        self.assertEqual(code, 0, error)
        self.assertEqual(self.kube.calls, [])
        self.assertEqual(error, '')
        document = json.loads(output)
        self.assertEqual((document['apiVersion'], document['kind']), ('v1', 'List'))
        role, binding = document['items']
        self.assertEqual([role['kind'], binding['kind']], ['Role', 'RoleBinding'])
        self.assertEqual(role['rules'], [{'apiGroups': ['postgresql.cnpg.io'], 'resources': ['clusters'],
                                          'resourceNames': [CLUSTER], 'verbs': ['get', 'patch']}])
        self.assertEqual(binding['subjects'], [{'kind': 'ServiceAccount', 'name': 'hopcharlie-platform',
                                              'namespace': 'charlie-system'}])
        self.assertEqual(binding['roleRef'], {'apiGroup': 'rbac.authorization.k8s.io',
                                             'kind': 'Role', 'name': NAME})
        for resource in (role, binding):
            self.assertEqual(resource['metadata']['name'], NAME)
            self.assertEqual(resource['metadata']['namespace'], NAMESPACE)
            self.assertIn(SESSION, resource['metadata']['labels'].values())
            self.assertIn(CLUSTER, resource['metadata']['annotations'].values())
            self.assertNotIn('ttl', json.dumps(resource).lower())

    def test_names_and_protected_namespaces_are_checked_before_execution(self):
        invalid = [('namespace', value) for value in ('', 'App', 'app.name', 'x' * 64, 'bad-')]
        invalid += [('cluster', value) for value in ('', 'a/b', 'a,b', '*', 'x' * 64)]
        invalid += [('session', value) for value in ('', '-run', 'Run', 'run_id', 'x' * 51)]
        invalid += [('namespace', value) for value in (
            'argocd', 'cert-manager', 'charlie-apps', 'charlie-platform', 'charlie-system',
            'cnpg-system', 'default', 'headlamp', 'hopcharlie-db-network', 'observability',
            'kube-system', 'kube-public', 'kube-node-lease', 'kube-anything')]
        invalid += [('context', ''), ('kubeconfig', ''), ('context', 'unsafe\ncontext')]
        for field, value in invalid:
            with self.subTest(field=field, value=value):
                code, _, _ = self.invoke('grant', **{field: value})
                self.assertEqual(code, 1)
                self.assertEqual(self.kube.calls, [])

    def test_session_name_is_bounded_without_truncation(self):
        code, output, error = self.invoke(session='s' * 50)
        self.assertEqual(code, 0, error)
        name = json.loads(output)['items'][0]['metadata']['name']
        self.assertEqual(name, 'db-migration-' + 's' * 50)
        self.assertEqual(len(name), 63)

    def test_every_target_selector_is_required(self):
        values = {'context': 'operator-context', 'kubeconfig': 'operator.kubeconfig',
                  'namespace': NAMESPACE, 'cluster': CLUSTER, 'session': SESSION}
        for missing in values:
            with self.subTest(missing=missing), patch.object(self.mod.subprocess, 'run', self.kube.run), \
                    contextlib.redirect_stderr(io.StringIO()):
                argv = [value for key, item in values.items() if key != missing
                        for value in ('--' + key, item)]
                with self.assertRaises(SystemExit) as caught:
                    self.mod.main(argv)
                self.assertEqual(caught.exception.code, 2)
        self.assertEqual(self.kube.calls, [])

    def test_grant_preflights_then_creates_only_two_scoped_objects(self):
        code, output, error = self.invoke('grant')
        self.assertEqual(code, 0, error)
        self.assertIn('until', output.lower())
        self.assertIn('revoke', output.lower())
        self.assertEqual([args[args.index('get') + 1] for args, _ in self.kube.calls[:5]],
                         ['namespace', 'serviceaccount', 'clusters.postgresql.cnpg.io',
                          'roles.rbac.authorization.k8s.io', 'rolebindings.rbac.authorization.k8s.io'])
        created = [json.loads(body) for _, body in self.kube.mutations('create')]
        self.assertEqual([item['kind'] for item in created], ['Role', 'RoleBinding'])
        self.assertTrue(all(item['metadata']['namespace'] == NAMESPACE for item in created))
        self.assertEqual(len(self.kube.mutations()), 2)

    def test_grant_is_idempotent_for_identical_owned_objects(self):
        self.seed_grant()
        code, _, error = self.invoke('grant')
        self.assertEqual(code, 0, error)
        self.assertEqual(self.kube.mutations(), [])

    def test_grant_fills_only_the_missing_owned_object(self):
        for kind in ('role', 'rolebinding'):
            with self.subTest(missing=kind):
                self.kube = FakeKubectl()
                self.seed_grant()
                del self.kube.store[(kind, NAMESPACE, NAME)]
                code, _, error = self.invoke('grant')
                self.assertEqual(code, 0, error)
                created = [json.loads(body)['kind'].lower() for _, body in self.kube.mutations('create')]
                self.assertEqual(created, [kind])

    def test_grant_requires_existing_namespace_serviceaccount_and_cluster(self):
        for key in (('namespace', None, NAMESPACE),
                    ('serviceaccount', 'charlie-system', 'hopcharlie-platform'),
                    ('cluster', NAMESPACE, CLUSTER)):
            with self.subTest(missing=key):
                self.kube = FakeKubectl()
                del self.kube.store[key]
                code, _, _ = self.invoke('grant')
                self.assertEqual(code, 1)
                self.assertEqual(self.kube.mutations(), [])

    def test_charlie_db_is_allowed_only_if_the_poc_resources_exist(self):
        code, _, _ = self.invoke('grant', namespace='charlie-db')
        self.assertEqual(code, 1)
        self.assertEqual(self.kube.mutations(), [])
        self.kube.add({'apiVersion': 'v1', 'kind': 'Namespace', 'metadata': {'name': 'charlie-db'}})
        self.kube.add({'apiVersion': 'postgresql.cnpg.io/v1', 'kind': 'Cluster',
                       'metadata': {'name': CLUSTER, 'namespace': 'charlie-db'}})
        code, _, error = self.invoke('grant', namespace='charlie-db')
        self.assertEqual(code, 0, error)

    def test_foreign_binding_blocks_grant_before_creating_a_role(self):
        binding = self.rendered()[1]
        binding['subjects'][0]['namespace'] = 'other-namespace'
        self.kube.add(binding)
        self.kube.calls.clear()
        code, _, _ = self.invoke('grant')
        self.assertEqual(code, 1)
        self.assertEqual(self.kube.mutations(), [])

    def test_owned_objects_with_changed_authorization_or_ownership_are_refused(self):
        changes = [
            ('role', lambda obj: obj['rules'][0]['verbs'].append('create')),
            ('role', lambda obj: obj['rules'][0].update(resourceNames=['other-cluster'])),
            ('role', lambda obj: obj.update(aggregationRule={})),
            ('role', lambda obj: obj['metadata']['labels'].clear()),
            ('role', lambda obj: obj['metadata']['annotations'].clear()),
            ('role', lambda obj: obj['metadata'].update(ownerReferences=[{'uid': 'foreign'}])),
            ('rolebinding', lambda obj: obj['subjects'].append({'kind': 'Group', 'name': 'everyone'})),
            ('rolebinding', lambda obj: obj['roleRef'].update(name='foreign-role')),
            ('rolebinding', lambda obj: obj['metadata']['labels'].update({'hopcharlie.io/migration-session': 'other'})),
            ('rolebinding', lambda obj: obj['metadata'].update(finalizers=['foreign/finalizer'])),
        ]
        for command in ('grant', 'revoke'):
            for kind, change in changes:
                with self.subTest(command=command, kind=kind, change=change):
                    self.kube = FakeKubectl()
                    self.seed_grant()
                    change(self.kube.store[(kind, NAMESPACE, NAME)])
                    before = copy.deepcopy(self.kube.store)
                    code, _, _ = self.invoke(command)
                    self.assertEqual(code, 1)
                    self.assertEqual(self.kube.mutations(), [])
                    self.assertEqual(self.kube.store, before)

    def test_binding_failure_rolls_back_only_the_new_role(self):
        self.kube.fail_binding = True
        code, _, error = self.invoke('grant')
        self.assertEqual(code, 1)
        self.assertIn('rollback', error.lower())
        self.assertNotIn(('role', NAMESPACE, NAME), self.kube.store)
        self.assertEqual(len(self.kube.mutations('delete')), 1)

    def test_ambiguous_binding_creation_rolls_back_new_access_and_can_be_revoked(self):
        self.kube.fail_binding = True
        self.kube.binding_created_on_failure = True
        code, _, _ = self.invoke('grant')
        self.assertEqual(code, 1)
        self.assertNotIn(('role', NAMESPACE, NAME), self.kube.store)
        self.assertIn(('rolebinding', NAMESPACE, NAME), self.kube.store)
        code, _, error = self.invoke('revoke')
        self.assertEqual(code, 0, error)
        self.assertNotIn(('rolebinding', NAMESPACE, NAME), self.kube.store)

    def test_binding_failure_preserves_a_preexisting_role(self):
        self.kube.add(self.rendered()[0])
        before = copy.deepcopy(self.kube.store[('role', NAMESPACE, NAME)])
        self.kube.calls.clear()
        self.kube.fail_binding = True
        code, _, _ = self.invoke('grant')
        self.assertEqual(code, 1)
        self.assertEqual(self.kube.store[('role', NAMESPACE, NAME)], before)
        self.assertEqual(self.kube.mutations('delete'), [])

    def test_rollback_refuses_a_role_replaced_after_its_creation(self):
        self.kube.fail_binding = True
        original = self.kube.run

        def replace_after_failure(args, **kwargs):
            result = original(args, **kwargs)
            if 'create' in args and json.loads(kwargs['input'])['kind'] == 'RoleBinding':
                self.kube.store[('role', NAMESPACE, NAME)]['metadata']['uid'] = 'foreign-replacement'
            return result

        with patch.object(self.kube, 'run', replace_after_failure):
            code, _, error = self.invoke('grant')
        self.assertEqual(code, 1)
        self.assertIn('rollback', error.lower())
        self.assertEqual(self.kube.mutations('delete'), [])
        self.assertEqual(self.kube.store[('role', NAMESPACE, NAME)]['metadata']['uid'], 'foreign-replacement')

    def test_revoke_deletes_binding_before_role_with_identity_preconditions(self):
        self.seed_grant()
        before = copy.deepcopy(self.kube.store)
        code, output, error = self.invoke('revoke')
        self.assertEqual(code, 0, error)
        self.assertIn('revok', output.lower())
        deletions = self.kube.mutations('delete')
        self.assertEqual(len(deletions), 2)
        for (args, body), kind in zip(deletions, ('rolebinding', 'role')):
            uri = args[args.index('--raw') + 1]
            self.assertEqual(uri, f'/apis/{self.mod.RBAC_API}/namespaces/{NAMESPACE}/{kind}s/{NAME}')
            metadata = before[(kind, NAMESPACE, NAME)]['metadata']
            self.assertEqual(json.loads(body)['preconditions'],
                             {'uid': metadata['uid'], 'resourceVersion': metadata['resourceVersion']})
            self.assertNotIn((kind, NAMESPACE, NAME), self.kube.store)
        self.assertEqual(len(self.kube.mutations()), 2)

    def test_revoke_is_idempotent_when_all_or_either_object_is_absent(self):
        for missing in (('role',), ('rolebinding',), ('role', 'rolebinding')):
            with self.subTest(missing=missing):
                self.kube = FakeKubectl()
                self.seed_grant()
                for kind in missing:
                    del self.kube.store[(kind, NAMESPACE, NAME)]
                code, _, error = self.invoke('revoke')
                self.assertEqual(code, 0, error)
                self.assertNotIn(('role', NAMESPACE, NAME), self.kube.store)
                self.assertNotIn(('rolebinding', NAMESPACE, NAME), self.kube.store)

    def test_revoke_still_works_after_cluster_or_serviceaccount_removal(self):
        self.seed_grant()
        del self.kube.store[('cluster', NAMESPACE, CLUSTER)]
        del self.kube.store[('serviceaccount', 'charlie-system', 'hopcharlie-platform')]
        code, _, error = self.invoke('revoke')
        self.assertEqual(code, 0, error)
        self.assertEqual(len(self.kube.mutations('delete')), 2)

    def test_revoke_refuses_a_different_cluster_for_the_same_session(self):
        self.seed_grant()
        code, _, _ = self.invoke('revoke', cluster='different-cluster')
        self.assertEqual(code, 1)
        self.assertEqual(self.kube.mutations(), [])

    def test_revoke_detects_a_racing_replacement_or_edit(self):
        for field, value in (('uid', 'replacement'), ('resourceVersion', 'changed')):
            with self.subTest(field=field):
                self.kube = FakeKubectl()
                self.seed_grant()
                self.kube.before_delete = lambda key: self.kube.store[key]['metadata'].update({field: value})
                code, _, _ = self.invoke('revoke')
                self.assertEqual(code, 1)
                self.assertIn(('rolebinding', NAMESPACE, NAME), self.kube.store)
                self.assertIn(('role', NAMESPACE, NAME), self.kube.store)
                self.assertEqual(len(self.kube.mutations('delete')), 1)

    def test_revoke_treats_not_found_between_read_and_delete_as_success(self):
        self.seed_grant()
        self.kube.before_delete = lambda key: self.kube.store.pop(key)
        code, _, error = self.invoke('revoke')
        self.assertEqual(code, 0, error)
        self.assertNotIn(('role', NAMESPACE, NAME), self.kube.store)
        self.assertNotIn(('rolebinding', NAMESPACE, NAME), self.kube.store)

    def test_subprocess_replaces_non_utf8_plugin_output(self):
        args = self.mod.argparse.Namespace(context='operator-context', kubeconfig='operator.kubeconfig')
        result = subprocess.CompletedProcess(['kubectl'], 0, '', '')
        with patch.object(self.mod.subprocess, 'run', return_value=result) as run:
            self.mod.Kubectl(args).run('get', 'namespace', NAMESPACE)
        self.assertEqual(run.call_args.kwargs.get('encoding'), 'utf-8')
        self.assertEqual(run.call_args.kwargs.get('errors'), 'replace')

    def test_subprocess_failures_never_expose_raw_credentials(self):
        for failure in (OSError(LEAK), subprocess.TimeoutExpired(['kubectl'], 30, output=LEAK, stderr=LEAK)):
            with self.subTest(failure=type(failure).__name__), patch.object(self.kube, 'run', side_effect=failure):
                code, _, error = self.invoke('grant')
                self.assertEqual(code, 1)
                self.assertIn('suppressed', error)

    def test_invalid_json_never_exposes_raw_output(self):
        def invalid(args, **kwargs):
            return subprocess.CompletedProcess(args, 0, LEAK, LEAK)

        with patch.object(self.kube, 'run', invalid):
            code, _, error = self.invoke('grant')
        self.assertEqual(code, 1)
        self.assertIn('invalid JSON', error)


if __name__ == '__main__':
    unittest.main()
