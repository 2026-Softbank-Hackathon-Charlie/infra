#!/usr/bin/env python3
"""Opt-in CI bootstrap. Never print credentials, raw CLI errors, or resource bodies."""
import argparse
import base64
import collections
import hashlib
import http.client
import http.cookies
import importlib.util
import json
import os
from pathlib import Path
import re
import signal
import socket
import subprocess
import sys
import tarfile
import tempfile
import threading
import time
from urllib.parse import urlencode, urlsplit
import urllib.request

import yaml
from clickstack_bootstrap import (ACCOUNT, REGION, CLUSTER, REPO, ROLE, POLICY, OWNER, OWNER_KEY,
    RUN_KEY, ATTEMPT_KEY, REPO_KEY, WORKFLOW, APPS, SECRET_NAMES, Bundle, Rejected, require, context, access_owned)

ROOT = Path(__file__).resolve().parents[1]


def module(name, path):
    spec = importlib.util.spec_from_file_location(name, path)
    value = importlib.util.module_from_spec(spec)
    spec.loader.exec_module(value)
    return value


class CommandFailed(Rejected):
    def __init__(self, message, authorization_denied=False):
        super().__init__(message)
        self.authorization_denied = authorization_denied


class Commands:
    def run(self, args, data=None, timeout=120, optional=False):
        try:
            result = subprocess.run(args, input=data, stdout=subprocess.PIPE, stderr=subprocess.PIPE,
                                    timeout=timeout, text=True)
        except (OSError, subprocess.TimeoutExpired):
            raise CommandFailed('Command unavailable or timed out') from None
        if result.returncode:
            if optional and 'ResourceNotFoundException' in result.stderr:
                return None
            raise CommandFailed('Command failed; raw output withheld', authorization_denied=(
                any(text in result.stderr for text in ('Unauthorized', 'forbidden', 'You must be logged'))
                or ('can-i' in args and result.stdout.strip() == 'no')))
        return result.stdout


class AWS:
    def __init__(self, commands):
        self.commands = commands

    def get(self, *args, optional=False):
        result = self.commands.run(['aws', '--region', REGION, *args, '--output', 'json'], optional=optional)
        return json.loads(result) if result else None

    def identity(self):
        identity = self.get('sts', 'get-caller-identity')
        require(identity['Account'] == ACCOUNT and identity['Arn'].startswith(
            f'arn:aws:sts::{ACCOUNT}:assumed-role/charlie-dev-github-terraform/'), 'AWS caller rejected')

    def entry(self):
        d = self.get('eks', 'describe-access-entry', '--cluster-name', CLUSTER, '--principal-arn', ROLE, optional=True)
        return d['accessEntry'] if d else None

    def create_access(self, run):
        require(self.entry() is None, 'Existing access entry left unchanged; review or cleanup required')
        self.get('eks', 'create-access-entry', '--cluster-name', CLUSTER, '--principal-arn', ROLE,
                 '--type', 'STANDARD', '--tags', f'{OWNER_KEY}={OWNER},{RUN_KEY}={run},{ATTEMPT_KEY}={os.environ["GITHUB_RUN_ATTEMPT"]},{REPO_KEY}={REPO.replace("/", ".")}')
        self.get('eks', 'associate-access-policy', '--cluster-name', CLUSTER, '--principal-arn', ROLE,
                 '--policy-arn', POLICY, '--access-scope', 'type=cluster')

    def cleanup(self, run, *, finalizer=False):
        entry = self.entry()
        if entry is None:
            return
        if not access_owned(entry, run) or (finalizer and entry.get('tags', {}).get(ATTEMPT_KEY) != os.environ.get('GITHUB_RUN_ATTEMPT')):
            require(finalizer, 'Cleanup owner mismatch; access unchanged')
            print('Pre-existing access entry left unchanged.')
            return
        policies = self.get('eks', 'list-associated-access-policies', '--cluster-name', CLUSTER, '--principal-arn', ROLE)['associatedAccessPolicies']
        require(all(p['policyArn'] == POLICY and p['accessScope'].get('type') == 'cluster' and not p['accessScope'].get('namespaces') for p in policies), 'Unexpected access policy; cleanup stopped')
        self.get('eks', 'delete-access-entry', '--cluster-name', CLUSTER, '--principal-arn', ROLE)
        wait(lambda: self.entry() is None, 120, 'Access removal not confirmed')
        print('Owned temporary EKS access entry removed.')


