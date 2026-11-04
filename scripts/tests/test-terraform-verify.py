"""Disposable verification tree, init reuse, cache misses/hits, failure propagation."""
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
spec = importlib.util.spec_from_file_location('verify', ROOT / 'scripts/terraform-verify.py')
verify = importlib.util.module_from_spec(spec)
spec.loader.exec_module(verify)


class VerifyTests(unittest.TestCase):
    def test_copy_excludes_runtime_inputs_and_overrides(self):
        with tempfile.TemporaryDirectory() as t:
            src, dest = Path(t) / 'source', Path(t) / 'copy'
            src.mkdir()
            for name in ('main.tf', '.terraform.lock.hcl', 'terraform.tfvars', 'x.auto.tfvars.json', 'terraform.tfstate', 'backend.hcl', 'override.tf', 'local_override.tf'):
                (src / name).write_text('data')
            (src / 'linked.tf').symlink_to(src / 'main.tf')
            (src / '.terraform').mkdir()
            (src / '.terraform/secret.tf').write_text('secret')
            verify.clean_copy(src, dest)
            self.assertEqual({p.name for p in dest.iterdir()}, {'main.tf', '.terraform.lock.hcl'})

    def test_all_roots_one_init_mock_coverage_and_warm_cache(self):
        with tempfile.TemporaryDirectory() as t:
            work = Path(t)
            binary = work / 'terraform'
            binary.write_text('''#!/usr/bin/env python3
import json, os, sys
from pathlib import Path
path = Path(sys.argv[1].split('=', 1)[1])
assert not (path / 'terraform.tfvars').exists()
assert not (path / 'terraform.tfstate').exists()
assert 'TF_VAR_project' not in os.environ
assert 'TF_DATA_DIR' not in os.environ
with open(os.environ['VERIFY_LOG'], 'a') as stream:
    stream.write(json.dumps([str(path), sys.argv[2]]) + '\\n')
cache = Path(os.environ['TF_PLUGIN_CACHE_DIR'])
(cache / 'fake-provider').touch()
if os.environ.get('VERIFY_FAIL') == sys.argv[2]:
    sys.exit(1)
''')
            binary.chmod(0o755)
            log = work / 'calls'
            env = dict(os.environ, PATH=str(work) + os.pathsep + os.environ['PATH'], VERIFY_LOG=str(log), TF_PLUGIN_CACHE_DIR=str(work / 'cache'), TF_VAR_project='hidden')
            command = [sys.executable, str(ROOT / 'scripts/terraform-verify.py')]
            for _ in range(2):
                result = subprocess.run(command, env=env, capture_output=True, text=True)
                self.assertEqual(result.returncode, 0, result.stderr)
                calls = [json.loads(line) for line in log.read_text().splitlines()]
                self.assertEqual(sum(c[1] == 'init' for c in calls), 7)
                self.assertEqual(sum(c[1] == 'validate' for c in calls), 7)
                self.assertEqual(sum(c[1] == 'test' for c in calls), 6)
                self.assertTrue((work / 'cache/fake-provider').exists())
                log.unlink()
            env['VERIFY_FAIL'] = 'validate'
            self.assertNotEqual(subprocess.run(command, env=env, capture_output=True).returncode, 0)
            self.assertEqual([json.loads(line)[1] for line in log.read_text().splitlines()], ['init', 'validate'])

    def test_original_mock_runs_preserved(self):
        # Preserve the original AWS suite without counting independent cloud suites.
        roots = [ROOT / 'terraform' / verify.relative(stack) for stack in verify.ROOTS if not stack.startswith('integrations/')]
        tests = [p for root in roots for p in root.rglob('*.tftest.hcl')]
        cnpg = ROOT / 'terraform/environments/aws/dev/foundation/tests/cnpg.tftest.hcl'
        runs = lambda path: __import__('re').findall(r'^run\s+"([^"]+)"', path.read_text(), __import__('re').M)
        self.assertEqual(sum(len(runs(p)) for p in tests if p != cnpg), 28)
        self.assertIn(cnpg, tests)
        self.assertEqual(runs(cnpg), ['cnpg_backup_and_restore_boundaries'])
        self.assertEqual(len(roots), 6)

    def test_tailscale_registration_is_backend_free_and_preserves_templates(self):
        stack = 'integrations/aws-gcp/tailscale'
        self.assertIn(stack, verify.ROOTS)
        self.assertEqual(verify.relative(stack), Path(stack))
        with tempfile.TemporaryDirectory() as directory:
            root = Path(directory)
            source = root / 'terraform' / stack
            (source / 'tests').mkdir(parents=True)
            for name in ('main.tf', 'router-bootstrap.sh.tftpl', 'policy.json.tftpl', 'tests/router.tftest.hcl', 'terraform.tfstate', 'secret.auto.tfvars.json'):
                (source / name).write_text('fixture')
            copied = root / 'copy'
            verify.clean_copy(source, copied)
            self.assertEqual({p.relative_to(copied).as_posix() for p in copied.rglob('*') if p.is_file()},
                             {'main.tf', 'router-bootstrap.sh.tftpl', 'policy.json.tftpl', 'tests/router.tftest.hcl'})
            with patch.object(verify, 'ROOT', root), patch.object(sys, 'argv', ['verify', '--stack', stack]), patch.object(verify.subprocess, 'run') as run:
                verify.main()
            commands = [call.args[0][2:] for call in run.call_args_list]
            self.assertEqual(commands, [['init', '-backend=false', '-input=false', '-lockfile=readonly'], ['validate'], ['test']])
        spec = importlib.util.spec_from_file_location('deployment_config', ROOT / 'scripts/project-config.py')
        config = importlib.util.module_from_spec(spec)
        spec.loader.exec_module(config)
        self.assertNotIn(stack, config.STACKS)


if __name__ == '__main__':
    unittest.main()
