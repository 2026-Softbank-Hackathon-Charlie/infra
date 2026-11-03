#!/usr/bin/env python3
"""After the approved Terraform apply, prepare public Helm values and install the OIDC Secrets.

Only --install-secret mutates Kubernetes. No secret is printed or written to disk.
Headlamp uses the OIDC settings of each kubeconfig user for the token exchange,
so the cluster kubeconfig must carry the client secret and is stored as a Secret.
"""
import argparse
import json
from pathlib import Path
import subprocess

ROOT = Path(__file__).resolve().parents[1]
SCOPES = 'profile,email'  # Headlamp always adds openid.


def cluster_kubeconfig(values, client_secret):
    """Return the Headlamp kubeconfig for public Terraform values and the client secret."""
    clusters, oidc = values['clusters'], values['oidc']
    if not clusters or not client_secret or not oidc.get('issuerURL') or not oidc.get('clientID'):
        raise SystemExit('Terraform outputs are missing clusters or OIDC settings.')
    return {
        'apiVersion': 'v1', 'kind': 'Config', 'current-context': clusters[0]['name'],
        'clusters': [{'name': c['name'], 'cluster': {'server': c['endpoint'], 'certificate-authority-data': c['ca']}}
                     for c in clusters],
        'contexts': [{'name': c['name'], 'context': {'cluster': c['name'], 'user': 'team-oidc'}} for c in clusters],
        'users': [{'name': 'team-oidc', 'user': {'auth-provider': {'name': 'oidc', 'config': {
            'client-id': oidc['clientID'], 'client-secret': client_secret,
            'idp-issuer-url': oidc['issuerURL'], 'extra-scopes': SCOPES}}}}],
    }


def main():
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument('--terraform', default='terraform')
    parser.add_argument('--kubeconfig', type=Path)
    parser.add_argument('--install-secret', action='store_true')
    args = parser.parse_args()
    if args.install_secret and not args.kubeconfig:
        parser.error('--install-secret requires --kubeconfig')
    result = subprocess.run([args.terraform, f'-chdir={ROOT / "terraform/platform/aws/headlamp"}',
                             'output', '-json'], check=True, capture_output=True, text=True)
    outputs = json.loads(result.stdout)
    values = outputs['public_config']['value']
    path = ROOT / '.generated/headlamp/public-values.json'
    path.parent.mkdir(parents=True, exist_ok=True)
    path.write_text(json.dumps(values, indent=2) + '\n', encoding='utf-8')
    print(f'Public Helm values: {path}')
    if args.install_secret:
        base = ['kubectl', '--kubeconfig', str(args.kubeconfig), '--context', 'charlie-dev-management']
        namespace = {'apiVersion': 'v1', 'kind': 'Namespace', 'metadata': {'name': 'headlamp'}}
        client_secret = outputs['client_secret']['value']
        secret = {'apiVersion': 'v1', 'kind': 'Secret', 'metadata': {'name': 'headlamp-oidc', 'namespace': 'headlamp'},
                  'type': 'Opaque', 'stringData': {'client-secret': client_secret}}
        clusters = {'apiVersion': 'v1', 'kind': 'Secret', 'metadata': {'name': 'headlamp-clusters', 'namespace': 'headlamp'},
                    'type': 'Opaque', 'stringData': {'config': json.dumps(cluster_kubeconfig(values, client_secret))}}
        # SSA avoids kubectl's last-applied annotation containing a duplicate secret.
        for resource in (namespace, secret, clusters):
            subprocess.run([*base, 'apply', '--server-side', '--field-manager=headlamp-bootstrap', '-f', '-'],
                           input=json.dumps(resource), text=True, check=True, stdout=subprocess.DEVNULL)
        print('Installed OIDC Secrets; restart Headlamp after any later secret or cluster change.')


if __name__ == '__main__':
    main()
