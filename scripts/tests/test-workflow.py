"""Workflow gates, matrix coverage and least privilege; no GitHub/AWS calls."""
from contextlib import redirect_stdout
import importlib.util
import io
from itertools import product
from pathlib import Path
import os
import subprocess
import sys
import tempfile
import unittest
from unittest import mock
import yaml

ROOT = Path(__file__).resolve().parents[2]


class WorkflowGate(unittest.TestCase):
    def setUp(self):
        self.workflow = yaml.safe_load((ROOT / '.github/workflows/terraform-check.yml').read_text())

    def test_event_matrix(self):
        job = self.workflow['jobs']['deploy']
        expression = job['if'].replace('needs.check.outputs.aws_changed', 'aws_changed').replace('github.event_name', 'event').replace('github.ref', 'ref').replace('inputs.action', 'action').replace('&&', 'and').replace('||', 'or')
        for event, ref, action, aws_changed in product(('push', 'pull_request', 'workflow_dispatch'), ('refs/heads/main', 'refs/heads/feature'), ('plan', 'apply', '', 'destroy'), ('true', 'false')):
            expected = ref == 'refs/heads/main' and ((event == 'push' and aws_changed == 'true') or (event == 'workflow_dispatch' and action in ('plan', 'apply')))
            with self.subTest(event=event, ref=ref, action=action, aws_changed=aws_changed):
                self.assertEqual(eval(expression, {'__builtins__': {}}, dict(event=event, ref=ref, action=action, aws_changed=aws_changed)), expected)
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
        self.assertEqual(run['env'], {
            'TF_ACTION': "${{ github.event_name == 'push' && 'apply' || inputs.action }}",
            'TF_STACK': "${{ github.event_name == 'push' && 'all-runtime' || inputs.stack }}",
        })
        for event, action, stack in product(('push', 'workflow_dispatch'), ('plan', 'apply', ''), ('bootstrap', 'workload', '')):
            for key, push_value, input_value in (('TF_ACTION', 'apply', action), ('TF_STACK', 'all-runtime', stack)):
                value = run['env'][key][3:-3].replace('github.event_name', 'event').replace('inputs.action', 'action').replace('inputs.stack', 'stack').replace('&&', 'and').replace('||', 'or')
                resolved = eval(value, {'__builtins__': {}}, dict(event=event, action=action, stack=stack))
                self.assertEqual(resolved, push_value if event == 'push' else input_value)
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

    def test_aws_scope_output_reaches_deploy_with_full_push_history(self):
        scripts = self.workflow['jobs']['scripts']
        steps = scripts['steps']
        checkout = next(s for s in steps if s.get('uses', '').startswith('actions/checkout@'))
        setup_python = next(s for s in steps if s.get('uses', '').startswith('actions/setup-python@'))
        scope = next(s for s in steps if s.get('id') == 'aws-scope')
        self.assertEqual(checkout['with']['fetch-depth'], 0)
        self.assertGreater(steps.index(scope), steps.index(setup_python))
        self.assertEqual(scope['env'], {'BEFORE_SHA': '${{ github.event.before }}', 'AFTER_SHA': '${{ github.sha }}'})
        self.assertEqual(scope['run'], 'python3 scripts/aws-apply-scope.py >> "$GITHUB_OUTPUT"')
        self.assertFalse(scope.get('continue-on-error', False))
        self.assertEqual(scripts['outputs']['aws_changed'], '${{ steps.aws-scope.outputs.changed }}')
        self.assertEqual(self.workflow['jobs']['check']['outputs']['aws_changed'], '${{ needs.scripts.outputs.aws_changed }}')

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


SCOPE_PATH = ROOT / 'scripts/aws-apply-scope.py'
scope_spec = importlib.util.spec_from_file_location('aws_apply_scope', SCOPE_PATH)
scope = importlib.util.module_from_spec(scope_spec)
scope_spec.loader.exec_module(scope)


