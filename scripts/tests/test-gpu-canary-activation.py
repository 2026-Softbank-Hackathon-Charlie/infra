#!/usr/bin/env python3
"""Offline activation boundary contracts; no models, keys or cluster access."""
import copy
import importlib.util
from pathlib import Path
import unittest

ROOT = Path(__file__).resolve().parents[2]
spec = importlib.util.spec_from_file_location('activation', ROOT / 'scripts/render-gpu-canary-activation.py')
activation = importlib.util.module_from_spec(spec)
spec.loader.exec_module(activation)


def policy():
    return {'app': 'moimroom-ai', 'projectId': 'evaluation-project',
            'sourceBackend': 'gcp', 'targetBackend': 'laptop',
            'sourceModel': 'qwen@sha256:' + 'a' * 64, 'targetModel': 'qwen@sha256:' + 'b' * 64,
            'datasetRevision': 'reviewed-v1', 'promptRevision': 'prompt-v1',
            'retrievalRevision': 'none', 'evaluatorRevision': 'reviewed-rubric-v1',
            'judgeModel': 'independent-judge-v1', 'scoreSource': 'API',
            'items': ['item-%02d' % n for n in range(20)],
            'maxAgeSeconds': 600, 'minSoakSeconds': 60,
            'metrics': [{'name': 'safety', 'configId': 'safety-id', 'minimum': 1,
                         'maxRegression': 0, 'hardFailure': True},
                        {'name': 'task', 'configId': 'task-id', 'minimum': .9,
                         'maxRegression': .05}]}


class ActivationTest(unittest.TestCase):
    def render(self, p=None):
        return activation.render(policy() if p is None else p)

    def test_no_follow_reset_route_or_secret_material(self):
        docs = self.render()
        self.assertEqual([d['kind'] for d in docs], ['ConfigMap', 'PersistentVolumeClaim', 'Deployment', 'NetworkPolicy', 'NetworkPolicy'])
        d = docs[2]
        self.assertEqual(d['spec']['strategy']['type'], 'Recreate')
        self.assertEqual(d['spec']['replicas'], 1)
        pod = d['spec']['template']['spec']
        gateway = pod['containers'][0]
        self.assertFalse(any(a.startswith('-ai-follow') for a in gateway['args']))
        self.assertIn('-initial-backend=gcp', gateway['args'])
        self.assertIn('-ai-quality-policy=/quality/policy.json', gateway['args'])
        self.assertEqual(next(v for v in pod['volumes'] if v['name'] == 'state'),
                         {'name': 'state', 'persistentVolumeClaim': {'claimName': 'ai-gateway-canary-state'}})
        secret = next(v for v in pod['volumes'] if v['name'] == 'quality-auth')
        self.assertEqual(secret['secret']['secretName'], 'ai-gateway-quality-auth')
        # The egress proxy service must never be re-applied: Tailscale owns its externalName.
        self.assertFalse(any(d['kind'] in ('Service', 'Secret', 'Namespace', 'Role', 'RoleBinding') for d in docs))
        self.assertEqual(docs[3]['spec']['egress'][0]['ports'], [{'protocol': 'TCP', 'port': 3000}])
        self.assertEqual(docs[4]['spec']['ingress'][0]['from'][0]['namespaceSelector']['matchLabels'],
                         {'kubernetes.io/metadata.name': 'hopcharlie-ai'})

    def test_missing_unknown_or_unpinned_policy_rejected(self):
        for patch in ({'projectId': ''}, {'app': 'moimroom-mig'}, {'sourceModel': 'qwen:latest'},
                      {'targetModel': 'unknown'}, {'items': ['one']}, {'maxAgeSeconds': 0},
                      {'baseUrl': 'http://attacker'}, {'prompt': 'must-not-render'},
                      {'sourceBackend': 'laptop'}, {'judgeModel': policy()['sourceModel']}):
            with self.subTest(patch=patch):
                p = policy(); p.update(patch)
                with self.assertRaises(ValueError): self.render(p)
        p=policy();p['items'][1]=p['items'][0]
        with self.assertRaises(ValueError): self.render(p)

    def test_safety_and_task_required_without_nan_or_bool(self):
        for transform in (lambda p: p['metrics'][0].update(minimum=.9),
                          lambda p: p['metrics'][0].update(hardFailure=False),
                          lambda p: p['metrics'][1].update(minimum=float('nan')),
                          lambda p: p['metrics'][1].update(maxRegression=True),
                          lambda p: p['metrics'][1].update(configId='safety-id')):
            p=policy(); transform(p)
            with self.assertRaises(ValueError): self.render(p)

    def test_input_not_modified_and_policy_bound_to_template(self):
        p=policy(); before=copy.deepcopy(p)
        first=self.render(p)
        self.assertEqual(p,before)
        p['datasetRevision']='reviewed-v2'
        second=self.render(p)
        self.assertNotEqual(first[0]['metadata']['name'],second[0]['metadata']['name'])
        self.assertEqual(second[2]['spec']['template']['spec']['volumes'][-2]['configMap']['name'],
                         second[0]['metadata']['name'])


if __name__ == '__main__': unittest.main()
