#!/usr/bin/env python3
"""Canonical deployment inputs. Never reads resource state or emits credentials."""
import argparse
import ipaddress
import json
import os
from pathlib import Path
import re
import sys

ROOT = Path(__file__).resolve().parents[1]
STACKS = ('bootstrap/aws', 'account/aws', 'aws/dev/foundation', 'aws/dev/management', 'aws/dev/workload')
ACCOUNT_KEYS = {'aws_account_id', 'operator_principal_arn', 'additional_operator_principal_arns', 'github_oidc_provider_arn', 'github_oidc_subject_prefix'}


class ConfigError(ValueError):
    pass


def require(ok, message):
    if not ok:
        raise ConfigError(message)


def read_json(path):
    def unique(pairs):
        result = {}
        for key, value in pairs:
            require(key not in result, 'Duplicate JSON key; refusing ambiguous configuration.')
            result[key] = value
        return result
    return json.loads(path.read_text(), object_pairs_hook=unique)


def project():
    p = read_json(ROOT / 'config/project.json')
    require(isinstance(p, dict) and set(p) == {'project', 'environment', 'aws_region', 'github_repository', 'github_branch', 'management_cluster_name', 'workload_cluster_name', 'workload_network_policy_enforcing_mode', 'foundation'}, 'Invalid project configuration keys.')
    require(p['project'] == 'charlie' and p['environment'] == 'dev', 'This repository supports charlie/dev; renaming requires a separate migration review.')
    require(isinstance(p['aws_region'], str) and re.fullmatch(r'[a-z]{2}-[a-z]+-\d+', p['aws_region']), 'Invalid AWS region.')
    require(isinstance(p['github_repository'], str) and re.fullmatch(r'[A-Za-z0-9_.-]+/[A-Za-z0-9_.-]+', p['github_repository']), 'Invalid GitHub repository.')
    require(p['github_branch'] == 'main', 'Only main deployment is supported.')
    require(p['management_cluster_name'] == 'charlie-dev-management' and p['workload_cluster_name'] == 'charlie-dev-workload', 'Keep the existing cluster identities; renaming requires migration review.')
    require(p['workload_network_policy_enforcing_mode'] in ('standard', 'strict'), 'Workload network policy enforcing mode must be standard or strict.')
    f = p['foundation']
    require(isinstance(f, dict) and set(f) == {'vpc_cidr', 'availability_zones', 'nat_gateway_mode'}, 'Invalid foundation keys.')
    network = ipaddress.ip_network(f['vpc_cidr'], strict=True)
    require(network.version == 4 and network.prefixlen == 16, 'VPC must be a canonical IPv4 /16.')
    for service in ('172.20.0.0/16', '172.21.0.0/16'):
        require(not network.overlaps(ipaddress.ip_network(service)), 'VPC overlaps EKS service CIDR.')
    azs = f['availability_zones']
    require(isinstance(azs, list) and len(azs) == 2 and all(isinstance(a, str) and re.fullmatch(re.escape(p['aws_region']) + '[a-z]', a) for a in azs) and len(set(azs)) == 2, 'Provide two distinct AZs in the configured region.')
    require(f['nat_gateway_mode'] in ('single', 'per_az'), 'Invalid NAT gateway mode.')
    return p