def wait(probe, timeout, message):
    end = time.monotonic() + timeout
    while True:
        try:
            value = probe()
            if value:
                return value
        except (CommandFailed, OSError):
            # Only transient observation/transport errors are retried, never contract failures.
            pass
        if time.monotonic() >= end:
            raise Rejected(message)
        time.sleep(3)


def verify_owner_run(commands, run, token, *, current=False):
    require(re.fullmatch('[0-9]+', run), 'Owner run ID rejected')
    request = urllib.request.Request(f'https://api.github.com/repos/{REPO}/actions/runs/{run}',
        headers={'Authorization': 'Bearer ' + token, 'Accept': 'application/vnd.github+json'})
    opener = urllib.request.build_opener(module('smoke_redirects', ROOT / 'scripts/observability-smoke.py').NoRedirect())
    with opener.open(request, timeout=20) as stream:
        data = json.load(stream)
    require(str(data['id']) == run and data['repository']['full_name'] == REPO and data['event'] == 'workflow_dispatch'
            and data['head_branch'] == 'main' and data['path'].split('@')[0] == WORKFLOW, 'Owner workflow rejected')
    require(current or data['status'] == 'completed', 'Previous bootstrap run is still active')


def cpu(value):
    value = str(value or 0)
    for suffix, scale in (('n', 1e-9), ('u', 1e-6), ('m', .001)):
        if value.endswith(suffix):
            return float(value[:-1]) * scale
    return float(value)


def pod_cpu(pod):
    spec = pod['spec']
    normal = sum(cpu(c.get('resources', {}).get('requests', {}).get('cpu')) for c in spec.get('containers', []))
    sidecars = peak = 0
    for c in spec.get('initContainers', []):
        value = cpu(c.get('resources', {}).get('requests', {}).get('cpu'))
        if c.get('restartPolicy') == 'Always':
            sidecars += value
            peak = max(peak, sidecars)
        else:
            peak = max(peak, sidecars + value)
    return max(normal + sidecars, peak, cpu(spec.get('resources', {}).get('requests', {}).get('cpu'))) + cpu(spec.get('overhead', {}).get('cpu'))


