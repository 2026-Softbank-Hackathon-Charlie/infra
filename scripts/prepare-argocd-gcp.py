#!/usr/bin/env python3
"""Prepare Argo CD GKE federation files offline; never contacts a cluster or issues tokens."""
import argparse
import base64
import copy
import json
from pathlib import Path
import re
import sys
from urllib.parse import urlsplit

ADC_PATH = '/etc/argocd-gcp/credential-configuration.json'
TOKEN_PATH = '/var/run/secrets/gcp/token'
SERVICE_ACCOUNTS = {'controller': 'argocd-application-controller', 'server': 'argocd-server'}
DNS_NAME = re.compile(r'[a-z0-9](?:[-a-z0-9]{0,61}[a-z0-9])?')
PROVIDER_NAME = re.compile(r'projects/[0-9]+/locations/global/workloadIdentityPools/[-a-z0-9]+/providers/[-a-z0-9]+')

def named_merge(existing, additions):
    if not isinstance(existing, list) or any(not isinstance(v, dict) or not v.get('name') for v in existing):
        raise ValueError('Expected named bootstrap values lists.')
    result = copy.deepcopy(existing)
    if len({v['name'] for v in result}) != len(result):
        raise ValueError('Duplicate existing bootstrap item names.')
    for item in additions:
        found = next((v for v in result if v['name'] == item['name']), None)
        if found is not None and found != item:
            raise ValueError('An existing bootstrap item conflicts with GCP configuration.')
        if found is None:
            result.append(copy.deepcopy(item))
    return result

