#!/usr/bin/env python3
"""Real disposable Kubernetes admission; no DB/operator or production access.

--check-only checks policy parity offline. The default requires an explicitly
created kind context and exercises actual CRD validation, RBAC and CEL policies.
"""
import copy
import json
import os
from pathlib import Path
import subprocess
import sys
import time
import urllib.request

import yaml

ROOT = Path(__file__).resolve().parents[1]
CONTEXT = 'kind-cnpg-migration-admission'
ACTOR = 'system:serviceaccount:charlie-system:hopcharlie-platform'
POLICY = 'hopcharlie-platform-migration'
ENVS = ('aws', 'gcp', 'onprem')
CNPG_SHA = '2a35abb4628f209d149825ef3c38011e0701ff2f'  # official v1.30.1


def policies():
    result = {}
    for env in ENVS:
        path = ROOT / 'clusters' / (env + '-dev-workload') / 'hopcharlie-platform-access.yaml'
        result[env] = [d for d in yaml.safe_load_all(path.read_text(encoding='utf-8'))
                       if d and d['metadata']['name'] == POLICY]
    return result


def check_only():
    loaded = policies()
    specs = [next(d['spec'] for d in loaded[e] if d['kind'] == 'ValidatingAdmissionPolicy') for e in ENVS]
    assert specs[0] == specs[1] == specs[2], 'AWS/GCP/onprem migration admission policies differ'
    for env, documents in loaded.items():
        role = next(d for d in documents if d['kind'] == 'ClusterRole')
        assert role['rules'] == [{'apiGroups': ['postgresql.cnpg.io'], 'resources': ['clusters'], 'verbs': ['get', 'patch']}], env
    print('Offline policy/RBAC parity passed (not an admission or migration proof).', flush=True)


def kube(args, obj=None, actor=False, fail=True):
    command = ['kubectl', '--context', CONTEXT, '--request-timeout=20s']
    if actor:
        command += ['--as=' + ACTOR, '--as-group=system:serviceaccounts',
                    '--as-group=system:serviceaccounts:charlie-system', '--as-group=system:authenticated']
    completed = subprocess.run(command + args, input=json.dumps(obj) if obj is not None else None,
                               text=True, capture_output=True)
    if fail:
        assert completed.returncode == 0, completed.stderr
    return completed


def apply(obj):
    return kube(['apply', '--server-side', '-f', '-'], obj)


def fixture(namespace, name='demo-db', **changes):
    spec = {'instances': 1, 'storage': {'size': '1Gi'},
            'replica': {'self': name + '-gke', 'primary': name + '-gke', 'source': name + '-eks'},
            'externalClusters': [{'name': name + '-' + e,
                                  'connectionParameters': {'host': e + '.example.invalid', 'user': 'streaming_replica', 'dbname': 'postgres'}}
                                 for e in ('eks', 'onprem')],
            'postgresql': {'parameters': {'archive_mode': 'on', 'max_connections': '100'}}}
    obj = {'apiVersion': 'postgresql.cnpg.io/v1', 'kind': 'Cluster',
           'metadata': {'name': name, 'namespace': namespace,
                        'labels': {'hopcharlie.dev/db-topology-members': '3'}}, 'spec': spec}
    for key, value in changes.items():
        obj[key] = value
    apply(obj)
    return json.loads(kube(['get', 'clusters.postgresql.cnpg.io', name, '-n', namespace, '-o', 'json']).stdout)


def source_patch(obj, target=None):
    m, r = obj['metadata'], obj['spec']['replica']
    return [{'op': 'test', 'path': '/metadata/uid', 'value': m['uid']},
            {'op': 'test', 'path': '/metadata/resourceVersion', 'value': m['resourceVersion']},
            {'op': 'test', 'path': '/spec/replica/self', 'value': r['self']},
            {'op': 'test', 'path': '/spec/replica/primary', 'value': r['primary']},
            {'op': 'add', 'path': '/spec/replica/source', 'value': target or m['name'] + '-onprem'}]


def decision(label, obj, patch, allowed, policy=True):
    m = obj['metadata']
    result = kube(['patch', 'clusters.postgresql.cnpg.io', m['name'], '-n', m['namespace'],
                   '--type=json', '-p', json.dumps(patch), '--dry-run=server', '-o', 'json'], actor=True, fail=False)
    if allowed:
        assert result.returncode == 0, label + ': ' + result.stderr
    else:
        assert result.returncode != 0, label + ': unexpectedly admitted'
        if policy:
            assert POLICY in result.stderr, label + ': not denied by migration policy: ' + result.stderr
    print(('ALLOW ' if allowed else 'DENY  ') + label, flush=True)
    return result