class Kube:
    def __init__(self, commands, path):
        self.commands, self.path = commands, path

    def run(self, *args, obj=None):
        return self.commands.run(['kubectl', '--kubeconfig', str(self.path), '--context', CLUSTER,
            '--request-timeout=20s', *args], json.dumps(obj) if obj is not None else None)

    def get(self, kind, name=None, namespace='observability'):
        args = ['get', kind]
        if name:
            args += [name, '--ignore-not-found']
        if namespace == '*':
            args += ['-A']
        elif namespace:
            args += ['-n', namespace]
        result = self.run(*args, '-o', 'json')
        return json.loads(result) if result else None

    def secret(self, name):
        require(name in SECRET_NAMES)
        return self.get('secret', name)

    def create(self, obj):
        if obj['kind'] == 'Secret':
            require(obj['metadata']['name'] in SECRET_NAMES and obj['metadata']['namespace'] == 'observability')
        self.run('create', '-f', '-', obj=obj)

    def register(self, obj):
        meta = obj['metadata']
        require(meta['namespace'] == 'argocd' and ((obj['kind'] == 'Application' and meta['name'] in APPS)
                or (obj['kind'] == 'AppProject' and meta['name'] == 'charlie-observability')))
        current = self.get(obj['kind'], meta['name'], 'argocd')
        if current:
            require(current['metadata'].get('labels', {}).get(OWNER_KEY) == OWNER, 'Existing Argo object owner rejected')
        obj['metadata'].setdefault('labels', {})[OWNER_KEY] = OWNER
        if current:
            self.run('apply', '--server-side', '--field-manager=charlie-clickstack-ci', '-f', '-', obj=obj)
        else:
            self.create(obj)

    def health(self):
        nodes = self.get('nodes', namespace=None)['items']
        require(len(nodes) == 3 and all(any(c['type'] == 'Ready' and c['status'] == 'True'
            for c in n['status']['conditions']) for n in nodes), 'Three Ready nodes required')
        pods = self.get('pods', namespace='*')['items']
        active = [p for p in pods if p.get('status', {}).get('phase') not in ('Succeeded', 'Failed')]
        require(not any(p.get('status', {}).get('phase') == 'Pending' for p in active), 'Pending workloads require review')
        free = sum(cpu(n['status']['allocatable']['cpu']) for n in nodes) - sum(pod_cpu(p) for p in active)
        return free

    def access_revoked(self):
        try:
            self.run('auth', 'can-i', 'get', 'secrets', '-n', 'observability')
            return False
        except CommandFailed as error:
            if error.authorization_denied:
                return True
            raise

    def ready_workloads(self):
        workloads = self.get('deployments,statefulsets,daemonsets', namespace='*')['items']
        for obj in workloads:
            status, spec = obj.get('status', {}), obj['spec']
            ds = obj['kind'] == 'DaemonSet'
            desired = status.get('desiredNumberScheduled', 0) if ds else spec.get('replicas', 1)
            ready = status.get('numberReady', 0) if ds else status.get('readyReplicas', 0)
            if desired != ready or status.get('observedGeneration', 0) < obj['metadata'].get('generation', 0):
                return False
        return True

    def owned_pods(self, root):
        uid = root['metadata']['uid']
        controllers = self.get('deployments,statefulsets,replicasets')['items']
        selected = {uid}
        for _ in range(4):
            selected |= {o['metadata']['uid'] for o in controllers if any(r['uid'] in selected for r in o['metadata'].get('ownerReferences', []))}
        owned_controllers = [o for o in controllers if o['metadata']['uid'] in selected]
        if any(o.get('status', {}).get('observedGeneration', 0) < o['metadata'].get('generation', 0) or o.get('status', {}).get('readyReplicas', 0) != o['spec'].get('replicas', 1) for o in owned_controllers if o['kind'] in ('Deployment', 'StatefulSet')):
            return []
        return [p for p in self.get('pods')['items'] if any(r['uid'] in selected for r in p['metadata'].get('ownerReferences', []))]

    def wait_root(self, kind, name, timeout):
        def probe():
            root = self.get(kind, name)
            if not root:
                return False
            if kind in ('deployment', 'daemonset'):
                status = root.get('status', {})
                desired = status.get('desiredNumberScheduled', 0) if kind == 'daemonset' else root['spec'].get('replicas', 1)
                ready = status.get('numberReady', 0) if kind == 'daemonset' else status.get('readyReplicas', 0)
                if ready != desired or desired < 1 or status.get('observedGeneration', 0) < root['metadata'].get('generation', 0):
                    return False
            pods = self.owned_pods(root)
            if not pods or not all(any(c['type'] == 'Ready' and c['status'] == 'True' for c in p.get('status', {}).get('conditions', [])) for p in pods):
                return False
            for pod in pods:
                for volume in pod['spec'].get('volumes', []):
                    if 'persistentVolumeClaim' in volume:
                        pvc = self.get('pvc', volume['persistentVolumeClaim']['claimName'])
                        if not pvc or pvc.get('status', {}).get('phase') != 'Bound':
                            return False
            return root
        return wait(probe, timeout, 'Owned workload readiness timed out')

    def restart(self, kind, name):
        require((kind, name) in {('deployment', 'clickstack'), ('clickhousecluster.clickhouse.com', 'clickstack-clickhouse'), ('mongodbcommunity.mongodbcommunity.mongodb.com', 'clickstack-mongodb')}, 'Restart target rejected')
        root = self.wait_root(kind, name, 300)
        pods = self.owned_pods(root)
        require(len(pods) == 1, 'Restart requires one owned Pod')
        pod = pods[0]
        old_uid = pod['metadata']['uid']
        volumes = {}
        for v in pod['spec'].get('volumes', []):
            if 'persistentVolumeClaim' in v:
                pvc = self.get('pvc', v['persistentVolumeClaim']['claimName'])
                volumes[pvc['metadata']['name']] = (pvc['metadata']['uid'], pvc['spec']['volumeName'])
        name = pod['metadata']['name']
        require(re.fullmatch('[a-z0-9][a-z0-9.-]*', name))
        self.run('create', '--raw', f'/api/v1/namespaces/observability/pods/{name}/eviction', '-f', '-', obj={
            'apiVersion': 'policy/v1', 'kind': 'Eviction', 'metadata': {'name': name, 'namespace': 'observability'},
            'deleteOptions': {'preconditions': {'uid': old_uid}}})
        def changed():
            current = self.owned_pods(root)
            return len(current) == 1 and current[0]['metadata']['uid'] != old_uid
        wait(changed, 300, 'Replacement Pod not observed')
        self.wait_root(kind, root['metadata']['name'], 300)
        for claim, identity in volumes.items():
            pvc = self.get('pvc', claim)
            require((pvc['metadata']['uid'], pvc['spec']['volumeName']) == identity, 'PVC identity changed')


