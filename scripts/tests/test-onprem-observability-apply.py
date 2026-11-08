#!/usr/bin/env python3
"""Installation boundary regression tests; no cloud or Kubernetes access."""
import importlib.util
import json
from pathlib import Path
import unittest
from unittest.mock import patch
import yaml

ROOT = Path(__file__).resolve().parents[2]
SPEC = importlib.util.spec_from_file_location('onprem_apply', ROOT / 'scripts/apply-onprem-observability.py')
M = importlib.util.module_from_spec(SPEC)
SPEC.loader.exec_module(M)


def bundle(extra=()):
    docs = [{'apiVersion': 'apps/v1', 'kind': kind,
             'metadata': {'name': name, 'namespace': 'observability'}, 'spec': {}}
            for kind, name in [('DaemonSet', 'observability-alloy'), ('Deployment', 'observability-gateway')]]
    return yaml.safe_dump_all(docs + list(extra))


class Boundary(unittest.TestCase):
    def test_other_namespace_secret_and_public_listener_rejected(self):
        for doc in [
            {'kind': 'Deployment', 'metadata': {'name': 'x', 'namespace': 'moimroom-mig'}},
            {'kind': 'Secret', 'metadata': {'name': 'observability-client-auth'}},
            {'kind': 'Service', 'metadata': {'name': 'x'}, 'spec': {'type': 'LoadBalancer'}},
            {'kind': 'CustomResourceDefinition', 'metadata': {'name': 'x'}},
        ]:
            with self.subTest(doc=doc), self.assertRaises(ValueError):
                M.validate_bundle(bundle([doc]))

    def test_wrong_device_prevents_dry_run_and_mutation(self):
        with patch.object(M, 'run', return_value='different-uid') as run:
            with self.assertRaises(ValueError):
                M.install(['kubectl'], bundle(), 'reviewed-uid', execute=True)
            self.assertEqual(run.call_count, 1)

    def replies(self, base, args, data=None):
        if args[:3] == ['get', 'namespace', 'kube-system']:
            return 'reviewed-uid'
        if args[:3] == ['get', 'namespace', 'observability']:
            return json.dumps({'metadata': {'labels': {'pod-security.kubernetes.io/enforce': 'privileged'}}})
        if args[:2] == ['get', 'configmap']:
            return 'https://observability-central.observability.svc:4318'
        return ''

    def test_default_performs_server_dry_run_only(self):
        with patch.object(M, 'run', side_effect=self.replies) as run:
            result = M.install(['kubectl'], bundle(), 'reviewed-uid')
            calls = [c.args[1] for c in run.call_args_list]
            self.assertIn(['apply', '--dry-run=server', '-n', 'observability', '-f', '-'], calls)
            self.assertFalse(any(c[:2] == ['apply', '-n'] for c in calls))
            self.assertFalse(result['applied'])
            self.assertFalse(result['receivingVerified'])

    def test_missing_auth_dependency_prevents_apply(self):
        def fail(base, args, data=None):
            if args[:2] == ['get', 'secret']:
                raise RuntimeError('missing')
            return self.replies(base, args, data)
        with patch.object(M, 'run', side_effect=fail) as run:
            with self.assertRaises(RuntimeError):
                M.install(['kubectl'], bundle(), 'reviewed-uid', execute=True)
            self.assertFalse(any(c.args[1][0] == 'apply' for c in run.call_args_list))

    def test_execute_requires_second_identity_check_and_never_claims_receiving(self):
        with patch.object(M, 'run', side_effect=self.replies) as run:
            result = M.install(['kubectl'], bundle(), 'reviewed-uid', execute=True)
            calls = [c.args[1] for c in run.call_args_list]
            self.assertEqual(sum(c[:3] == ['get', 'namespace', 'kube-system'] for c in calls), 2)
            self.assertTrue(result['applied'])
            self.assertFalse(result['receivingVerified'])


if __name__ == '__main__':
    unittest.main()
