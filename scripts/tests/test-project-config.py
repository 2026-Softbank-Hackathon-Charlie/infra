"""Canonical inputs and deployment rejection contracts; no AWS access."""
import importlib.util
import json
import os
from pathlib import Path
import subprocess
import sys
import tempfile
import unittest
from unittest.mock import patch

ROOT = Path(__file__).resolve().parents[2]
spec = importlib.util.spec_from_file_location('config', ROOT / 'scripts/project-config.py')
cfg = importlib.util.module_from_spec(spec)
spec.loader.exec_module(cfg)


class ConfigTests(unittest.TestCase):
    def setUp(self):
        self.temp = tempfile.TemporaryDirectory()
        self.addCleanup(self.temp.cleanup)
        self.root = Path(self.temp.name)
        (self.root / 'config').mkdir()
        self.p = json.loads((ROOT / 'config/project.json').read_text())
        self.a = {'aws_account_id': '123456789012', 'operator_principal_arn': 'arn:aws:iam::123456789012:role/operator'}
        self.write_project()
        (self.root / 'config/account.local.json').write_text(json.dumps(self.a))
        self.addCleanup(patch.stopall)
        patch.object(cfg, 'ROOT', self.root).start()
        patch.dict(os.environ, {}, clear=True).start()
        for stack in cfg.STACKS:
            cfg.directory(stack).mkdir(parents=True)

    def write_project(self):
        (self.root / 'config/project.json').write_text(json.dumps(self.p))

    def test_local_ci_parity_and_scoped_inputs(self):
        os.environ.update(AWS_ACCOUNT_ID=self.a['aws_account_id'], EKS_OPERATOR_PRINCIPAL_ARN=self.a['operator_principal_arn'])
        local, ci = cfg.account(False), cfg.account(True)
        for stack in cfg.STACKS:
            self.assertEqual(cfg.variables(self.p, local, stack), cfg.variables(self.p, ci, stack))
        self.assertNotIn('management_cluster_name', cfg.variables(self.p, local, 'bootstrap/aws'))
        self.assertNotIn('vpc_cidr', cfg.variables(self.p, local, 'account/aws'))
        self.p['foundation']['nat_gateway_mode'] = 'single'
        self.p['foundation']['vpc_cidr'] = '10.80.0.0/16'
        self.assertEqual(cfg.variables(self.p, ci, 'aws/dev/foundation')['nat_gateway_mode'], 'single')
        self.assertEqual(cfg.variables(self.p, local, 'aws/dev/foundation'), cfg.variables(self.p, ci, 'aws/dev/foundation'))

    def test_immutable_oidc_prefix_is_forwarded_without_removing_ids(self):
        prefix = 'repo:2026-Softbank-Hackathon-Charlie@350278309/infra@1442353250'
        self.a['github_oidc_subject_prefix'] = prefix
        (self.root / 'config/account.local.json').write_text(json.dumps(self.a))
        inputs = cfg.variables(cfg.project(), cfg.account(False), 'account/aws')
        self.assertEqual(inputs['github_oidc_subject_prefix'], prefix)
        # The helper checks syntax/name consistency, not ID authenticity.
        # Production IDs must come from GitHub's sub_claim_prefix API response.
        self.a['github_oidc_subject_prefix'] = 'repo:other@111/infra@222'
        with self.assertRaises(ValueError):
            cfg.variables(self.p, self.a, 'account/aws')

    def test_original_names_and_state_keys(self):
        s = cfg.settings(self.p, self.a)
        self.assertEqual(s['bucket'], 'charlie-tfstate-123456789012-ap-northeast-2')
        self.assertEqual(s['role_arn'], 'arn:aws:iam::123456789012:role/charlie-dev-github-terraform')
        for stack in cfg.STACKS:
            self.assertEqual(cfg.backend(s, stack)['key'], stack + '/terraform.tfstate')

    def test_schema_and_network_fail_closed(self):
        for key, val in [('extra', True), ('aws_region', 'wrong'), ('project', 'other'), ('environment', 'prod'), ('github_repository', 'bad repo'), ('workload_cluster_name', 'charlie-dev-management')]:
            original = dict(self.p)
            self.p[key] = val
            self.write_project()
            with self.assertRaises((ValueError, TypeError)):
                cfg.project()
            self.p = original
        for f in ({'vpc_cidr': '10.40.1.0/16'}, {'vpc_cidr': '172.20.0.0/16'}, {'availability_zones': ['ap-northeast-2a']}, {'nat_gateway_mode': 'none'}):
            original = dict(self.p['foundation'])
            self.p['foundation'].update(f)
            self.write_project()
            with self.assertRaises(ValueError):
                cfg.project()
            self.p['foundation'] = original

    def test_hidden_inputs_rejected(self):
        for key, value in [('TF_VAR_project', 'other'), ('TF_VAR_operator_principal_arn', 'hidden'), ('TF_CLI_ARGS_plan', '-var=x'), ('TF_DATA_DIR', '/tmp/elsewhere'), ('TF_WORKSPACE', 'other'), ('TF_CLI_CONFIG_FILE', '/tmp/config')]:
            with patch.dict(os.environ, {key: value}):
                with self.assertRaises(ValueError):
                    cfg.clean_environment()
        os.environ['TF_WORKSPACE'] = 'default'
        cfg.clean_environment()

    def test_legacy_files_and_workspace_rejected(self):
        path = cfg.directory('bootstrap/aws')
        expected = cfg.backend(cfg.settings(self.p, self.a), 'bootstrap/aws')
        for name in ('terraform.tfvars', 'terraform.tfvars.json', 'x.auto.tfvars', 'x.auto.tfvars.json', 'backend.hcl', 'override.tf', 'x_override.tf.json', 'terraform.tfstate'):
            p = path / name
            p.write_text('secret')
            with self.assertRaises(ValueError):
                cfg.check_directory(path, expected)
            p.unlink()
        (path / '.terraform').mkdir()
        (path / '.terraform/environment').write_text('other')
        with self.assertRaises(ValueError):
            cfg.check_directory(path, expected)

    def test_backend_mismatch_and_corruption_rejected(self):
        path = cfg.directory('bootstrap/aws')
        expected = cfg.backend(cfg.settings(self.p, self.a), 'bootstrap/aws')
        metadata = path / '.terraform/terraform.tfstate'
        metadata.parent.mkdir()
        for key, value in [('bucket', 'other'), ('key', 'account/aws/terraform.tfstate'), ('region', 'us-east-1'), ('allowed_account_ids', ['999999999999']), ('encrypt', False), ('use_lockfile', False)]:
            metadata.write_text(json.dumps({'backend': {'type': 's3', 'config': dict(expected, **{key: value})}}))
            with self.assertRaises(ValueError):
                cfg.check_directory(path, expected)
        metadata.write_text('invalid secret')
        with self.assertRaises(ValueError):
            cfg.check_directory(path, expected)
        metadata.write_text(json.dumps({'backend': {'type': 's3', 'config': expected}}))
        cfg.check_directory(path, expected)

    def test_account_conflicts_and_credentials_rejected(self):
        with patch.dict(os.environ, {'AWS_ACCOUNT_ID': '999999999999'}):
            with self.assertRaises(ValueError):
                cfg.account(False)
        for key, value in [('credentials', 'secret'), ('operator_principal_arn', 'arn:aws:sts::123456789012:assumed-role/operator/session')]:
            (self.root / 'config/account.local.json').write_text(json.dumps(dict(self.a, **{key: value})))
            with self.assertRaises(ValueError):
                cfg.account(False)
        with patch.dict(os.environ, {'AWS_REGION': 'us-east-1'}):
            with self.assertRaises(ValueError):
                cfg.settings(self.p, self.a)

    def test_cli_fork_check_and_deploy_context(self):
        # Public config validation is independent of the fork's repository identity.
        result = subprocess.run([sys.executable, str(ROOT / 'scripts/project-config.py'), 'check'], capture_output=True, text=True, env=dict(os.environ, GITHUB_REPOSITORY='fork/infra'))
        self.assertEqual(result.returncode, 0, result.stderr)
        env = dict(os.environ, AWS_ACCOUNT_ID=self.a['aws_account_id'], GITHUB_REPOSITORY='fork/infra', GITHUB_REF='refs/heads/main', AWS_ROLE_ARN=cfg.settings(self.p, self.a)['role_arn'])
        command = [sys.executable, str(ROOT / 'scripts/project-config.py'), 'settings', '--ci', '--deploy-context']
        self.assertNotEqual(subprocess.run(command, env=env, capture_output=True).returncode, 0)
        env['GITHUB_REPOSITORY'] = self.p['github_repository']
        self.assertEqual(subprocess.run(command, env=env, capture_output=True).returncode, 0)


if __name__ == '__main__':
    unittest.main()