class AWSApplyInputs(unittest.TestCase):
    def test_deployment_roots_and_exact_shared_inputs(self):
        paths = [
            'terraform/bootstrap/aws/main.tf',
            'terraform/environments/aws/dev/foundation/main.tf',
            'terraform/environments/aws/dev/management/main.tf',
            'terraform/environments/aws/dev/workload/.terraform.lock.hcl',
            'terraform/modules/eks/main.tf',
            'terraform/modules/eks/userdata.txt',
            '.terraform-version',
            'config/project.json',
            'scripts/tf-ci.sh',
            'scripts/common.sh',
            'scripts/project-config.py',
            'terraform/config/aws-load-balancer-controller-policy.json',
        ]
        for path in paths:
            with self.subTest(path=path):
                self.assertTrue(scope.is_aws_input(path))

    def test_docs_tests_examples_and_other_stacks_are_not_apply_inputs(self):
        paths = [
            'terraform/bootstrap/aws/README.MD',
            'terraform/modules/eks/guide.mdx',
            'terraform/modules/eks/guide.rst',
            'terraform/modules/eks/docs/diagram.svg',
            'terraform/modules/eks/tests/main.tf',
            'terraform/modules/eks/nested/tests/fixture.tf',
            'terraform/modules/eks/check.tftest.hcl',
            'terraform/bootstrap/aws/terraform.tfvars.example',
            'terraform/environments/gcp/dev/workload/variables.tf',
            'terraform/account/aws/main.tf',
            'terraform/platform/aws/main.tf',
            'terraform/integrations/aws-gcp/tailscale/main.tf',
            'scripts/aws-apply-scope.py',
            'scripts/unrelated.py',
            '.github/workflows/terraform-check.yml',
            'docs/adr/0068-record-three-node-gke-workload-capacity.md',
        ]
        for path in paths:
            with self.subTest(path=path):
                self.assertFalse(scope.is_aws_input(path))

    def test_git_names_are_nul_delimited_not_line_delimited(self):
        cases = [
            (b'docs/note\nterraform/modules/eks/main.tf\0', 'changed=false\n'),
            (b'docs/note.md\0terraform/modules/eks/file with spaces.tf\0', 'changed=true\n'),
        ]
        env = {'GITHUB_EVENT_NAME': 'push', 'GITHUB_REF': 'refs/heads/main', 'BEFORE_SHA': 'a' * 40, 'AFTER_SHA': 'b' * 40}
        for output, expected in cases:
            with self.subTest(output=output), mock.patch.dict(os.environ, env, clear=True), mock.patch.object(scope.subprocess, 'check_output', return_value=output) as git:
                stdout = io.StringIO()
                with redirect_stdout(stdout):
                    self.assertEqual(scope.main(), 0)
                self.assertEqual(stdout.getvalue(), expected)
                self.assertEqual(git.call_args.args[0], ['git', 'diff', '--no-renames', '--name-only', '-z', 'a' * 40, 'b' * 40, '--'])
                self.assertEqual(git.call_args.kwargs, {'shell': False})