def account(ci):
    if ci:
        a = {'aws_account_id': os.environ.get('AWS_ACCOUNT_ID', '')}
        for key, env in (('operator_principal_arn', 'EKS_OPERATOR_PRINCIPAL_ARN'), ('additional_operator_principal_arns', 'EKS_ADDITIONAL_OPERATOR_PRINCIPAL_ARNS')):
            if os.environ.get(env):
                a[key] = json.loads(os.environ[env]) if key.startswith('additional') else os.environ[env]
    else:
        path = ROOT / 'config/account.local.json'
        require(path.is_file(), 'Copy config/account.local.json.example to config/account.local.json and set account inputs.')
        a = read_json(path)
    require(isinstance(a, dict) and set(a) <= ACCOUNT_KEYS, 'Account JSON contains unsupported keys; do not put credentials here.')
    account_id = a.get('aws_account_id', '')
    require(isinstance(account_id, str) and re.fullmatch(r'\d{12}', account_id) and account_id != '000000000000', 'A real 12-digit AWS account ID is required.')
    require(not os.environ.get('AWS_ACCOUNT_ID') or os.environ['AWS_ACCOUNT_ID'] == account_id, 'AWS_ACCOUNT_ID conflicts with account configuration.')
    arn_pattern = rf'arn:aws:iam::{account_id}:(role|user)/[A-Za-z0-9/_+=,.@-]+'
    if 'operator_principal_arn' in a:
        require(isinstance(a['operator_principal_arn'], str) and re.fullmatch(arn_pattern, a['operator_principal_arn']), 'Invalid operator IAM ARN (STS session ARNs are not allowed).')
    extra = a.get('additional_operator_principal_arns', [])
    require(isinstance(extra, list) and all(isinstance(v, str) and re.fullmatch(arn_pattern, v) for v in extra) and len(set(extra)) == len(extra) and a.get('operator_principal_arn') not in extra, 'Invalid or duplicate additional operator IAM ARNs.')
    provider = a.get('github_oidc_provider_arn', '')
    require(provider in ('', f'arn:aws:iam::{account_id}:oidc-provider/token.actions.githubusercontent.com'), 'Invalid GitHub OIDC provider ARN.')
    if 'github_oidc_subject_prefix' in a:
        require(isinstance(a['github_oidc_subject_prefix'], str) and re.fullmatch(r'repo:[A-Za-z0-9_.-]+(@[0-9]+)?/[A-Za-z0-9_.-]+(@[0-9]+)?', a['github_oidc_subject_prefix']), 'Invalid customized OIDC subject prefix.')
    return a


def settings(p, a):
    region = p['aws_region']
    aid = a['aws_account_id']
    values = {'account_id': aid, 'region': region, 'bucket': f"{p['project']}-tfstate-{aid}-{region}", 'role_arn': f"arn:aws:iam::{aid}:role/{p['project']}-{p['environment']}-github-terraform"}
    for env, key in (('AWS_REGION', 'region'), ('AWS_DEFAULT_REGION', 'region'), ('TF_STATE_BUCKET', 'bucket'), ('AWS_ROLE_ARN', 'role_arn')):
        require(not os.environ.get(env) or os.environ[env] == values[key], f'{env} conflicts with canonical project settings.')
    return values


def clean_environment():
    require(not any(k.startswith(('TF_VAR_', 'TF_CLI_ARGS')) for k in os.environ), 'Remove TF_VAR_* and TF_CLI_ARGS*; use canonical project/account inputs.')
    require('TF_DATA_DIR' not in os.environ, 'Custom TF_DATA_DIR is not supported for deployment.')
    require(os.environ.get('TF_WORKSPACE', 'default') == 'default', 'Only the default Terraform workspace is supported.')
    require(not os.environ.get('TF_CLI_CONFIG_FILE'), 'Custom Terraform CLI configuration is not supported for deployment.')


def directory(stack):
    require(stack in STACKS, 'Unsupported deployment stack.')
    return ROOT / ('terraform/environments' if stack.startswith('aws/dev/') else 'terraform') / stack


def backend(s, stack):
    return {'bucket': s['bucket'], 'key': stack + '/terraform.tfstate', 'region': s['region'], 'encrypt': True, 'use_lockfile': True, 'allowed_account_ids': [s['account_id']]}


def check_directory(path, expected):
    legacy = [p for p in path.iterdir() if p.name in ('terraform.tfvars', 'terraform.tfvars.json', 'backend.hcl', 'override.tf', 'override.tf.json') or p.name.endswith(('.auto.tfvars', '.auto.tfvars.json', '_override.tf', '_override.tf.json'))]
    require(not legacy, 'Legacy tfvars/backend/override inputs found. Manually migrate account inputs to config/account.local.json and shared values to config/project.json first.')
    workspace = path / '.terraform/environment'
    require(not workspace.exists() or workspace.read_text().strip() == 'default', 'Existing workspace is not default.')
    metadata = path / '.terraform/terraform.tfstate'
    if metadata.exists():
        data = read_json(metadata)
        b = data.get('backend', {})
        require(isinstance(b, dict) and b.get('type') == 's3' and isinstance(b.get('config'), dict), 'Unrecognized initialized backend; manual review required.')
        require(all(b['config'].get(k) == v for k, v in expected.items()), 'Initialized backend differs from canonical settings; manual state review required. No automatic migration.')
    else:
        require(not (path / '.terraform').exists(), 'Initialized directory has no readable backend metadata; manual review required.')
    require(not (path / 'terraform.tfstate').exists(), 'Local resource state found; migrate it manually before using the S3 wrapper.')


