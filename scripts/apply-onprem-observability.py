#!/usr/bin/env python3
"""Explicit administrator installation of the reviewed onprem collector bundle.

Default is server-side dry-run. Transport/TLS/auth and CRDs must already exist.
No credentials are read, generated, copied or printed by this program.
"""
import argparse
import hashlib
import importlib.util
import json
from pathlib import Path
import subprocess
import sys
import yaml

ROOT = Path(__file__).resolve().parents[1]
SPEC = importlib.util.spec_from_file_location('onprem_render', ROOT / 'scripts/render-onprem-observability.py')
RENDER = importlib.util.module_from_spec(SPEC)
SPEC.loader.exec_module(RENDER)
SECRETS = ('observability-client-auth', 'observability-upstream-auth', 'observability-client-ca',
           'observability-upstream-ca', 'observability-gateway-tls',
           'observability-metrics-query-auth', 'observability-metrics-query-tls')


def validate_bundle(text):
    docs = [d for d in yaml.safe_load_all(text) if d]
    allowed = {'ServiceAccount', 'ConfigMap', 'Secret', 'Service', 'Deployment', 'DaemonSet',
               'ClusterRole', 'ClusterRoleBinding', 'Role', 'RoleBinding', 'NetworkPolicy',
               'PersistentVolumeClaim', 'Prometheus', 'ServiceMonitor', 'PodMonitor'}
    for doc in docs:
        kind, meta = doc['kind'], doc['metadata']
        if kind not in allowed or meta.get('namespace', 'observability') != 'observability':
            raise ValueError('Bundle contains an unexpected resource scope')
        if kind == 'Secret' and meta['name'] != 'monitoring-prometheus-scrape-confg':
            raise ValueError('Only the reviewed public scrape configuration Secret is permitted')
        if kind == 'Service' and doc['spec'].get('type', 'ClusterIP') != 'ClusterIP':
            raise ValueError('Public collector services are forbidden')
    if not any(d['kind'] == 'DaemonSet' and d['metadata']['name'] == 'observability-alloy' for d in docs):
        raise ValueError('Reviewed Alloy daemonset missing')
    if not any(d['kind'] == 'Deployment' and d['metadata']['name'] == 'observability-gateway' for d in docs):
        raise ValueError('Reviewed gateway missing')
    return docs


def run(base, args, data=None):
    result = subprocess.run(base + args, input=data, capture_output=True, text=True, timeout=180)
    if result.returncode:
        # kubectl errors can include object data, API credentials or internal URLs.
        raise RuntimeError('Kubernetes step failed: ' + args[0] + ' (details suppressed)')
    return result.stdout


def install(base, bundle, expected_uid, execute=False):
    docs = validate_bundle(bundle)
    uid = run(base, ['get', 'namespace', 'kube-system', '-o', 'jsonpath={.metadata.uid}']).strip()
    if not expected_uid or uid != expected_uid:
        raise ValueError('Target kube-system UID does not match the reviewed device')
    ns = json.loads(run(base, ['get', 'namespace', 'observability', '-o', 'json']))
    if ns.get('metadata', {}).get('labels', {}).get('pod-security.kubernetes.io/enforce') != 'privileged':
        raise ValueError('Reviewed observability namespace with privileged collector policy is required')
    run(base, ['get', 'storageclass', 'local-path', '-o', 'name'])
    for name in SECRETS:
        run(base, ['get', 'secret', name, '-n', 'observability', '-o', 'name'])
    endpoint = run(base, ['get', 'configmap', 'observability-remote-endpoint', '-n', 'observability',
                          '-o', 'jsonpath={.data.endpoint}']).strip()
    if endpoint != 'https://observability-central.observability.svc:4318':
        raise ValueError('Reviewed authenticated central transport endpoint is required')
    for name in ('prometheuses', 'servicemonitors', 'podmonitors'):
        run(base, ['wait', '--for=condition=Established', 'crd/' + name + '.monitoring.coreos.com', '--timeout=10s'])
    run(base, ['apply', '--dry-run=server', '-n', 'observability', '-f', '-'], bundle)
    applied = False
    if execute:
        # Recheck device identity immediately before effects; never switch context.
        if run(base, ['get', 'namespace', 'kube-system', '-o', 'jsonpath={.metadata.uid}']).strip() != uid:
            raise ValueError('Target identity changed before application')
        run(base, ['apply', '-n', 'observability', '-f', '-'], bundle)
        applied = True
        for doc in docs:
            if doc['kind'] in ('Deployment', 'DaemonSet'):
                run(base, ['rollout', 'status', doc['kind'].lower() + '/' + doc['metadata']['name'],
                           '-n', 'observability', '--timeout=120s'])
    return {'bundleSha256': hashlib.sha256(bundle.encode()).hexdigest(), 'resources': len(docs),
            'targetIdentityVerified': True, 'serverDryRunPassed': True, 'applied': applied,
            'receivingVerified': False,
            'note': 'Readiness is not receiving evidence; verify fresh app-specific central signals separately.'}


def main():
    p = argparse.ArgumentParser(description=__doc__)
    p.add_argument('--kubeconfig', required=True, type=Path)
    p.add_argument('--context', required=True)
    p.add_argument('--expected-kube-system-uid', required=True)
    for name in ('alloy', 'otel', 'prometheus'):
        p.add_argument('--' + name + '-chart', required=True, type=Path)
    p.add_argument('--execute', action='store_true', help='Apply only after the same full server dry-run succeeds')
    args = p.parse_args()
    _, values = RENDER.prepare()
    bundle = RENDER.render(values, {'alloy': args.alloy_chart,
                                   'opentelemetry-collector': args.otel_chart,
                                   'kube-prometheus-stack': args.prometheus_chart})
    base = ['kubectl', '--kubeconfig', str(args.kubeconfig.resolve()), '--context', args.context, '--request-timeout=30s']
    try:
        print(json.dumps(install(base, bundle, args.expected_kube_system_uid, args.execute)))
    except (ValueError, RuntimeError, subprocess.TimeoutExpired):
        print(json.dumps({'completed': False, 'receivingVerified': False,
                          'error': 'Preflight/apply/readiness failed; partial changes are possible after --execute. Inspect scoped resource metadata.'}))
        return 1
    return 0


if __name__ == '__main__':
    sys.exit(main())
