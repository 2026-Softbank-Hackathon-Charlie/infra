#!/usr/bin/env python3
"""Preview GKE registration offline; --install-secret requires prior live-change approval.

Only the opt-in path reads the existing Secret, updates its config in memory and
restarts Headlamp. No Google token is generated, stored or printed.
"""
import argparse
import base64
import binascii
from copy import deepcopy
import ipaddress
import json
from pathlib import Path
import re
import subprocess

import yaml


def cluster_entry(metadata):
    try:
        name, endpoint = metadata['name'], metadata['endpoint']
        ca = metadata['masterAuth']['clusterCaCertificate']
        address = ipaddress.ip_address(endpoint)
        certificate = base64.b64decode(ca, validate=True)
        valid = (metadata.get('status') == 'RUNNING' and isinstance(name, str)
                 and re.fullmatch(r'[a-zA-Z0-9][a-zA-Z0-9._-]*', name)
                 and not (address.is_loopback or address.is_unspecified or address.is_link_local or address.is_multicast)
                 and certificate.startswith(b'-----BEGIN CERTIFICATE-----')
                 and b'-----END CERTIFICATE-----' in certificate)
        if not valid:
            raise ValueError
    except (KeyError, TypeError, ValueError, binascii.Error):
        raise ValueError('Expected RUNNING GKE metadata with a name, API IP and base64 CA certificate.') from None
    host = f'[{address}]' if address.version == 6 else str(address)
    return {'name': name, 'cluster': {'server': f'https://{host}', 'certificate-authority-data': ca}}


def merge_cluster(config, metadata):
    """Add one token-login context without modifying or copying AWS credentials."""
    cluster = cluster_entry(metadata)
    name, user = cluster['name'], f'gke-personal:{cluster["name"]}'
    additions = {'clusters': cluster,
                 'contexts': {'name': name, 'context': {'cluster': name, 'user': user}},
                 'users': {'name': user, 'user': {}}}
    if not isinstance(config, dict) or config.get('kind') != 'Config' or config.get('apiVersion') != 'v1':
        raise ValueError('Existing Secret config is not a v1 kubeconfig.')
    result = deepcopy(config)
    for kind, addition in additions.items():
        entries = result.get(kind)
        if not isinstance(entries, list) or any(not isinstance(e, dict) or not isinstance(e.get('name'), str) for e in entries):
            raise ValueError(f'Existing kubeconfig {kind} entries are invalid.')
        names = [entry['name'] for entry in entries]
        if len(names) != len(set(names)):
            raise ValueError(f'Existing kubeconfig has duplicate {kind} names.')
        matching = [entry for entry in entries if entry['name'] == addition['name']]
        if matching and matching[0] != addition:
            raise ValueError(f'Refusing to overwrite an existing {kind} entry with the GKE name.')
        if not matching:
            entries.append(addition)
    return result


def kubectl(base, args, payload=None):
    try:
        result = subprocess.run([*base, *args], input=payload, capture_output=True,
                                text=True, encoding='utf-8', check=False)
    except OSError:
        raise SystemExit('Cannot run kubectl; no command output or credentials were printed.') from None
    if result.returncode:
        raise SystemExit(f'kubectl {args[0]} failed (exit {result.returncode}); output suppressed to protect Secret data.')
    return result.stdout


def install(metadata, kubeconfig, context):
    base = ['kubectl', '--kubeconfig', str(kubeconfig), '--context', context, '--namespace', 'headlamp']
    raw = kubectl(base, ['get', 'secret', 'headlamp-clusters', '-o', 'json'])
    try:
        secret = json.loads(raw)
        info = secret['metadata']
        if (secret.get('kind') != 'Secret' or info.get('name') != 'headlamp-clusters'
                or info.get('namespace') != 'headlamp' or not info.get('resourceVersion') or secret.get('immutable')):
            raise ValueError
        config = yaml.safe_load(base64.b64decode(secret['data']['config'], validate=True).decode('utf-8'))
    except (KeyError, TypeError, ValueError, binascii.Error, yaml.YAMLError):
        raise SystemExit('Existing Headlamp Secret is invalid or immutable; no changes made.') from None
    try:
        merged = merge_cluster(config, metadata)
    except ValueError as error:
        raise SystemExit(str(error)) from None
    if merged == config:
        print('GKE context is already registered; Secret and deployment unchanged.')
        return
    # Preserve every other field and use resourceVersion to reject concurrent updates.
    secret['data']['config'] = base64.b64encode(json.dumps(merged).encode('utf-8')).decode('ascii')
    kubectl(base, ['replace', '-f', '-'], json.dumps(secret))
    try:
        kubectl(base, ['rollout', 'restart', 'deployment/headlamp'])
    except SystemExit:
        raise SystemExit('Secret updated, but Headlamp restart failed. Restart deployment/headlamp in namespace headlamp after review.') from None
    print('Registered GKE without stored user credentials and requested a Headlamp restart. Verify rollout and login separately.')


def main(argv=None):
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument('--metadata', type=Path, required=True, help='Public gcloud cluster describe JSON, not kubeconfig')
    parser.add_argument('--kubeconfig', type=Path, help='Explicit management-cluster access file')
    parser.add_argument('--context', help='Explicit management-cluster context')
    parser.add_argument('--install-secret', '--apply', action='store_true', help='Approved Secret update and Headlamp restart')
    args = parser.parse_args(argv)
    if args.install_secret and (not args.kubeconfig or not args.context):
        parser.error('--install-secret requires both --kubeconfig and --context')
    try:
        metadata = json.loads(args.metadata.read_text(encoding='utf-8-sig'))
        cluster = cluster_entry(metadata)
    except (OSError, ValueError) as error:
        raise SystemExit('Cannot read valid public GKE cluster metadata; no cluster access attempted.') from None
    print(json.dumps({'cluster': cluster['name'], 'endpoint': cluster['cluster']['server'],
                      'authentication': 'personal Google access token; existing IAM permissions',
                      'mode': 'install' if args.install_secret else 'offline preview',
                      'planned_change': 'headlamp/headlamp-clusters config plus deployment/headlamp restart'}, indent=2))
    if args.install_secret:
        install(metadata, args.kubeconfig, args.context)


if __name__ == '__main__':
    main()