def variables(p, a, stack):
    common = {key: p[key] for key in ('project', 'environment', 'aws_region')}
    common['aws_account_id'] = a['aws_account_id']
    if stack in ('account/aws', 'aws/dev/foundation'):
        common.update({key: p[key] for key in ('management_cluster_name', 'workload_cluster_name')})
    if stack == 'account/aws':
        require('github_oidc_subject_prefix' in a, 'Set github_oidc_subject_prefix in config/account.local.json from the GitHub OIDC customization API before preparing account/aws.')
        prefix = a['github_oidc_subject_prefix']
        require(re.sub(r'@[0-9]+', '', prefix) == 'repo:' + p['github_repository'], 'OIDC subject prefix must match the configured repository.')
        common['github_oidc_subject_prefix'] = prefix
        common['github_oidc_provider_arn'] = a.get('github_oidc_provider_arn', '')
    if stack == 'aws/dev/foundation':
        common.update(p['foundation'])
    if stack in ('aws/dev/management', 'aws/dev/workload'):
        require('operator_principal_arn' in a, 'An existing operator IAM ARN is required before deploying EKS.')
        common.update(operator_principal_arn=a['operator_principal_arn'], additional_operator_principal_arns=a.get('additional_operator_principal_arns', []))
        common['state_bucket_name'] = settings(p, a)['bucket']
    if stack == 'aws/dev/workload':
        common['network_policy_enforcing_mode'] = p['workload_network_policy_enforcing_mode']
    return common


def main():
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument('command', choices=('check', 'settings', 'prepare'))
    parser.add_argument('--ci', action='store_true')
    parser.add_argument('--deploy-context', action='store_true')
    parser.add_argument('--stack', choices=STACKS)
    parser.add_argument('--output-dir', type=Path)
    args = parser.parse_args()
    try:
        p = project()
        if args.command == 'check':
            print('Project configuration valid.')
            return
        clean_environment()
        a = account(args.ci)
        s = settings(p, a)
        if args.deploy_context:
            require(args.ci and os.environ.get('GITHUB_REPOSITORY') == p['github_repository'] and os.environ.get('GITHUB_REF') == 'refs/heads/' + p['github_branch'], 'Deployment repository/branch does not match project trust settings.')
            require(os.environ.get('AWS_ROLE_ARN') == s['role_arn'], 'Set TERRAFORM_APPLY_ROLE_ARN to the canonical CI role.')
        if args.command == 'settings':
            for key, value in s.items():
                print(f'{key}={value}')
            return
        require(args.stack and args.output_dir is not None, 'prepare requires --stack and --output-dir.')
        b = backend(s, args.stack)
        check_directory(directory(args.stack), b)
        v = variables(p, a, args.stack)
        args.output_dir.mkdir(parents=True, exist_ok=True, mode=0o700)
        for name, value in (('inputs.tfvars.json', v), ('backend.json', b)):
            target = args.output_dir / name
            target.write_text(json.dumps(value, indent=2) + '\n')
            target.chmod(0o600)
        print(s['account_id'], s['region'], s['bucket'])
    except ConfigError as error:
        print(str(error), file=sys.stderr)
        return 1
    except (ValueError, TypeError, KeyError, OSError, AttributeError):
        # Exceptions from JSON/parsers may contain input values: never dump raw data.
        print('Configuration rejected. Check schema, account inputs, environment and legacy/backend files. No AWS/Terraform command was run.', file=sys.stderr)
        return 1
    return 0


if __name__ == '__main__':
    sys.exit(main())