class Processes:
    def __init__(self, state):
        self.procs, self.session, self.state = [], None, state

    def start(self, args, session=False):
        p = subprocess.Popen(args, stdout=subprocess.PIPE if session else subprocess.DEVNULL,
            stderr=subprocess.DEVNULL, text=True, start_new_session=True)
        self.procs.append(p)
        if session:
            def reader():
                for line in p.stdout:
                    match = re.search(r'Starting session with SessionId: ([A-Za-z0-9_-]+)', line)
                    if match:
                        self.session = match[1]
                        self.state.write_text(json.dumps({'ssmSessionId': self.session}))
                        self.state.chmod(0o600)
            threading.Thread(target=reader, daemon=True).start()
        return p

    def close(self):
        for p in reversed(self.procs):
            if p.poll() is None:
                try:
                    os.killpg(p.pid, signal.SIGINT)
                except ProcessLookupError:
                    continue
                try:
                    p.wait(timeout=12)
                except subprocess.TimeoutExpired:
                    os.killpg(p.pid, signal.SIGKILL)
                    p.wait()


def free_port():
    with socket.socket() as s:
        s.bind(('127.0.0.1', 0))
        return s.getsockname()[1]


def listener(port):
    try:
        with socket.create_connection(('127.0.0.1', port), timeout=1):
            return True
    except OSError:
        return False


class LocalHTTP:
    def __init__(self, port):
        self.port, self.cookies = port, {}

    def request(self, path='/', data=None, headers=None, method=None):
        require(path.startswith('/') and '\r' not in path and '\n' not in path)
        connection = http.client.HTTPConnection('127.0.0.1', self.port, timeout=20)
        request_headers = dict(headers or {})
        if self.cookies:
            request_headers['Cookie'] = '; '.join(k + '=' + v for k, v in self.cookies.items())
        if data is not None and not isinstance(data, (bytes, str)):
            data = json.dumps(data).encode()
            request_headers['Content-Type'] = 'application/json'
        try:
            connection.request(method or ('POST' if data is not None else 'GET'), path, body=data, headers=request_headers)
            response = connection.getresponse()
            for key, value in response.getheaders():
                if key.lower() == 'set-cookie':
                    cookie = http.cookies.SimpleCookie();cookie.load(value)
                    self.cookies.update({k: m.value for k, m in cookie.items()})
            body = response.read(2 * 1024 * 1024)
            # Redirects are not followed. Login may return a local frontend redirect.
            require(response.status in (200, 201, 302), 'Service HTTP request rejected')
            return json.loads(body) if body.lstrip()[:1] in (b'{', b'[') else body
        finally:
            connection.close()


def render(commands, targets, sha):
    settings = {'repoURL': f'https://github.com/{REPO}.git', 'revision': sha, 'targets': targets,
                'observability': {'enabled': True, 'agentTargets': ['management'], 'gatewayTargets': ['management'], 'destinationTargets': ['management']}}
    # This file contains endpoint metadata, never Secret values.
    with tempfile.TemporaryDirectory(prefix='clickstack-render-') as tmp:
        path = Path(tmp) / 'values.json';path.write_text(json.dumps(settings));path.chmod(0o600)
        text = commands.run(['helm', 'template', 'charlie', str(ROOT / 'helm/gitops'), '-n', 'argocd',
            '--kube-version', '1.35.0', '--show-only', 'templates/observability.yaml', '-f', str(path)])
    docs = [d for d in yaml.safe_load_all(text) if d]
    require(len(docs) == 5)
    expected = {('AppProject', 'charlie-observability'), *(('Application', a) for a in APPS)}
    require({(d['kind'], d['metadata']['name']) for d in docs} == expected)
    for d in docs:
        require(d['metadata']['namespace'] == 'argocd')
        if d['kind'] == 'AppProject':
            require(d['spec']['destinations'] == [{'server': targets['management']['endpoint'], 'namespace': 'observability'}])
        else:
            require(d['spec']['destination'] == {'server': targets['management']['endpoint'], 'namespace': 'observability'})
            require('automated' not in d['spec'].get('syncPolicy', {}) and not d['metadata'].get('finalizers'))
            sources = d['spec'].get('sources', [d['spec'].get('source')])
            for source in sources:
                if 'chart' not in source:
                    require(source['targetRevision'] == sha and source['repoURL'] == f'https://github.com/{REPO}.git')
    return docs