class AWSApplyGitRange(unittest.TestCase):
    def setUp(self):
        temp = tempfile.TemporaryDirectory()
        self.addCleanup(temp.cleanup)
        self.repo = Path(temp.name)
        self.env = dict(os.environ, GIT_CONFIG_NOSYSTEM='1', GIT_CONFIG_GLOBAL=os.devnull, GIT_TERMINAL_PROMPT='0', GIT_AUTHOR_NAME='Workflow fixture', GIT_AUTHOR_EMAIL='workflow@example.invalid', GIT_COMMITTER_NAME='Workflow fixture', GIT_COMMITTER_EMAIL='workflow@example.invalid')
        self.git('init', '--quiet')
        self.git('commit', '--quiet', '--allow-empty', '-m', 'fixture')
        self.base = self.git('rev-parse', 'HEAD').decode().strip()

    def git(self, *args):
        return subprocess.check_output(['git', '-c', 'commit.gpgsign=false', *args], cwd=self.repo, env=self.env, stderr=subprocess.PIPE)

    def commit(self, changes):
        for name, content in changes.items():
            path = self.repo / name
            if content is None:
                path.unlink()
            else:
                path.parent.mkdir(parents=True, exist_ok=True)
                path.write_text(content, encoding='utf-8')
        self.git('add', '-A')
        self.git('commit', '--quiet', '-m', 'fixture')
        return self.git('rev-parse', 'HEAD').decode().strip()

    def run_scope(self, before, after, event='push', ref='refs/heads/main', **extra_env):
        env = dict(self.env, GITHUB_EVENT_NAME=event, GITHUB_REF=ref, BEFORE_SHA=before, AFTER_SHA=after, **extra_env)
        return subprocess.run([sys.executable, str(SCOPE_PATH)], cwd=self.repo, env=env, capture_output=True)

    def assert_scope(self, before, after, changed, **kwargs):
        result = self.run_scope(before, after, **kwargs)
        self.assertEqual(result.returncode, 0, result.stderr.decode(errors='replace'))
        self.assertEqual(result.stdout.decode().splitlines(), ['changed=' + str(changed).lower()])

    def test_full_push_includes_aws_commit_before_last_docs_commit(self):
        aws = self.commit({'terraform/modules/eks/main.tf': '# fixture\n'})
        docs = self.commit({'README.md': 'fixture\n'})
        self.assert_scope(self.base, docs, True)
        self.assert_scope(aws, docs, False)

    def test_gcp_pr_paths_workflow_and_other_stacks_skip_but_shared_and_mixed_apply(self):
        paths = [
            'terraform/environments/gcp/dev/workload/variables.tf',
            'terraform/environments/gcp/dev/workload/terraform.tfvars.example',
            'terraform/environments/gcp/dev/workload/tests/workload.tftest.hcl',
            'docs/adr/0032-add-gke-capacity-for-cnpg-poc.md',
            'docs/adr/0068-record-three-node-gke-workload-capacity.md',
            'README.md', '.github/workflows/terraform-check.yml',
            'scripts/aws-apply-scope.py', 'scripts/tests/test-workflow.py',
            'terraform/account/aws/main.tf', 'terraform/platform/aws/main.tf',
            'terraform/integrations/aws-gcp/tailscale/main.tf',
        ]
        non_aws = self.commit({path: 'fixture\n' for path in paths})
        self.assert_scope(self.base, non_aws, False)
        shared = self.commit({'config/project.json': '{}\n'})
        self.assert_scope(non_aws, shared, True)
        mixed = self.commit({'terraform/environments/aws/dev/workload/main.tf': '# fixture\n', 'docs/note.md': 'fixture\n'})
        self.assert_scope(shared, mixed, True)

    def test_deleted_or_renamed_aws_paths_are_detected(self):
        path = 'terraform/modules/eks/main.tf'
        created = self.commit({path: '# fixture\n'})
        deleted = self.commit({path: None})
        self.assert_scope(created, deleted, True)
        recreated = self.commit({path: '# fixture\n'})
        (self.repo / 'archive').mkdir()
        self.git('mv', path, 'archive/moved.tf')
        self.git('commit', '--quiet', '-m', 'fixture')
        renamed = self.git('rev-parse', 'HEAD').decode().strip()
        self.assert_scope(recreated, renamed, True)

    def test_initial_push_uses_complete_tree(self):
        after = self.commit({'terraform/bootstrap/aws/main.tf': '# fixture\n'})
        self.assert_scope('0' * 40, after, True)
        self.assert_scope('0' * 40, self.base, False)

    def test_git_lookup_failure_fails_closed(self):
        result = self.run_scope('f' * 40, self.base)
        self.assertNotEqual(result.returncode, 0)
        self.assertEqual(result.stdout, b'')

    def test_other_events_or_branches_do_not_query_git(self):
        for event, ref in [('pull_request', 'refs/heads/main'), ('push', 'refs/heads/feature'), ('workflow_dispatch', 'refs/heads/main')]:
            with self.subTest(event=event, ref=ref):
                self.assert_scope('not-a-sha', 'not-a-sha', False, event=event, ref=ref, PATH='')


if __name__ == '__main__':
    unittest.main()
