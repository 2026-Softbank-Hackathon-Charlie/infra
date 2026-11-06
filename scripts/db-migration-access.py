#!/usr/bin/env python3
"""Render, grant or revoke temporary access to one existing CNPG Cluster.

Render (the default) makes no kubectl calls and reads no kubeconfig. Grant/revoke
are operator actions requiring separate execution approval. No TTL is enforced:
access remains until an operator explicitly revokes this session.
"""
import argparse
import json
import re
import subprocess
import sys

PROTECTED = {'argocd', 'cert-manager', 'charlie-apps', 'charlie-platform', 'charlie-system',
             'cnpg-system', 'default', 'headlamp', 'hopcharlie-db-network', 'observability'}
# Exact (namespace, CNPG Cluster) pairs that may be granted although the namespace is protected
# (infra ADR 0060). charlie-apps holds the GKE app workloads, and the live moimroom database is in it
# (infra ADR 0053). Nothing else in a protected namespace is allowed, and a kube-* namespace never is.
PROTECTED_EXCEPTIONS = {('charlie-apps', 'moimroom-db-gke')}
DNS_LABEL = re.compile(r'[a-z0-9](?:[a-z0-9-]*[a-z0-9])?')
RBAC_API = 'rbac.authorization.k8s.io/v1'
# get and patch for the migration engine. migrate is not a Kubernetes verb: the workload's
# ValidatingAdmissionPolicy hopcharlie-platform-migration lets the platform patch a Cluster
# outside a db-topology namespace only when it holds migrate on that Cluster (infra ADR 0063).
VERBS = ['get', 'patch', 'migrate']
# Sessions granted before migrate was added; revoke still recognizes them.
LEGACY_VERBS = ['get', 'patch']


class Refused(Exception):
    pass


def require(ok, message):
    if not ok:
        raise Refused(message)


def manifests(args):
    for field, limit in (('namespace', 63), ('cluster', 63), ('session', 50)):
        value = getattr(args, field)
        require(len(value) <= limit and DNS_LABEL.fullmatch(value),
                f'Invalid {field}: use a DNS label of 1-{limit} lowercase letters, digits or hyphens.')
    protected = args.namespace in PROTECTED or args.namespace.startswith('kube-')
    require(not protected or (args.namespace, args.cluster) in PROTECTED_EXCEPTIONS,
            'The requested namespace is reserved for infrastructure.' +
            ' Only these (namespace, CNPG Cluster) pairs may be granted there: ' +
            ', '.join(f'{ns}/{name}' for ns, name in sorted(PROTECTED_EXCEPTIONS)) + '.')
    for value in (args.context, args.kubeconfig):
        require(value.strip() and not any(ord(char) < 32 for char in value),
                'Context and kubeconfig must be explicit nonempty values without control characters.')
    name = 'db-migration-' + args.session
    metadata = {'name': name, 'namespace': args.namespace,
                'labels': {'app.kubernetes.io/managed-by': 'db-migration-access',
                           'hopcharlie.io/migration-session': args.session},
                'annotations': {'hopcharlie.io/access-schema': 'v1',
                                'hopcharlie.io/cnpg-cluster': args.cluster}}
    return [
        {'apiVersion': RBAC_API, 'kind': 'Role', 'metadata': metadata,
         'rules': [{'apiGroups': ['postgresql.cnpg.io'], 'resources': ['clusters'],
                    'resourceNames': [args.cluster], 'verbs': VERBS}]},
        {'apiVersion': RBAC_API, 'kind': 'RoleBinding', 'metadata': metadata,
         'roleRef': {'apiGroup': 'rbac.authorization.k8s.io', 'kind': 'Role', 'name': name},
         'subjects': [{'kind': 'ServiceAccount', 'name': 'hopcharlie-platform', 'namespace': 'charlie-system'}]},
    ]


def owned(actual, expected, legacy=False):
    if actual is None:
        return
    metadata = actual.get('metadata') or {}
    wanted = expected['metadata']
    body = {key: value for key, value in actual.items() if key != 'metadata'}
    same_body = body == {key: value for key, value in expected.items() if key != 'metadata'}
    if legacy and not same_body and expected['kind'] == 'Role':
        old = json.loads(json.dumps(expected))
        old['rules'][0]['verbs'] = LEGACY_VERBS
        same_body = body == {key: value for key, value in old.items() if key != 'metadata'}
    require(same_body and metadata.get('name') == wanted['name']
            and metadata.get('namespace') == wanted['namespace']
            and all((metadata.get(field) or {}).get(key) == value
                    for field in ('labels', 'annotations') for key, value in wanted[field].items())
            and not any(metadata.get(field) for field in ('ownerReferences', 'finalizers', 'deletionTimestamp')),
            'Existing RBAC differs from this session, cluster, subject or exact permission scope; no takeover allowed.')