def prepare(outputs, metadata, bootstrap, namespace='argocd', managed_namespaces=None):
    if not all(isinstance(value, dict) for value in [outputs, metadata, bootstrap]):
        raise ValueError('Inputs must be JSON objects.')
    public = {}
    for key, value in outputs.items():
        if isinstance(value, dict) and 'value' in value and 'sensitive' in value:
            if value['sensitive']:
                raise ValueError('Sensitive Terraform outputs are not accepted.')
            value = value['value']
        public[key] = value
    managed_namespaces = managed_namespaces or ['charlie-apps']
    if not DNS_NAME.fullmatch(namespace) or not managed_namespaces or len(set(managed_namespaces)) != len(managed_namespaces):
        raise ValueError('Invalid namespace configuration.')
    if any(not isinstance(n, str) or not DNS_NAME.fullmatch(n) or n in ['kube-system', namespace] for n in managed_namespaces):
        raise ValueError('Managed namespaces must be dedicated application namespaces.')
    provider = public.get('workload_identity_provider', '')
    email = public.get('deploy_service_account_email', '')
    if not isinstance(provider, str) or not PROVIDER_NAME.fullmatch(provider):
        raise ValueError('Expected an exact Google federation provider resource name.')
    if not isinstance(email, str) or not re.fullmatch(r'[a-z][-a-z0-9]*@[-a-z0-9]+\.iam\.gserviceaccount\.com', email):
        raise ValueError('Expected a Google service account email.')
    audience = 'https://iam.googleapis.com/' + provider
    subjects = ['system:serviceaccount:' + namespace + ':' + name for name in SERVICE_ACCOUNTS.values()]
    if public.get('projected_token_audience') != audience or set(public.get('kubernetes_service_account_subjects', [])) != set(subjects):
        raise ValueError('Projected token audience or Kubernetes identities do not match the Argo deployment.')
    expected_adc = {
        'type': 'external_account', 'audience': '//iam.googleapis.com/' + provider,
        'subject_token_type': 'urn:ietf:params:oauth:token-type:jwt',
        'token_url': 'https://sts.googleapis.com/v1/token',
        'service_account_impersonation_url': 'https://iamcredentials.googleapis.com/v1/projects/-/serviceAccounts/' + email + ':generateAccessToken',
        'credential_source': {'file': TOKEN_PATH},
    }
    if public.get('federation_config') != expected_adc:
        raise ValueError('Federation configuration differs from the approved keyless token flow.')
    name, endpoint = metadata.get('name', ''), metadata.get('endpoint', '')
    if not isinstance(name, str) or not DNS_NAME.fullmatch(name) or not isinstance(endpoint, str) or not endpoint:
        raise ValueError('GKE name and endpoint are required.')
    parsed = urlsplit('https://' + endpoint)
    if parsed.hostname != endpoint or parsed.netloc != endpoint or parsed.path or parsed.query or parsed.fragment:
        raise ValueError('GKE endpoint must be a host without credentials, port, path, or scheme.')
    ca = metadata.get('masterAuth', {}).get('clusterCaCertificate', '')
    try:
        decoded = base64.b64decode(ca, validate=True)
    except (ValueError, TypeError):
        raise ValueError('GKE public CA must be base64.') from None
    if b'-----BEGIN CERTIFICATE-----' not in decoded or b'-----END CERTIFICATE-----' not in decoded:
        raise ValueError('GKE public CA must contain a PEM certificate.')
    overlay = {'argo-cd': {}}
    for component, account in SERVICE_ACCOUNTS.items():
        current = bootstrap.get('argo-cd', {}).get(component, {})
        if current.get('serviceAccount', {}).get('name', account) != account:
            raise ValueError('Current Argo service account differs from the trusted subject.')
        existing_env = current.get('env', [])
        if any('value' in item and re.search(r'TOKEN|SECRET|PASSWORD|PRIVATE_KEY|ACCESS_KEY', item.get('name', ''), re.I) for item in existing_env):
            raise ValueError('Inline credential environment values must not be copied into generated files.')
        overlay['argo-cd'][component] = {
            'serviceAccount': {'name': account},
            'env': named_merge(existing_env, [{'name': 'GOOGLE_APPLICATION_CREDENTIALS', 'value': ADC_PATH}]),
            'volumes': named_merge(current.get('volumes', []), [
                {'name': 'gcp-identity-token', 'projected': {'sources': [{'serviceAccountToken': {'audience': audience, 'expirationSeconds': 3600, 'path': 'token'}}]}},
                {'name': 'gcp-credential-config', 'configMap': {'name': 'argocd-gcp-wif'}}]),
            'volumeMounts': named_merge(current.get('volumeMounts', []), [
                {'name': 'gcp-identity-token', 'mountPath': '/var/run/secrets/gcp', 'readOnly': True},
                {'name': 'gcp-credential-config', 'mountPath': '/etc/argocd-gcp', 'readOnly': True}]),
        }
    config_map = {'apiVersion': 'v1', 'kind': 'ConfigMap', 'metadata': {'name': 'argocd-gcp-wif', 'namespace': namespace},
                  'data': {'credential-configuration.json': json.dumps(expected_adc)}}
    cluster = {'apiVersion': 'v1', 'kind': 'Secret',
        'metadata': {'name': 'gcp-' + name, 'namespace': namespace, 'labels': {'argocd.argoproj.io/secret-type': 'cluster'}},
        'type': 'Opaque', 'stringData': {'name': name, 'server': 'https://' + endpoint,
            'namespaces': ','.join(managed_namespaces), 'clusterResources': 'false',
            'config': json.dumps({'execProviderConfig': {'command': 'argocd-k8s-auth', 'args': ['gcp'],
                'apiVersion': 'client.authentication.k8s.io/v1beta1',
                'env': {'GOOGLE_APPLICATION_CREDENTIALS': ADC_PATH}},
                'tlsClientConfig': {'insecure': False, 'caData': ca}})}}
    return {'bootstrap-overlay.json': overlay,
        'management-manifests.json': {'apiVersion': 'v1', 'kind': 'List', 'items': [config_map, cluster]},
        'gke-rbac-values.json': {'googleServiceAccountEmail': email, 'managedNamespaces': managed_namespaces, 'createNamespaces': False}}

def main():
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument('--terraform-outputs', type=Path, required=True)
    parser.add_argument('--gke-metadata', type=Path, required=True)
    parser.add_argument('--bootstrap-values', type=Path, required=True)
    parser.add_argument('--output-dir', type=Path, required=True)
    parser.add_argument('--argocd-namespace', default='argocd')
    parser.add_argument('--managed-namespace', action='append')
    args = parser.parse_args()
    try:
        files = prepare(*[json.loads(p.read_text(encoding='utf-8-sig')) for p in [args.terraform_outputs, args.gke_metadata, args.bootstrap_values]],
                        namespace=args.argocd_namespace, managed_namespaces=args.managed_namespace)
        serialized = {name: json.dumps(value, indent=2) + '\n' for name, value in files.items()}
        if any((args.output_dir / name).exists() and (args.output_dir / name).read_text(encoding='utf-8') != text for name, text in serialized.items()):
            raise ValueError('Output files differ; review changes in a new output directory.')
        args.output_dir.mkdir(parents=True, exist_ok=True)
        for name, text in serialized.items():
            (args.output_dir / name).write_text(text, encoding='utf-8')
    except (OSError, ValueError, TypeError, AttributeError):
        print('Public inputs rejected or output cannot be written; no cluster operation was performed.', file=sys.stderr)
        return 1
    print('Offline preparation complete: bootstrap overlay, public federation/cluster manifests and GKE RBAC values. No cluster operation was performed.')
    return 0

if __name__ == '__main__':
    sys.exit(main())