def sync(kube, app_name, *, expected, resources=None):
    require(app_name in APPS)
    app = kube.get('application', app_name, 'argocd')
    require(app['metadata'].get('labels', {}).get(OWNER_KEY) == OWNER)
    for field in ('project', 'destination', 'source', 'sources', 'syncPolicy'):
        require(app['spec'].get(field) == expected['spec'].get(field), 'Argo application binding drift')
    sources = app['spec'].get('sources')
    spec = {'prune': False, 'syncOptions': ['ServerSideApply=true']}
    revisions = [s['targetRevision'] for s in sources] if sources else [app['spec']['source']['targetRevision']]
    spec['revisions' if sources else 'revision'] = revisions if sources else revisions[0]
    if resources:
        require(app_name == APPS[1] and {(r['kind'], r['name']) for r in resources} == {
            ('ClickHouseCluster', 'clickstack-clickhouse'), ('KeeperCluster', 'clickstack-keeper'), ('MongoDBCommunity', 'clickstack-mongodb')})
        spec['resources'] = resources
    marker = str(time.time_ns())
    patch = {'operation': {'initiatedBy': {'username': OWNER}, 'info': [{'name': OWNER, 'value': marker}], 'sync': spec}}
    kube.run('patch', 'application', app_name, '-n', 'argocd', '--type=merge', '--patch-file=/dev/stdin', obj=patch)
    def completed():
        current = kube.get('application', app_name, 'argocd')
        if current.get('operation'):
            return False
        operation = current.get('status', {}).get('operationState', {})
        requested = operation.get('operation', {}).get('sync', {})
        if operation.get('operation', {}).get('info') != patch['operation']['info']:
            return False  # Never mistake the preceding operation for this request.
        require(not requested.get('prune') and requested.get('revisions' if sources else 'revision') == spec['revisions' if sources else 'revision'], 'Argo operation binding changed')
        require(operation.get('phase') not in ('Failed', 'Error'), 'Argo operation failed; next stage stopped')
        if operation.get('phase') != 'Succeeded':
            return False
        result = operation.get('syncResult', {})
        require((result.get('revisions') if sources else [result.get('revision')]) == revisions, 'Argo revision result rejected')
        return True
    wait(completed, 600, 'Argo sync completion timed out')


def aws_preflight(aws):
    aws.identity()
    project = module('project_config', ROOT / 'scripts/project-config.py').project()
    targets = {}
    for kind in ('management', 'workload'):
        name = project[kind + '_cluster_name']
        c = aws.get('eks', 'describe-cluster', '--name', name)['cluster']
        require(c['arn'] == f'arn:aws:eks:{REGION}:{ACCOUNT}:cluster/{name}' and c['status'] == 'ACTIVE' and c['version'] == '1.35')
        endpoint = urlsplit(c['endpoint'])
        require(endpoint.scheme == 'https' and endpoint.hostname and endpoint.netloc.lower() == endpoint.hostname and not endpoint.query and not endpoint.fragment and endpoint.path in ('', '/'))
        targets[kind] = {'name': name, 'endpoint': c['endpoint'], 'region': REGION, 'vpc_id': c['resourcesVpcConfig']['vpcId']}
    return targets


def operator_crds(commands, work):
    pin = json.loads((ROOT / 'helm/versions.json').read_text())['charts']['clickstack-operators']
    commands.run(['helm', 'pull', 'clickstack-operators', '--repo', pin['repo'], '--version', pin['version'], '--destination', str(work)])
    archive = work / f'clickstack-operators-{pin["version"]}.tgz'
    require(hashlib.sha256(archive.read_bytes()).hexdigest() == pin['sha256'], 'Operator chart checksum rejected')
    with tarfile.open(archive) as bundle:
        bundle.extractall(work, filter='data')
    docs = yaml.safe_load_all(commands.run(['helm', 'template', 'clickstack-operators', str(work / 'clickstack-operators'),
        '-n', 'observability', '--kube-version', '1.35.0', '--include-crds', '-f', str(ROOT / 'clusters/aws-dev-management/values/clickstack-operators.yaml')]))
    return [d['metadata']['name'] for d in docs if d and d['kind'] == 'CustomResourceDefinition']


