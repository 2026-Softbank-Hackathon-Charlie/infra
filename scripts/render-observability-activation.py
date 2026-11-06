#!/usr/bin/env python3
"""Render a fixed observability-only proposal. No Kubernetes/AWS API or apply operation."""
import argparse
import copy
import json
import os
from pathlib import Path
import re
import subprocess
from urllib.parse import urlsplit
import yaml

ROOT = Path(__file__).resolve().parents[1]
APPLICATIONS = {
    'charlie-management-clickstack', 'charlie-workload-kube-prometheus-stack',
    *{f'charlie-{cluster}-observability-{component}'
      for cluster in ('management', 'workload') for component in ('agent', 'gateway', 'boundary')},
}

def render(revision, targets):
    if not re.fullmatch(r'[a-f0-9]{40}', revision):
        raise ValueError('revision must be a full immutable Git SHA')
    if set(targets) != {'management', 'workload'}:
        raise ValueError('exactly management/workload targets are required')
    for purpose, target in targets.items():
        if (not isinstance(target, dict) or set(target) != {'endpoint', 'name', 'region', 'vpc_id'}
                or not all(isinstance(value, str) for value in target.values())):
            raise ValueError('only public target metadata is accepted')
        u = urlsplit(target['endpoint'])
        if (u.scheme != 'https' or not u.hostname or not u.hostname.endswith('.eks.amazonaws.com')
                or u.username or u.password or u.port or u.path or u.query or u.fragment):
            raise ValueError('target must be a credential-free HTTPS EKS endpoint')
        if (target['name'] != f'charlie-dev-{purpose}' or target['region'] != 'ap-northeast-2'
                or target['vpc_id'] != 'vpc-04b7c6a79ad46fcdc'):
            raise ValueError('target is outside the reviewed AWS activation scope')
    values = json.loads((ROOT / 'clusters/aws-dev-management/values/observability-activation.yaml').read_text())
    values.update(repoURL='https://github.com/2026-Softbank-Hackathon-Charlie/infra.git',
                  revision=revision, targets=copy.deepcopy(targets))
    # Values on stdin contain public metadata only; no token/certificate/private key inputs.
    result = subprocess.run([os.environ.get('HELM', 'helm'), 'template', 'charlie-addons',
        str(ROOT / 'helm/gitops'), '-n', 'argocd', '--kube-version', '1.35.0', '-f', '-'],
        input=json.dumps(values), capture_output=True, text=True)
    if result.returncode:
        raise ValueError('Helm render failed: ' + result.stderr)
    docs = [x for x in yaml.safe_load_all(result.stdout) if x]
    selected = [d for d in docs if
                (d['kind'] == 'AppProject' and d['metadata']['name'] == 'charlie-observability') or
                (d['kind'] == 'Application' and d['metadata']['name'] in APPLICATIONS)]
    if {d['metadata']['name'] for d in selected} != APPLICATIONS | {'charlie-observability'}:
        raise ValueError('observability selection differs from the reviewed allowlist')
    for doc in selected:
        if doc['kind'] == 'Application':
            if 'automated' in doc['spec'].get('syncPolicy', {}):
                raise ValueError('backend application must remain manually synchronized')
            sources = doc['spec'].get('sources') or [doc['spec']['source']]
            for source in sources:
                if source['repoURL'] == values['repoURL'] and source['targetRevision'] != revision:
                    raise ValueError('repository source is not pinned to the reviewed SHA')
    return selected, values


def main():
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument('--revision', required=True)
    parser.add_argument('--targets-file', type=Path, required=True,
                        help='public metadata mapping only; never provide kubeconfig/credentials')
    parser.add_argument('--output-dir', type=Path, required=True)
    args = parser.parse_args()
    try:
        selected, values = render(args.revision, json.loads(args.targets_file.read_text()))
    except (ValueError, KeyError, OSError) as error:
        parser.error(str(error))
    args.output_dir.mkdir(parents=True, exist_ok=True)
    (args.output_dir / 'selected-applications.yaml').write_text(yaml.safe_dump_all(selected, sort_keys=False))
    (args.output_dir / 'root-runtime-values.json').write_text(json.dumps(values, indent=2) + '\n')
    print('Rendered AppProject1 + Application8; no API access or deployment performed.')
    print('Root/baseline pin patches and individual resource syncs still require execution approval.')

if __name__ == '__main__':
    main()
