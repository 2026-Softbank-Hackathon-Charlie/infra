#!/usr/bin/env python3
"""Exercise the scaleout boundary using synthetic resources, without cloud access."""
import copy
import hashlib
import importlib.util
import json
from pathlib import Path
import subprocess
import sys
import unittest
from unittest.mock import patch

ROOT = Path(__file__).resolve().parents[2]
SPEC = importlib.util.spec_from_file_location('scaleout_guard', ROOT / 'scripts/check-management-scaleout-plan.py')
guard = importlib.util.module_from_spec(SPEC)
SPEC.loader.exec_module(guard)


def resource(address, kind, values, actions=None, before=None):
    return {'address': address, 'type': kind, 'mode': 'managed', 'change': {
        'actions': actions or ['no-op'], 'before': copy.deepcopy(values if before is None else before),
        'after': copy.deepcopy(values), 'after_unknown': {}}}


def fixture(update=True):
    resources = []
    for address, az, count in ((guard.TARGET, 'a', 2), (guard.OTHER, 'c', 1)):
        values = {'cluster_name': 'charlie-dev-management', 'node_group_name': 'charlie-dev-management-ap-northeast-2' + az,
                  'arn': 'arn:aws:eks:ap-northeast-2:525091405886:nodegroup/charlie-dev-management/test-' + az,
                  'instance_types': ['m7i-flex.large'], 'scaling_config': [{'min_size': count, 'max_size': count, 'desired_size': count}], 'version': '1.35'}
        before = copy.deepcopy(values)
        if address == guard.TARGET and update:
            before['scaling_config'] = [{'min_size': 1, 'max_size': 1, 'desired_size': 1}]
        resources.append(resource(address, 'aws_eks_node_group', values, ['update'] if address == guard.TARGET and update else None, before))
    drift = []
    if update:
        for name in ('cluster', 'cni', 'ebs', 'lbc', 'node'):
            arn = 'arn:aws:iam::525091405886:policy/synthetic-' + name
            after = {'name': name, 'managed_policy_arns': [arn]}
            before = dict(after, managed_policy_arns=[])
            item = resource('module.eks.aws_iam_role.' + name, 'aws_iam_role', after)
            resources.append(item)
            drift.append(resource(item['address'], item['type'], after, ['update'], before))
            resources.append(resource('module.eks.aws_iam_role_policy_attachment.' + name, 'aws_iam_role_policy_attachment', {'role': name, 'policy_arn': arn}))
        after = {'arn': 'arn:aws:iam::525091405886:policy/synthetic-lbc', 'attachment_count': 1}
        item = resource('module.eks.aws_iam_policy.lbc', 'aws_iam_policy', after)
        resources.append(item)
        drift.append(resource(item['address'], item['type'], after, ['update'], dict(after, attachment_count=0)))
        for name in ('argocd', 'operator'):
            after = {'access_scope': [{'type': 'cluster', 'namespaces': []}]}
            before = {'access_scope': [{'type': 'cluster', 'namespaces': None}]}
            drift.append(resource('module.eks.aws_eks_access_policy_association.' + name, 'aws_eks_access_policy_association', after, ['update'], before))
        drift.append(resource('module.eks.aws_launch_template.node', 'aws_launch_template', {'security_group_names': []}, ['update'], {'security_group_names': None}))
        api = {'id': 'sg-synthetic-api', 'ingress': [{'security_groups': ['sg-synthetic-bridge'], 'protocol': 'tcp', 'from_port': 443, 'to_port': 443}]}
        node = {'id': 'sg-synthetic-node', 'egress': [{'protocol': '-1', 'cidr_blocks': ['0.0.0.0/0']}]}
        for name, after, field in (('api', api, 'ingress'), ('node', node, 'egress')):
            item = resource('module.eks.aws_security_group.' + name, 'aws_security_group', after)
            resources.append(item)
            drift.append(resource(item['address'], item['type'], after, ['update'], dict(after, **{field: []})))
        resources.extend([
            resource('module.eks.aws_vpc_security_group_ingress_rule.bridge_api', 'aws_vpc_security_group_ingress_rule', {'security_group_id': api['id'], 'referenced_security_group_id': 'sg-synthetic-bridge', 'ip_protocol': 'tcp', 'from_port': 443, 'to_port': 443}),
            resource('module.eks.aws_vpc_security_group_egress_rule.node', 'aws_vpc_security_group_egress_rule', {'security_group_id': node['id'], 'cidr_ipv4': '0.0.0.0/0', 'ip_protocol': '-1'})])
    return {'format_version': '1.2', 'complete': True, 'errored': False, 'resource_changes': resources, 'resource_drift': drift, 'output_changes': {}}


