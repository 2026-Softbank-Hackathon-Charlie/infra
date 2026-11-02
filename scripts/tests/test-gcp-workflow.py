"""Credential/deployment boundaries and rendered GKE storage; no cloud calls."""
import json
import os
from pathlib import Path
import subprocess
import tempfile
import unittest

import yaml

ROOT = Path(__file__).resolve().parents[2]


class GCPDeliveryTests(unittest.TestCase):
    @classmethod
    def setUpClass(cls):
        cls.workflow = yaml.safe_load((ROOT / '.github/workflows/terraform-gcp.yml').read_text(encoding='utf-8'))

    def test_cloud_credentials_are_manual_main_only(self):
        deploy = self.workflow['jobs']['deploy']
        for condition in ("github.event_name == 'workflow_dispatch'", "github.ref == 'refs/heads/main'", 'inputs.confirm_apply', "vars.GCP_APPLY_ENABLED == 'true'"):
            self.assertIn(condition, deploy['if'])
        self.assertEqual(deploy['needs'], ['validate', 'baseline'])
        self.assertIn("'gcp-apply'", deploy['environment'])
        self.assertEqual(deploy['concurrency']['cancel-in-progress'], False)
        self.assertEqual(deploy['permissions'], {'contents': 'read', 'id-token': 'write'})
        for name in ('validate', 'baseline'):
            self.assertNotIn('id-token', self.workflow['jobs'][name].get('permissions', {}))
            self.assertFalse(any('google-github-actions/auth' in step.get('uses', '') for step in self.workflow['jobs'][name]['steps']))

    def test_both_roots_get_backend_free_checks_and_mock_tests(self):
        validate = self.workflow['jobs']['validate']
        self.assertEqual(set(validate['strategy']['matrix']['root']), {'terraform/bootstrap/gcp', 'terraform/environments/gcp/dev/workload'})
        command = validate['steps'][-1]['run']
        for part in ('-backend=false', 'validate', ' test'):
            self.assertIn(part, command)

    def test_saved_plan_is_applied_without_bootstrap_or_artifact_upload(self):
        deploy = self.workflow['jobs']['deploy']
        apply = next(s for s in deploy['steps'] if s.get('name') == 'Apply reviewed dispatch')
        self.assertEqual(apply['if'], "inputs.action == 'apply'")
        self.assertIn('"$RUNNER_TEMP/gcp.tfplan"', apply['run'])
        self.assertNotIn('-auto-approve', apply['run'])
        self.assertFalse(any('upload-artifact' in s.get('uses', '') for s in deploy['steps']))
        self.assertFalse(any('terraform/bootstrap/gcp' in s.get('run', '') for s in deploy['steps']))
        self.assertEqual(deploy['steps'][-1]['if'], 'always()')

    def test_tfvars_materialization_treats_repository_values_as_data(self):
        step = next(s for s in self.workflow['jobs']['deploy']['steps'] if s.get('name', '').startswith('Validate deployment'))
        self.assertNotIn('${{', step['run'])
        code = step['run'].split("python - <<'PY'\n", 1)[1].rsplit('\nPY', 1)[0]
        with tempfile.TemporaryDirectory() as tmp:
            values = {'project_id': 'example-project', 'control_plane_authorized_cidrs': ['192.0.2.1/32'], 'node_service_account_email': 'node@example-project.iam.gserviceaccount.com'}
            env = dict(os.environ, RUNNER_TEMP=tmp, GCP_PROJECT_ID='example-project', GCP_STATE_BUCKET='example-state', GCP_WIF_PROVIDER='example-provider', GCP_TERRAFORM_SA='example-sa', GCP_TFVARS_JSON=json.dumps(values))
            result = subprocess.run(['python', '-c', code], env=env, capture_output=True)
            self.assertEqual(result.returncode, 0, result.stderr)
            self.assertEqual(json.loads(Path(tmp, 'gcp.auto.tfvars.json').read_text()), values)
            values['project_id'] = 'different-project'
            env['GCP_TFVARS_JSON'] = json.dumps(values)
            self.assertNotEqual(subprocess.run(['python', '-c', code], env=env, capture_output=True).returncode, 0)

    def test_gke_baseline_has_correct_csi_and_configurable_limits(self):
        rendered = subprocess.run(['helm', 'template', 'gcp-baseline', str(ROOT / 'helm/charts/gcp-baseline'), '--set', 'storageClass.reclaimPolicy=Retain', '--set-string', 'quota.pods=12'], check=True, capture_output=True, text=True)
        docs = list(yaml.safe_load_all(rendered.stdout))
        storage = next(d for d in docs if d['kind'] == 'StorageClass')
        self.assertEqual(storage['provisioner'], 'pd.csi.storage.gke.io')
        self.assertEqual(storage['parameters']['type'], 'pd-balanced')
        self.assertEqual(storage['volumeBindingMode'], 'WaitForFirstConsumer')
        self.assertEqual(storage['reclaimPolicy'], 'Retain')
        quota = next(d for d in docs if d['kind'] == 'ResourceQuota')
        self.assertEqual(quota['spec']['hard']['pods'], '12')
        self.assertNotIn('ebs.csi.aws.com', rendered.stdout)


if __name__ == '__main__':
    unittest.main()