class Kubectl:
    def __init__(self, args):
        self.base = ['kubectl', '--kubeconfig', args.kubeconfig, '--context', args.context,
                     '--request-timeout=15s']

    def run(self, *args, document=None):
        try:
            return subprocess.run([*self.base, *args], input=None if document is None else json.dumps(document),
                                  text=True, encoding='utf-8', errors='replace', capture_output=True, timeout=30)
        except (OSError, subprocess.SubprocessError):
            raise Refused('kubectl could not complete; command output and credential details are suppressed.') from None

    @staticmethod
    def decode(output):
        try:
            document = json.loads(output)
        except (ValueError, TypeError):
            raise Refused('kubectl returned invalid JSON; output is suppressed.') from None
        require(isinstance(document, dict), 'kubectl returned an unexpected response; output is suppressed.')
        return document

    def get(self, resource, name, namespace=None):
        scope = ['--namespace', namespace] if namespace else []
        result = self.run('get', resource, name, *scope, '-o', 'json', '--ignore-not-found')
        require(result.returncode == 0, 'kubectl get failed; output and credential details are suppressed.')
        if not result.stdout.strip():
            return None
        document = self.decode(result.stdout)
        metadata = document.get('metadata') or {}
        require(metadata.get('name') == name and metadata.get('namespace') == namespace,
                'kubectl returned a different object; refusing the requested operation.')
        return document

    def read_rbac(self, expected, legacy=False):
        metadata = expected['metadata']
        actual = self.get(expected['kind'].lower() + 's.rbac.authorization.k8s.io',
                          metadata['name'], metadata['namespace'])
        owned(actual, expected, legacy)
        return actual

    def create(self, document):
        result = self.run('create', '-f', '-', '-o', 'json', document=document)
        require(result.returncode == 0, 'kubectl create failed; output and credential details are suppressed.')
        actual = self.decode(result.stdout)
        owned(actual, document)
        require(actual.get('metadata', {}).get('uid'), 'Created RBAC response is missing its identity.')
        return actual

    def remove(self, expected, created_uid=None, legacy=False):
        actual = self.read_rbac(expected, legacy)
        if actual is None:
            return
        metadata = actual['metadata']
        require(created_uid is None or metadata.get('uid') == created_uid,
                'Rollback refused: the newly created Role was replaced.')
        require(all(isinstance(metadata.get(field), str) and metadata[field]
                    for field in ('uid', 'resourceVersion')), 'RBAC identity/version is missing; refusing deletion.')
        # Preconditions close the race between checking ownership and deleting the object.
        options = {'apiVersion': 'v1', 'kind': 'DeleteOptions',
                   'preconditions': {field: metadata[field] for field in ('uid', 'resourceVersion')}}
        uri = (f'/apis/{RBAC_API}/namespaces/{metadata["namespace"]}/'
               f'{expected["kind"].lower()}s/{metadata["name"]}')
        result = self.run('delete', '--raw', uri, '-f', '-', document=options)
        remaining = self.get(expected['kind'].lower() + 's.rbac.authorization.k8s.io',
                             metadata['name'], metadata['namespace'])
        require(remaining is None, 'Conditional RBAC deletion failed or is incomplete; remaining objects were retained.'
                if result.returncode else 'RBAC deletion is incomplete; access may remain.')


def grant(kube, args, documents):
    for resource, name, namespace in (('namespace', args.namespace, None),
                                      ('serviceaccount', 'hopcharlie-platform', 'charlie-system'),
                                      ('clusters.postgresql.cnpg.io', args.cluster, args.namespace)):
        require(kube.get(resource, name, namespace) is not None,
                'The namespace, platform ServiceAccount and CNPG Cluster must already exist; nothing is bootstrapped.')
    existing = [kube.read_rbac(document) for document in documents]
    created_uid = None
    try:
        for document, actual in zip(documents, existing):
            if actual is None:
                created = kube.create(document)
                if document['kind'] == 'Role':
                    created_uid = created['metadata']['uid']
    except Refused:
        if created_uid:
            try:
                kube.remove(documents[0], created_uid)
            except Refused:
                raise Refused('Grant failed; rollback was refused or failed. Inspect this session before retrying or revoking.') from None
            raise Refused('Grant failed; rollback removed only the Role created by this invocation.') from None
        raise


def revoke(kube, documents):
    for document in documents:
        kube.read_rbac(document, legacy=True)
    for document in reversed(documents):
        kube.remove(document, legacy=True)


def main(argv=None):
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument('command', nargs='?', default='render', choices=('render', 'grant', 'revoke'))
    for field in ('context', 'kubeconfig', 'namespace', 'cluster', 'session'):
        parser.add_argument('--' + field, required=True)
    args = parser.parse_args(argv)
    try:
        documents = manifests(args)
        if args.command == 'render':
            print(json.dumps({'apiVersion': 'v1', 'kind': 'List', 'items': documents}, indent=2))
        else:
            kube = Kubectl(args)
            if args.command == 'grant':
                print('Warning: access has no automatic expiry and remains until explicit revoke.', file=sys.stderr)
                grant(kube, args, documents)
                print('Session access granted; it remains until you explicitly revoke the same session and cluster.')
            else:
                revoke(kube, documents)
                print('Session RBAC revoked (or already absent). Other grants and credentials are unchanged.')
        return 0
    except Refused as error:
        print(f'Refused: {error}', file=sys.stderr)
        return 1


if __name__ == '__main__':
    raise SystemExit(main())