def collision_checks(kube, crds):
    require(kube.get('namespace', 'observability', namespace=None), 'Existing observability namespace required')
    apps = [kube.get('application', a, 'argocd') for a in APPS]
    project = kube.get('appproject', 'charlie-observability', 'argocd')
    for obj in [*apps, project]:
        require(not obj or obj['metadata'].get('labels', {}).get(OWNER_KEY) == OWNER, 'Existing Argo object owner rejected')
    for name in crds:
        obj = kube.get('crd', name, namespace=None)
        if obj:
            meta = obj['metadata']
            tracking = meta.get('annotations', {}).get('argocd.argoproj.io/tracking-id', '')
            require(apps[0] and (tracking.startswith(APPS[0] + ':') or meta.get('labels', {}).get('app.kubernetes.io/instance') == APPS[0]), 'Existing CRD is not owned by the operator app')
    if any(apps):
        require(all(kube.secret(n) is not None for n in SECRET_NAMES), 'Live app has an incomplete credential bundle; no regeneration')
    for d in kube.get('deployments', namespace='*')['items']:
        images = ' '.join(c['image'] for c in d['spec']['template']['spec']['containers'])
        if any(i in images for i in ('clickhouse-operator', 'mongodb-kubernetes:')):
            require(d['metadata']['namespace'] == 'observability' and apps[0] and (d['metadata'].get('annotations', {}).get('argocd.argoproj.io/tracking-id', '').startswith(APPS[0] + ':') or d['metadata'].get('labels', {}).get('app.kubernetes.io/instance') == APPS[0]), 'Existing operator requires review')


def forward(kube, processes, service, remote):
    port = free_port()
    processes.start(['kubectl', '--kubeconfig', str(kube.path), '--context', CLUSTER, '-n', 'observability',
                     'port-forward', '--address=127.0.0.1', 'service/' + service, f'{port}:{remote}'])
    wait(lambda: listener(port), 30, 'Service port-forward failed')
    return port


def verify_runtime(kube, processes, values, work, run, *, send=True):
    api = LocalHTTP(forward(kube, processes, 'clickstack', 8000))
    ui = LocalHTTP(forward(kube, processes, 'clickstack', 3000))
    require(ui.request('/'), 'HyperDX UI empty')
    login = values['clickstack-demo-login']
    try:
        api.request('/login/password', login)
    except Rejected:
        installation = api.request('/installation')
        require(installation.get('isTeamExisting') is False, 'Existing team credentials rejected; no reset')
        api.request('/register/password', login)
    require(api.request('/me').get('email') == login['email'], 'HyperDX login identity not verified')
    connections = api.request('/connections')
    sources = api.request('/sources')
    require(isinstance(connections, list) and isinstance(sources, list))
    require({'Logs', 'Traces'} <= {s['name'] for s in sources}, 'HyperDX source provisioning failed')
    connection = next((c for c in connections if c.get('name') == 'Management Prometheus' and c.get('isPrometheusEndpoint')), None)
    require(connection is not None, 'Prometheus source missing')
    response = api.request('/v1/prometheus/query?' + urlencode({'query': 'up', 'connectionId': str(connection.get('id') or connection.get('_id'))}))
    require(response.get('status') == 'success' and response.get('data', {}).get('result'), 'HyperDX PromQL query failed')
    ch = LocalHTTP(forward(kube, processes, 'clickstack-clickhouse-clickhouse-headless', 8123))
    auth = 'Basic ' + base64.b64encode(('app:' + values['clickstack-clickhouse-auth']['read-password']).encode()).decode()
    require(re.fullmatch('[0-9]+-[0-9]+', run), 'Synthetic run ID rejected')
    run_id = 'cs-' + run
    if send:
        smoke = module('smoke', ROOT / 'scripts/observability-smoke.py')
        port = forward(kube, processes, 'clickstack-otel-collector', 4318)
        ca = work / 'ca.crt';ca.write_text(values['observability-client-ca']['ca.crt']);ca.chmod(0o600)
        logs, traces = smoke.payloads(run_id=run_id, cluster=CLUSTER)
        smoke.send(f'https://clickstack-otel-collector.observability.svc:{port}', str(ca),
                   values['observability-client-auth']['OTLP_AUTH_TOKEN'], logs, traces, connect_address='127.0.0.1')
    def counts():
        query = ("SELECT count(),uniqExact(LogAttributes['event_id']),uniqExact(TraceId) FROM default.otel_logs "
                 f"WHERE ResourceAttributes['run_id']='{run_id}' FORMAT JSONCompact")
        logs = ch.request('/', query, headers={'Authorization': auth})['data'][0]
        traces = ch.request('/', f"SELECT count(),uniqExact(TraceId) FROM default.otel_traces WHERE ResourceAttributes['run_id']='{run_id}' FORMAT JSONCompact", headers={'Authorization': auth})['data'][0]
        joined = ch.request('/', f"SELECT uniqExact(TraceId) FROM default.otel_logs WHERE ResourceAttributes['run_id']='{run_id}' AND TraceId IN (SELECT TraceId FROM default.otel_traces WHERE ResourceAttributes['run_id']='{run_id}') FORMAT JSONCompact", headers={'Authorization': auth})['data'][0][0]
        return [int(x) for x in [*logs, *traces, joined]] == [100, 100, 10, 10, 10, 10]
    wait(counts, 120, 'Synthetic event counts/correlation not verified')
    print('HyperDX login, PromQL up and synthetic logs=100/traces=10/correlation verified via HTTP APIs.')
    # Do not serialize session cookies or credentials; dispose this client after checking.
    source_ids = [s.get('id') or s.get('_id') for s in sources]
    require(all(isinstance(v, str) and v for v in source_ids), 'Source identity missing')
    result = sorted(source_ids)
    api.request('/logout')
    api.cookies.clear()
    return result