def wait_policy():
    for _ in range(60):
        obj = json.loads(kube(['get', 'validatingadmissionpolicy', POLICY, '-o', 'json']).stdout)
        status = obj.get('status') or {}
        if status.get('observedGeneration') == obj['metadata']['generation'] and 'typeChecking' in status:
            warnings = status['typeChecking'].get('expressionWarnings') or []
            assert not warnings, json.dumps(warnings)
            return
        time.sleep(1)
    raise AssertionError('migration policy type checking did not finish')


def real_checks():
    assert os.environ.get('CNPG_MIGRATION_ADMISSION_FIXTURE') == '1', 'explicit disposable fixture opt-in required'
    current = subprocess.check_output(['kubectl', 'config', 'current-context'], text=True).strip()
    assert current == CONTEXT, 'refusing a non-fixture current context'
    url = ('https://raw.githubusercontent.com/cloudnative-pg/cloudnative-pg/' + CNPG_SHA +
           '/config/crd/bases/postgresql.cnpg.io_clusters.yaml')
    with urllib.request.urlopen(url, timeout=30) as response:
        crd = yaml.safe_load(response.read())
    apply(crd)
    for _ in range(60):
        obj = json.loads(kube(['get', 'crd', 'clusters.postgresql.cnpg.io', '-o', 'json']).stdout)
        if any(c['type'] == 'Established' and c['status'] == 'True' for c in (obj.get('status') or {}).get('conditions') or []):
            break
        time.sleep(1)
    else:
        raise AssertionError('CNPG CRD not established')
    apply({'apiVersion': 'v1', 'kind': 'Namespace', 'metadata': {'name': 'charlie-system'}})
    apply({'apiVersion': 'v1', 'kind': 'ServiceAccount', 'metadata': {'name': 'hopcharlie-platform', 'namespace': 'charlie-system'},
           'automountServiceAccountToken': False})
    for namespace, labels in [('paired', {'hopcharlie.dev/db-topology': 'true'}), ('production', {})]:
        apply({'apiVersion': 'v1', 'kind': 'Namespace', 'metadata': {'name': namespace, 'labels': labels}})
    base = fixture('paired')
    directions = []
    for source in ('eks', 'gke', 'onprem'):
        name = 'direction-' + source
        peers = [e for e in ('eks', 'gke', 'onprem') if e != source]
        spec = copy.deepcopy(base['spec'])
        spec['replica'] = {'self': name + '-' + source, 'primary': name + '-' + source,
                           'source': name + '-' + peers[0]}
        spec['externalClusters'] = [{'name': name + '-' + p, 'connectionParameters': {'host': p + '.example.invalid'}} for p in peers]
        directions.append((source, peers, fixture('paired', name, spec=spec)))
    foreign = fixture('production')
    other = fixture('production', 'foreign-db')
    # Only this fixture Cluster receives the existing custom migrate verb.
    apply({'apiVersion': 'rbac.authorization.k8s.io/v1', 'kind': 'Role', 'metadata': {'name': 'fixture-migrate', 'namespace': 'production'},
           'rules': [{'apiGroups': ['postgresql.cnpg.io'], 'resources': ['clusters'], 'resourceNames': ['demo-db'], 'verbs': ['migrate']}]})
    apply({'apiVersion': 'rbac.authorization.k8s.io/v1', 'kind': 'RoleBinding', 'metadata': {'name': 'fixture-migrate', 'namespace': 'production'},
           'roleRef': {'apiGroup': 'rbac.authorization.k8s.io', 'kind': 'Role', 'name': 'fixture-migrate'},
           'subjects': [{'kind': 'ServiceAccount', 'name': 'hopcharlie-platform', 'namespace': 'charlie-system'}]})
    standby_spec = copy.deepcopy(base['spec'])
    standby_spec['replica']['primary'] = 'demo-db-eks'
    standby = fixture('paired', 'standby-db', spec=standby_spec)
    two = fixture('paired', 'two-db')
    kube(['label', 'clusters.postgresql.cnpg.io', 'two-db', '-n', 'paired', 'hopcharlie.dev/db-topology-members=2', '--overwrite'])
    two = json.loads(kube(['get', 'clusters.postgresql.cnpg.io', 'two-db', '-n', 'paired', '-o', 'json']).stdout)
    legacy_spec = copy.deepcopy(base['spec'])
    legacy_spec['replica'] = {'self': 'legacy-db-gke', 'primary': 'legacy-db-gke', 'source': 'legacy'}
    legacy_spec['externalClusters'] = [{'name': n, 'connectionParameters': {'host': 'fixture.example.invalid'}} for n in ('legacy', 'legacy-db-eks')]
    legacy = fixture('paired', 'legacy-db', spec=legacy_spec)
    for env, documents in policies().items():
        for document in documents:
            apply(document)
        wait_policy()
        # Bounded admission-cache readiness: an authorized benign patch must
        # pass and an unauthorized Cluster must be denied by this exact policy.
        for _ in range(30):
            probe = source_patch(other)
            r = kube(['patch', 'clusters.postgresql.cnpg.io', 'foreign-db', '-n', 'production', '--type=json',
                      '-p', json.dumps(probe), '--dry-run=server'], actor=True, fail=False)
            if r.returncode and POLICY in r.stderr:
                break
            time.sleep(1)
        else:
            raise AssertionError('migration binding admission cache not ready')
        label = env + ': '
        allowed = decision(label + 'exact engine UID/RV/self/primary source-only rewire', base, source_patch(base), True)
        returned = json.loads(allowed.stdout)
        expected = copy.deepcopy(base['spec'])
        expected['replica']['source'] = 'demo-db-onprem'
        assert returned['spec'] == expected, 'rewire changed another spec field'
        for source, peers, direction in directions:
            for target in peers:
                decision(label + source + ' -> ' + target + ' source preparation', direction,
                         source_patch(direction, direction['metadata']['name'] + '-' + target), True)
        decision(label + 'scoped production migrate authorization', foreign, source_patch(foreign), True)
        decision(label + 'foreign production Cluster', other, source_patch(other), False)
        decision(label + 'unknown external source', base, source_patch(base, 'attacker-db'), False)
        decision(label + 'source self', base, source_patch(base, 'demo-db-gke'), False)
        decision(label + 'standby source change', standby, source_patch(standby), False)
        decision(label + 'two-member source change', two, source_patch(two), False)
        for field, value in [('primary', 'demo-db-onprem'), ('self', 'attacker-db'), ('promotionToken', 'fixture-token')]:
            decision(label + 'source with replica.' + field, base,
                     source_patch(base) + [{'op': 'add', 'path': '/spec/replica/' + field, 'value': value}], False)
        for path, value in [('/spec/storage/size', '2Gi'), ('/spec/externalClusters/0/connectionParameters/host', 'attacker.invalid'),
                            ('/spec/postgresql/parameters/max_connections', '200'),
                            ('/metadata/labels/hopcharlie.dev~1db-topology-members', '2'),
                            ('/metadata/annotations', {'fixture': 'changed'})]:
            decision(label + 'source with forbidden ' + path, base,
                     source_patch(base) + [{'op': 'add', 'path': path, 'value': value}], False)
        decision(label + 'existing primary transition', base,
                 [{'op': 'add', 'path': '/spec/replica/primary', 'value': 'demo-db-onprem'}], True)
        decision(label + 'existing promotion primary/token transition', standby,
                 [{'op': 'add', 'path': '/spec/replica/primary', 'value': 'demo-db-gke'},
                  {'op': 'add', 'path': '/spec/replica/promotionToken', 'value': 'fixture-not-a-real-token'}], True)
        decision(label + 'existing archive_mode mutation exception', base,
                 [{'op': 'add', 'path': '/spec/postgresql/parameters/archive_mode', 'value': 'always'}], True)
        decision(label + 'archive_mode off', base,
                 [{'op': 'add', 'path': '/spec/postgresql/parameters/archive_mode', 'value': 'off'}], False)
        decision(label + 'existing legacy promoted source rewire', legacy, source_patch(legacy, 'legacy-db-eks'), True)
        stale = source_patch(base)
        stale[0]['value'] = 'stale-uid'
        decision(label + 'stale engine UID compare-and-swap', base, stale, False, policy=False)
    print('PASS: actual CNPG CRD/RBAC/CEL admission across AWS/GCP/onprem; no database migration was executed.', flush=True)


if __name__ == '__main__':
    check_only()
    if '--check-only' not in sys.argv:
        real_checks()
