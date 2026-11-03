#!/usr/bin/env python3
"""Preview the dedicated Google kubeconfig; --install-secret opts into a live write.

The legacy gke alias is preserved for rollback. Credentials are never accepted in
this kubeconfig. Secret contents stay in memory and kubectl command output is
suppressed. This helper does not restart workloads: a Helm rollout must follow,
or restart only deployment/headlamp-gke-google after a standalone config update.
"""
import argparse
import base64
import binascii
from copy import deepcopy
import importlib.util
import json
from pathlib import Path
import subprocess

import yaml

SECRET = 'headlamp-gke-google-clusters'
spec = importlib.util.spec_from_file_location('register_headlamp_gke', Path(__file__).with_name('register-headlamp-gke.py'))
registration = importlib.util.module_from_spec(spec)
spec.loader.exec_module(registration)


def prepare_config(config, metadata):
    """Validate a dedicated credentialless config, then add the actual GKE name."""
    cluster = registration.cluster_entry(metadata)
    name = cluster['name']
    context = {'name': name, 'context': {'cluster': name, 'user': 'google'}}
    if config is None:
        return {'apiVersion': 'v1', 'kind': 'Config', 'current-context': name,
                'clusters': [cluster], 'contexts': [context],
                'users': [{'name': 'google', 'user': {}}]}
    if not isinstance(config, dict) or config.get('apiVersion') != 'v1' or config.get('kind') != 'Config':
        raise ValueError('Existing dedicated config must be a v1 kubeconfig.')
    for kind in ('clusters', 'contexts', 'users'):
        entries = config.get(kind)
        if (not isinstance(entries, list) or not entries
                or any(not isinstance(e, dict) or not isinstance(e.get('name'), str) for e in entries)
                or len({e['name'] for e in entries}) != len(entries)):
            raise ValueError('Existing dedicated config has missing, invalid or duplicate entries.')
    if config['users'] != [{'name': 'google', 'user': {}}]:
        raise ValueError('Dedicated Google config must contain only an empty google user.')
    cluster_names = {entry['name'] for entry in config['clusters']}
    context_names = {entry['name'] for entry in config['contexts']}
    if (not cluster_names <= {'gke', name} or context_names != cluster_names
            or config.get('current-context') not in context_names):
        raise ValueError('Dedicated Google config contains unexpected cluster/context references.')
    for entry in config['clusters']:
        if entry != {'name': entry['name'], 'cluster': cluster['cluster']}:
            raise ValueError('Existing GKE endpoint or CA conflicts with the validated metadata.')
    for entry in config['contexts']:
        if entry != {'name': entry['name'], 'context': {'cluster': entry['name'], 'user': 'google'}}:
            raise ValueError('Existing GKE context has unexpected authentication or cluster references.')
    result = deepcopy(config)
    if name not in cluster_names:
        result['clusters'].append(cluster)
        result['contexts'].append(context)
    return result


def kubectl(base, args, payload=None):
    try:
        result = subprocess.run([*base, *args], input=payload, capture_output=True,
                                text=True, encoding='utf-8', check=False)
    except OSError:
        raise SystemExit('Cannot run kubectl; command output and Secret data were not printed.') from None
    if result.returncode:
        raise SystemExit(f'kubectl {args[0]} failed (exit {result.returncode}); output suppressed to protect Secret data.')
    return result.stdout


def install(metadata, kubeconfig, context):
    prepared = prepare_config(None, metadata)
    base = ['kubectl', '--kubeconfig', str(kubeconfig), '--context', context, '--namespace', 'headlamp']
    raw = kubectl(base, ['get', 'secret', SECRET, '--ignore-not-found', '-o', 'json'])
    if raw.strip():
        try:
            secret = json.loads(raw)
            info = secret['metadata']
            if (secret.get('apiVersion') != 'v1' or secret.get('kind') != 'Secret'
                    or secret.get('type') != 'Opaque' or secret.get('immutable')
                    or info.get('name') != SECRET or info.get('namespace') != 'headlamp'
                    or not isinstance(info.get('resourceVersion'), str) or not info['resourceVersion']
                    or 'stringData' in secret):
                raise ValueError
            config = yaml.safe_load(base64.b64decode(secret['data']['config'], validate=True).decode('utf-8'))
            if not isinstance(config, dict):
                raise ValueError
            prepared = prepare_config(config, metadata)
        except (KeyError, TypeError, ValueError, AttributeError, binascii.Error, yaml.YAMLError):
            raise SystemExit('Existing dedicated Google Secret is invalid, immutable or conflicts with GKE metadata; no changes made.') from None
        if prepared == config:
            print(f'{SECRET} already contains the actual GKE context; no changes made.')
            return
        operation = 'replace'
    else:
        secret = {'apiVersion': 'v1', 'kind': 'Secret', 'type': 'Opaque',
                  'metadata': {'name': SECRET, 'namespace': 'headlamp'}, 'data': {}}
        operation = 'create'
    # Preserve metadata, including resourceVersion for optimistic concurrency.
    secret['data']['config'] = base64.b64encode(json.dumps(prepared).encode('utf-8')).decode('ascii')
    kubectl(base, [operation, '-f', '-'], json.dumps(secret))
    print(f'Prepared {SECRET}; no OAuth or user credentials stored. Follow with the approved Helm rollout.')


def main(argv=None):
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument('--metadata', type=Path, required=True, help='Public gcloud cluster describe JSON')
    parser.add_argument('--kubeconfig', type=Path, help='Explicit management-cluster access file')
    parser.add_argument('--context', help='Explicit management-cluster context')
    parser.add_argument('--install-secret', action='store_true', help='Approved dedicated Secret create/update only')
    args = parser.parse_args(argv)
    if args.install_secret and (not args.kubeconfig or not args.context):
        parser.error('--install-secret requires both --kubeconfig and --context')
    try:
        metadata = json.loads(args.metadata.read_text(encoding='utf-8-sig'))
        cluster = registration.cluster_entry(metadata)
    except (OSError, ValueError):
        raise SystemExit('Cannot read valid public GKE metadata; no cluster access attempted.') from None
    print(json.dumps({'secret': SECRET, 'namespace': 'headlamp', 'cluster': cluster['name'],
                      'legacy_alias_preserved_if_present': 'gke',
                      'mode': 'install' if args.install_secret else 'offline preview'}, indent=2))
    if args.install_secret:
        install(metadata, args.kubeconfig, args.context)


if __name__ == '__main__':
    main()