def main():
    os.umask(0o077)
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument('--check-context', action='store_true')
    parser.add_argument('--finalize', action='store_true')
    parser.add_argument('--tunnel-worker', action='store_true')
    args = parser.parse_args()
    action, sha, run = context(os.environ)
    require(not os.environ.get('AWS_PROFILE'), 'Profile override rejected; OIDC environment required')
    if args.check_context:
        print('Bootstrap execution context accepted; no AWS call made.')
        return
    commands, aws = Commands(), AWS(Commands())
    work = Path(os.environ['CLICKSTACK_WORK_DIR'])
    require(work.is_dir() and not work.is_symlink() and work.resolve().parent == Path(os.environ['RUNNER_TEMP']).resolve()
            and work.name.startswith('clickstack-' + run + '.') and work.stat().st_mode & 0o077 == 0, 'Private work directory rejected')
    kube_path = work / 'kubeconfig'
    if args.tunnel_worker:
        require(action == 'deploy')
        access = module('eks_access', ROOT / 'scripts/eks-access.py')
        access.tunnel(access.config.project(), ACCOUNT, 'management', int(os.environ['CLICKSTACK_TUNNEL_PORT']), None, kube_path)
        return
    aws.identity()
    if args.finalize or action == 'cleanup-access':
        require(action in ('deploy', 'cleanup-access'))
        owner_run = run if args.finalize else os.environ['CLEANUP_OWNER_RUN_ID']
        verify_owner_run(commands, owner_run, os.environ['GH_TOKEN'], current=args.finalize)
        aws.cleanup(owner_run, finalizer=args.finalize)
        state = work / 'session.json'
        if state.exists():
            session = json.loads(state.read_text())['ssmSessionId']
            require(re.fullmatch('[A-Za-z0-9_-]+', session))
            aws.get('ssm', 'terminate-session', '--session-id', session)
        return
    targets = aws_preflight(aws)
    docs = render(commands, targets, sha)
    crds = operator_crds(commands, work)
    require(len(crds) == 9, 'Operator CRD inventory changed')
    require(aws.entry() is None, 'Existing access entry requires review; not modified')
    if action == 'preflight':
        print('AWS targets/chart/render verified; no mapping created and Kubernetes checks not performed.')
        return
    processes = Processes(work / 'session.json')
    kube = None
    try:
        print('Stage: temporary-access')
        aws.create_access(run)
        port = free_port()
        os.environ['CLICKSTACK_TUNNEL_PORT'] = str(port)
        processes.start([sys.executable, str(Path(__file__).resolve()), '--tunnel-worker'], session=True)
        kube = Kube(commands, kube_path)
        wait(lambda: kube_path.exists() and listener(port) and processes.session, 60, 'EKS tunnel or owned session ID missing')
        wait(lambda: kube.get('nodes', namespace=None), 120, 'Temporary access not yet usable')
        require(kube.health() >= 1.81, 'Insufficient CPU request headroom')
        require(kube.ready_workloads(), 'Existing workloads are not Ready')
        collision_checks(kube, crds)
        argo_crd = kube.get('crd', 'applications.argoproj.io', namespace=None)
        version = next(v for v in argo_crd['spec']['versions'] if v['name'] == 'v1alpha1')
        operation = version['schema']['openAPIV3Schema']['properties']['operation']['properties']
        require('info' in operation and {'revision', 'revisions', 'resources'} <= set(operation['sync']['properties']), 'Argo operation schema unsupported')
        for doc in docs:
            kube.run('apply', '--server-side', '--dry-run=server', '--field-manager=charlie-clickstack-ci', '-f', '-', obj=doc)
        print('Stage: owned-secret-bundle')
        values = Bundle(kube.secret, kube.create, ROOT).build()
        print('Stage: four-argo-apps')
        for doc in docs:
            kube.register(doc)
        expected_apps = {d['metadata']['name']: d for d in docs if d['kind'] == 'Application'}
        sync(kube, APPS[0], expected=expected_apps[APPS[0]])
        for name in crds:
            wait(lambda n=name: any(c['type'] == 'Established' and c['status'] == 'True' for c in (kube.get('crd', n, namespace=None) or {}).get('status', {}).get('conditions', [])), 120, 'CRD not Established')
        operators = [d for d in kube.get('deployments')['items'] if d['metadata'].get('annotations', {}).get('argocd.argoproj.io/tracking-id', '').startswith(APPS[0] + ':')
                     or d['metadata'].get('labels', {}).get('app.kubernetes.io/instance') == APPS[0]]
        require(len(operators) == 2, 'Two Argo-owned operators required')
        for obj in operators:
            kube.wait_root('deployment', obj['metadata']['name'], 120)
        print('Stage: database-selective-sync')
        dbs = [('clickhouse.com', 'ClickHouseCluster', 'clickstack-clickhouse'), ('clickhouse.com', 'KeeperCluster', 'clickstack-keeper'), ('mongodbcommunity.mongodb.com', 'MongoDBCommunity', 'clickstack-mongodb')]
        sync(kube, APPS[1], expected=expected_apps[APPS[1]], resources=[{'group': g, 'kind': k, 'name': n} for g, k, n in dbs])
        for group, kind, name in dbs:
            kube.wait_root(kind.lower() + '.' + group, name, 900)
        print('Stage: central-and-gateway')
        sync(kube, APPS[1], expected=expected_apps[APPS[1]]);kube.wait_root('deployment', 'clickstack', 600)
        kube.wait_root('deployment', 'clickstack-otel-collector', 600)
        sync(kube, APPS[2], expected=expected_apps[APPS[2]]);kube.wait_root('deployment', 'observability-gateway', 300)
        correlation = run + '-' + os.environ['GITHUB_RUN_ATTEMPT']
        source_ids = verify_runtime(kube, processes, values, work, correlation)
        print('Stage: approved-platform-log-agent')
        sync(kube, APPS[3], expected=expected_apps[APPS[3]]);kube.wait_root('daemonset', 'observability-agent', 300)
        print('Stage: sequential-new-pod-persistence')
        for kind, name in [('deployment', 'clickstack'), ('clickhousecluster.clickhouse.com', 'clickstack-clickhouse'), ('mongodbcommunity.mongodbcommunity.mongodb.com', 'clickstack-mongodb')]:
            kube.restart(kind, name)
        require(verify_runtime(kube, processes, values, work, correlation, send=False) == source_ids, 'HyperDX source identity changed')
        require(kube.health() >= 0 and kube.ready_workloads(), 'Final workload health not verified')
        print('Deployment HTTP/DB persistence verified. Queue backlog recovery and browser interaction remain unverified.')
    finally:
        failures = []
        try:
            aws.cleanup(run, finalizer=True)
            if kube is not None:
                wait(kube.access_revoked, 120, 'Kubernetes authorization revocation not confirmed')
                print('Kubernetes authorization revocation verified before tunnel closure.')
        except Exception:
            failures.append(True)
        for cleanup in (processes.close,
                lambda: aws.get('ssm', 'terminate-session', '--session-id', processes.session) if processes.session else None):
            try:
                cleanup()
            except Exception:
                failures.append(True)
        if failures:
            print('Cleanup incomplete; run owner-checked cleanup-access. No success claimed.', file=sys.stderr)
            raise Rejected('Cleanup incomplete')
        (work / 'session.json').unlink(missing_ok=True)



if __name__ == '__main__':
    try:
        # The helper stops before the workflow timeout, leaving time for always cleanup.
        signal.signal(signal.SIGALRM, lambda *_: (_ for _ in ()).throw(Rejected('Bootstrap deadline exceeded')))
        signal.signal(signal.SIGTERM, lambda *_: (_ for _ in ()).throw(Rejected('Bootstrap interrupted')))
        signal.alarm(70 * 60)
        main()
    except (Exception, KeyboardInterrupt):
        print('ClickStack bootstrap stopped; inspect stage/result without exposing raw credentials.', file=sys.stderr)
        sys.exit(1)
