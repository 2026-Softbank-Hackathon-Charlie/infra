"""Workflow gates, matrix coverage and least privilege; no GitHub/AWS calls."""
from itertools import product
from pathlib import Path
import os
import subprocess
import tempfile
import unittest
import yaml

ROOT = Path(__file__).resolve().parents[2]


class WorkflowGate(unittest.TestCase):
    def setUp(self):
        self.workflow = yaml.safe_load((ROOT / '.github/workflows/terraform-check.yml').read_text())

    def test_event_matrix(self):
        job = self.workflow['jobs']['deploy']
        expression = job['if'].replace('github.event_name', 'event').replace('github.ref', 'ref').replace('inputs.action', 'action').replace('&&', 'and').replace('||', 'or')
        for event, ref, action in product(('push', 'pull_request', 'workflow_dispatch'), ('refs/heads/main', 'refs/heads/feature'), ('plan', 'apply', '', 'destroy')):
            self.assertEqual(eval(expression, {'__builtins__': {}}, dict(event=event, ref=ref, action=action)), event == 'workflow_dispatch' and ref == 'refs/heads/main' and action in ('plan', 'apply'))
        self.assertEqual(job['needs'], 'check')
        self.assertFalse(job['concurrency']['cancel-in-progress'])
        self.assertNotIn('concurrency', self.workflow)
        triggers = self.workflow.get('on', self.workflow.get(True))
        inputs = triggers['workflow_dispatch']['inputs']
        self.assertEqual(inputs['action']['default'], 'plan')
        self.assertEqual(inputs['action']['options'], ['plan', 'apply'])
        self.assertEqual(inputs['stack']['default'], 'bootstrap')
        self.assertEqual(inputs['stack']['options'], ['bootstrap', 'foundation', 'management', 'management-scaleout', 'workload', 'all-runtime'])
        run = job['steps'][-1]
        self.assertEqual(run['env'], {'TF_ACTION': '${{ inputs.action }}', 'TF_STACK': '${{ inputs.stack }}'})
        self.assertEqual(run['run'], 'bash scripts/tf-ci.sh "$TF_ACTION" "$TF_STACK"')
        for name, check in self.workflow['jobs'].items():
            if name != 'deploy':
                self.assertNotIn('id-token', check.get('permissions', {}))
        self.assertEqual(job['permissions']['id-token'], 'write')
        settings = next(s for s in job['steps'] if s.get('id') == 'settings')
        self.assertIn('--deploy-context', settings['run'])

    def test_scaleout_commit_gate_precedes_aws_authentication(self):
        steps = self.workflow['jobs']['deploy']['steps']
        gate = next(s for s in steps if s.get('name') == 'Verify approved scaleout commit before AWS authentication')
        auth = next(s for s in steps if s.get('uses', '').startswith('aws-actions/configure-aws-credentials'))
        self.assertLess(steps.index(gate), steps.index(auth))
        self.assertEqual(gate['if'], "inputs.stack == 'management-scaleout'")
        for expected, actual, ok in (("a" * 40, "a" * 40, True), ("a" * 40, "b" * 40, False), ("", "a" * 40, False), ("$(false)", "a" * 40, False)):
            result = subprocess.run(['bash', '-e', '-c', gate['run']], env=dict(os.environ, EXPECTED_SHA=expected, EXECUTION_SHA=actual), capture_output=True)
            self.assertEqual(result.returncode == 0, ok)

    def test_runner_context_is_not_used_in_workflow_or_job_env(self):
        # GitHub resolves these fields before runner context is available.
        environments = [self.workflow.get('env', {})]
        environments.extend(job.get('env', {}) for job in self.workflow['jobs'].values())
        for env in environments:
            for value in env.values():
                self.assertNotIn('runner.', str(value))

    def test_aggregate_blocks_failures_skips_and_cancellations(self):
        check = self.workflow['jobs']['check']
        self.assertEqual(check['if'], 'always()')
        self.assertEqual(set(check['needs']), {'terraform', 'helm', 'scripts'})
        step = check['steps'][0]
        self.assertEqual(set(step['env'].values()), {'${{ needs.' + key + '.result }}' for key in check['needs']})
        for results in product(('success', 'failure', 'skipped', 'cancelled'), repeat=3):
            env = dict(os.environ, **dict(zip(('TERRAFORM_RESULT', 'HELM_RESULT', 'SCRIPTS_RESULT'), results)))
            result = subprocess.run(['bash', '-e', '-c', step['run']], env=env, capture_output=True)
            self.assertEqual(result.returncode == 0, all(r == 'success' for r in results))

    def test_root_matrix_and_provider_only_cache(self):
        job = self.workflow['jobs']['terraform']
        self.assertFalse(job['strategy']['fail-fast'])
        self.assertEqual(job['strategy']['max-parallel'], 3)
        entries = job['strategy']['matrix']['include']
        self.assertEqual({v['stack'] for v in entries}, {'bootstrap/aws', 'account/aws', 'aws/dev/foundation', 'modules/eks', 'aws/dev/management', 'aws/dev/workload', 'integrations/aws-gcp/tailscale'})
        self.assertEqual(len(entries), 7)
        for v in entries:
            path = 'terraform/environments/' if v['stack'].startswith('aws/dev/') else 'terraform/'
            self.assertEqual(v['lock'], path + v['stack'] + '/.terraform.lock.hcl')
            self.assertTrue((ROOT / v['lock']).is_file())
        cache = next(s for s in job['steps'] if s.get('uses', '').startswith('actions/cache@'))
        cache_path = '${{ steps.provider-cache.outputs.path }}'
        self.assertEqual(cache['with']['path'], cache_path)
        self.assertEqual(job['steps'][-1]['env']['TF_PLUGIN_CACHE_DIR'], cache_path)
        setup = next(s for s in job['steps'] if s.get('id') == 'provider-cache')
        with tempfile.TemporaryDirectory() as temp:
            output = Path(temp) / 'output'
            env = dict(os.environ, RUNNER_TEMP=temp, GITHUB_OUTPUT=str(output))
            subprocess.run(['bash', '-e', '-c', setup['run']], env=env, check=True)
            self.assertEqual(output.read_text(), f'path={temp}/terraform-providers\n')
            self.assertTrue((Path(temp) / 'terraform-providers').is_dir())
        self.assertLess(job['steps'].index(setup), job['steps'].index(cache))
        for field in ('runner.os', 'runner.arch', 'tf-version.outputs.version', 'matrix.stack', 'hashFiles(matrix.lock)'):
            self.assertIn(field, cache['with']['key'])
        self.assertIn('scripts/terraform-verify.py', job['steps'][-1]['run'])

    def test_tailscale_is_validation_only(self):
        stack = 'integrations/aws-gcp/tailscale'
        job = self.workflow['jobs']['terraform']
        self.assertIn(stack, {entry['stack'] for entry in job['strategy']['matrix']['include']})
        self.assertNotIn('id-token', job.get('permissions', {}))
        self.assertFalse(any('configure-aws-credentials' in step.get('uses', '') or 'google-github-actions/auth' in step.get('uses', '') for step in job['steps']))
        triggers = self.workflow.get('on', self.workflow.get(True))
        self.assertNotIn(stack, triggers['workflow_dispatch']['inputs']['stack']['options'])


if __name__ == '__main__':
    unittest.main()
