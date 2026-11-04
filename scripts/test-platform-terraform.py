"""Credential-free regression tests for the isolated HopCharlie validator."""
import importlib.util
import os
from pathlib import Path
import subprocess
import tempfile
import unittest
from unittest.mock import patch

ROOT = Path(__file__).resolve().parents[1]
SCRIPT = ROOT / 'scripts/check-platform-terraform.py'
ASSETS = {
    'backend.tf', 'main.tf', 'eks.tf', 'app-build.tf', 'tests/app-build.tftest.hcl', 'image-publish.tf', 'tests/image-publish.tftest.hcl', 'app-storage.tf', 'tests/app-storage.tftest.hcl', 'outputs.tf', 'variables.tf', 'versions.tf',
    '.terraform.lock.hcl',
    'state/backend.tf', 'state/main.tf', 'state/.terraform.lock.hcl',
    'dns/backend.tf', 'dns/main.tf', 'dns/.terraform.lock.hcl',
    'host/envs.yaml', 'host/apps.yaml',
}


def load_script(path, name):
    spec = importlib.util.spec_from_file_location(name, path)
    module = importlib.util.module_from_spec(spec)
    spec.loader.exec_module(module)
    return module


class PlatformTerraformTests(unittest.TestCase):
    def test_validator_exists(self):
        self.assertTrue(SCRIPT.is_file(), 'The isolated platform validator is missing')

    def fixture(self, work):
        source = work / 'source'
        for name in ASSETS:
            path = source / name
            path.parent.mkdir(parents=True, exist_ok=True)
            path.write_text('committed asset\n', encoding='utf-8')
        for name in ('terraform.tfvars', 'local.auto.tfvars.json', 'terraform.tfstate',
                     'tfplan', 'override.tf', 'local_override.tf', 'backend.hcl',
                     '.terraform/provider.tf', '.aws/credentials', 'host/.env',
                     'host/local-secret.yaml', 'untracked.tf', 'state/terraform.tfstate',
                     'dns/terraform.tfstate', 'dns/local.auto.tfvars', 'dns/.terraform/provider.tf'):
            path = source / name
            path.parent.mkdir(parents=True, exist_ok=True)
            path.write_text('local secret\n', encoding='utf-8')
        return source

    def test_clean_copy_uses_exact_asset_allowlist(self):
        verify = load_script(SCRIPT, 'platform_verify')
        with tempfile.TemporaryDirectory() as temp:
            work = Path(temp)
            source = self.fixture(work)
            verify.clean_copy(source, work / 'copy')
            copied = {p.relative_to(work / 'copy').as_posix()
                      for p in (work / 'copy').rglob('*') if p.is_file()}
            self.assertEqual(copied, ASSETS)
            for name in ASSETS:
                self.assertEqual((source / name).read_bytes(), (work / 'copy' / name).read_bytes())

    def test_missing_asset_is_an_error_before_terraform(self):
        verify = load_script(SCRIPT, 'platform_verify')
        with tempfile.TemporaryDirectory() as temp:
            source = self.fixture(Path(temp))
            (source / 'host/apps.yaml').unlink()
            with patch.object(verify, 'SOURCE', source), patch.object(verify.subprocess, 'run') as run:
                with self.assertRaises(FileNotFoundError):
                    verify.main()
                run.assert_not_called()

    def test_only_backend_disabled_init_and_validate_in_clean_roots(self):
        verify = load_script(SCRIPT, 'platform_verify')
        with tempfile.TemporaryDirectory() as temp:
            work = Path(temp)
            source = self.fixture(work)
            calls = []

            def fake_terraform(command, *, env, check, cwd):
                self.assertTrue(check)
                self.assertEqual(command[0], 'terraform')
                path = Path(command[1].removeprefix('-chdir='))
                self.assertEqual(Path(cwd), path)
                self.assertFalse(path.is_relative_to(source))
                self.assertTrue((path / '.terraform.lock.hcl').is_file())
                self.assertFalse((path / 'terraform.tfstate').exists())
                self.assertFalse((path / 'terraform.tfvars').exists())
                for key in ('AWS_ACCESS_KEY_ID', 'AWS_SECRET_ACCESS_KEY', 'AWS_PROFILE',
                            'AWS_WEB_IDENTITY_TOKEN_FILE', 'TF_VAR_secret', 'TF_CLI_ARGS',
                            'TF_DATA_DIR', 'TF_WORKSPACE', 'CLOUDFLARE_API_TOKEN',
                            'CLOUDFLARE_API_KEY', 'CLOUDFLARE_EMAIL'):
                    self.assertFalse(key in env, f'{key} must not be inherited')
                self.assertEqual(env['AWS_EC2_METADATA_DISABLED'], 'true')
                for key in ('TF_CLI_CONFIG_FILE', 'AWS_CONFIG_FILE', 'AWS_SHARED_CREDENTIALS_FILE'):
                    self.assertEqual(Path(env[key]).read_text(), '')
                calls.append((path, command[2:]))

            polluted = {
                'AWS_ACCESS_KEY_ID': 'secret', 'AWS_SECRET_ACCESS_KEY': 'secret',
                'AWS_PROFILE': 'production', 'AWS_WEB_IDENTITY_TOKEN_FILE': 'token',
                'TF_VAR_secret': 'secret', 'TF_CLI_ARGS': '-backend=true',
                'TF_DATA_DIR': str(source / '.terraform'), 'TF_WORKSPACE': 'production',
                'CLOUDFLARE_API_TOKEN': 'secret', 'CLOUDFLARE_API_KEY': 'secret',
                'CLOUDFLARE_EMAIL': 'private@example.com',
            }
            with patch.object(verify, 'SOURCE', source), patch.dict(os.environ, polluted), \
                    patch.object(verify.subprocess, 'run', side_effect=fake_terraform):
                verify.main()
            commands = [['fmt', '-check'], ['init', '-backend=false', '-input=false', '-lockfile=readonly'], ['validate']]
            self.assertEqual([args for _, args in calls], commands + [['test']] + commands * 2)
            self.assertEqual(calls[4][0], calls[0][0] / 'state')
            self.assertEqual(calls[7][0], calls[0][0] / 'dns')
            self.assertFalse(calls[0][0].exists(), 'Temporary assets must be removed')
            self.assertEqual((source / 'terraform.tfstate').read_text(), 'local secret\n')

    def test_each_terraform_failure_propagates_and_stops(self):
        verify = load_script(SCRIPT, 'platform_verify')
        for failure in ('fmt', 'init', 'validate', 'test'):
            with self.subTest(failure=failure), tempfile.TemporaryDirectory() as temp:
                source = self.fixture(Path(temp))
                calls = []

                def fake_terraform(command, **kwargs):
                    calls.append(command[2])
                    if command[2] == failure:
                        raise subprocess.CalledProcessError(7, command)

                with patch.object(verify, 'SOURCE', source), \
                        patch.object(verify.subprocess, 'run', side_effect=fake_terraform):
                    with self.assertRaises(subprocess.CalledProcessError) as error:
                        verify.main()
                self.assertEqual(error.exception.returncode, 7)
                self.assertEqual(calls, ['fmt', 'init', 'validate', 'test'][:len(calls)])
                self.assertEqual(calls[-1], failure)

    def test_generic_runtime_and_project_roots_stay_separate(self):
        generic = load_script(ROOT / 'scripts/terraform-verify.py', 'generic_verify')
        config = load_script(ROOT / 'scripts/project-config.py', 'project_config')
        self.assertEqual(generic.ROOTS, ('bootstrap/aws', 'account/aws', 'aws/dev/foundation',
                                       'modules/eks', 'aws/dev/management', 'aws/dev/workload',
                                       'integrations/aws-gcp/tailscale'))
        self.assertEqual(config.STACKS, ('bootstrap/aws', 'account/aws', 'aws/dev/foundation',
                                        'aws/dev/management', 'aws/dev/workload'))
        self.assertIn('all-runtime) stacks=(bootstrap/aws aws/dev/foundation aws/dev/management aws/dev/workload)',
                      (ROOT / 'scripts/tf-ci.sh').read_text(encoding='utf-8'))


if __name__ == '__main__':
    unittest.main()
