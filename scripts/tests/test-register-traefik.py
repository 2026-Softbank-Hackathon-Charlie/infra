#!/usr/bin/env python3
"""Offline checks of register-traefik.py's AppProject guard (ADR 0058)."""
import copy
import importlib.util
from pathlib import Path
import unittest

ROOT = Path(__file__).resolve().parents[2]
spec = importlib.util.spec_from_file_location('register_traefik', ROOT / 'scripts/register-traefik.py')
mod = importlib.util.module_from_spec(spec)
spec.loader.exec_module(mod)

WORKLOAD = 'https://workload.example'
LIVE = {
    'metadata': {'annotations': {'argocd.argoproj.io/sync-wave': '-1'}},
    'spec': {
        'sourceRepos': ['https://github.com/example/infra.git', 'https://aws.github.io/eks-charts'],
        'destinations': [{'server': WORKLOAD, 'namespace': 'kube-system'}, {'server': WORKLOAD, 'namespace': 'charlie-apps'}],
        'clusterResourceWhitelist': [{'group': '', 'kind': 'Namespace'}],
        'namespaceResourceWhitelist': [{'group': '*', 'kind': '*'}],
    },
}


def rendered(change=None):
    project = copy.deepcopy(LIVE)
    project['spec']['sourceRepos'].append(mod.TRAEFIK_REPO)
    project['spec']['destinations'].insert(0, {'server': WORKLOAD, 'namespace': 'traefik'})
    if change:
        change(project)
    return project


class ProjectChangeTests(unittest.TestCase):
    def test_traefik_additions_in_any_order(self):
        self.assertEqual(mod.project_change(LIVE, rendered(), WORKLOAD), [])
        self.assertEqual(mod.project_change(rendered(), rendered(), WORKLOAD), [])

    def test_anything_else_is_refused(self):
        cases = {
            'removes destinations': lambda p: p['spec']['destinations'].pop(),
            'adds destinations': lambda p: p['spec']['destinations'].append({'server': WORKLOAD, 'namespace': 'default'}),
            'removes source repositories': lambda p: p['spec']['sourceRepos'].pop(0),
            'adds source repositories': lambda p: p['spec']['sourceRepos'].append('https://example.com/charts'),
            'spec.clusterResourceWhitelist differs': lambda p: p['spec']['clusterResourceWhitelist'].append({'group': '*', 'kind': '*'}),
            'annotation argocd.argoproj.io/sync-wave differs': lambda p: p['metadata']['annotations'].update({'argocd.argoproj.io/sync-wave': '0'}),
        }
        for message, change in cases.items():
            with self.subTest(message):
                problems = mod.project_change(LIVE, rendered(change), WORKLOAD)
                self.assertTrue(any(p.startswith(message) for p in problems), problems)


if __name__ == '__main__':
    unittest.main()