def fingerprint(plan):
    return hashlib.sha256(json.dumps(guard.drift_projection(plan['resource_drift']), sort_keys=True, separators=(',', ':')).encode()).hexdigest()


class GuardTest(unittest.TestCase):
    def setUp(self):
        self.plan = fixture()
        self.hash_patch = patch.object(guard, 'APPROVED_DRIFT_HASH', fingerprint(self.plan))
        self.hash_patch.start()
        self.addCleanup(self.hash_patch.stop)

    def test_only_reviewed_update(self):
        self.assertEqual(guard.check(self.plan), 'update')

    def test_already_applied_does_not_apply_refresh(self):
        self.assertEqual(guard.check(fixture(False)), 'noop')
        p = fixture(False)
        p['resource_drift'] = self.plan['resource_drift']
        with self.assertRaises(ValueError):
            guard.check(p)

    def test_unapproved_changes_fail(self):
        changes = [
            lambda p: p.update(complete=False),
            lambda p: p.update(deferred_changes=[{}]),
            lambda p: p.update(resource_drift=[]),
            lambda p: p['resource_drift'][0]['change']['after'].update(managed_policy_arns=['unreviewed']),
            lambda p: p['resource_changes'][0]['change']['after'].update(version='other'),
            lambda p: p['resource_changes'][0]['change']['after_unknown'].update(arn=True),
            lambda p: p['resource_changes'][0].update(previous_address='old.address'),
            lambda p: p['resource_changes'][0]['change'].update(importing={'id': 'example'}),
            lambda p: p['resource_changes'][0]['change'].update(actions=['delete', 'create']),
            lambda p: p['resource_changes'][1]['change'].update(actions=['update']),
            lambda p: p.update(output_changes={'value': {'actions': ['update']}}),
            lambda p: p['resource_changes'][0]['change']['after'].update(cluster_name='wrong'),
            lambda p: p['resource_changes'][0]['change']['after'].update(arn='arn:aws:eks:ap-northeast-2:111111111111:nodegroup/other/x'),
            lambda p: p['resource_changes'].append(copy.deepcopy(p['resource_changes'][0])),
        ]
        for change in changes:
            with self.subTest(change=change):
                p = copy.deepcopy(self.plan)
                change(p)
                with self.assertRaises((ValueError, KeyError, TypeError)):
                    guard.check(p)

    def test_ownership_must_match_even_with_reviewed_hash(self):
        for kind, field, value in (
            ('aws_iam_role_policy_attachment', 'policy_arn', 'wrong'),
            ('aws_vpc_security_group_ingress_rule', 'referenced_security_group_id', 'wrong'),
            ('aws_vpc_security_group_egress_rule', 'ip_protocol', 'tcp'),
        ):
            p = copy.deepcopy(self.plan)
            next(r for r in p['resource_changes'] if r['type'] == kind)['change']['after'][field] = value
            with self.assertRaises(ValueError):
                guard.check(p)

    def test_cli_never_echoes_payload(self):
        for payload in ('secret-invalid-json', json.dumps({'secret': 'secret-invalid-json'})):
            result = subprocess.run([sys.executable, str(ROOT / 'scripts/check-management-scaleout-plan.py')], input=payload, text=True, capture_output=True)
            self.assertEqual(result.returncode, 1)
            self.assertNotIn('secret-invalid-json', result.stdout + result.stderr)


if __name__ == '__main__':
    unittest.main()
