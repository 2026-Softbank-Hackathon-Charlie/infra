#!/usr/bin/env python3
"""Exercise the GKE render entrypoint offline with a temporary Helm chart."""
import json
import os
from pathlib import Path
import subprocess
import tempfile
import unittest

import yaml

ROOT = Path(__file__).resolve().parents[2]
PROFILE = ROOT / 'clusters/gcp-dev-workload/values/moimroom-gcp.yaml'


class GkeMoimroomTests(unittest.TestCase):
    def render(self, *args):
        return subprocess.run([os.environ.get('MAKE', 'make'), '--no-print-directory',
                               'render-gke-moimroom', *args], cwd=ROOT,
                              capture_output=True, text=True)

    def test_entrypoint_consumes_profile_and_renders_only_deployment(self):
        with tempfile.TemporaryDirectory() as folder:
            chart = Path(folder) / 'chart with spaces'
            templates = chart / 'templates'
            templates.mkdir(parents=True)
            (chart / 'Chart.yaml').write_text('apiVersion: v2\nname: moimroom\nversion: 0.1.0\n')
            (templates / 'deployment.yaml').write_text('''apiVersion: apps/v1
kind: Deployment
metadata:
  name: {{ .Release.Name }}
  annotations:
    namespace: {{ .Release.Namespace | quote }}
    profile: {{ .Values | toJson | quote }}
''')
            (templates / 'service.yaml').write_text('apiVersion: v1\nkind: Service\nmetadata:\n  name: must-not-render\n')
            result = self.render(f'MOIMROOM_CHART={chart.as_posix()}')
        self.assertEqual(result.returncode, 0, result.stderr)
        docs = [doc for doc in yaml.safe_load_all(result.stdout) if doc]
        self.assertEqual([(doc['kind'], doc['metadata']['name']) for doc in docs],
                         [('Deployment', 'moimroom-gcp')])
        annotations = docs[0]['metadata']['annotations']
        self.assertEqual(annotations['namespace'], 'charlie-apps')
        self.assertEqual(json.loads(annotations['profile']), yaml.safe_load(PROFILE.read_text()))

    def test_profile_preserves_settings_and_contains_only_secret_references(self):
        self.assertTrue(PROFILE.is_file(), 'The GKE profile is missing')
        profile = yaml.safe_load(PROFILE.read_text())
        self.assertEqual(set(profile), {'replicaCount', 'image', 'siteRegionLabel', 'seedSpaces',
                                      'database', 's3', 'appSecret', 'resources', 'nodeSelector'})
        self.assertEqual(profile['replicaCount'], 1)
        self.assertIs(profile['seedSpaces'], False)
        self.assertEqual(profile['siteRegionLabel'], 'gke')
        self.assertEqual(profile['database'], {'secretName': 'moimroom-gke-dburl', 'secretKey': 'uri'})
        self.assertEqual(profile['s3'], {
            'endpoint': 'https://storage.googleapis.com', 'region': 'auto',
            'bucket': 'charlie-414068132732-moimroom-gcp', 'pathStyle': 'false',
            'secretName': 'moimroom-gcp-s3', 'accessKeyKey': 'accessKey', 'secretKeyKey': 'secretKey'})
        self.assertEqual(profile['appSecret'], {'name': 'moimroom-gcp-app',
                                              'hostCodeKey': 'hostCode', 'adminTokenKey': 'adminToken'})
        self.assertEqual(set(profile['image']), {'repository', 'tag', 'pullPolicy'})
        self.assertEqual(profile['image']['repository'],
                         'asia-northeast3-docker.pkg.dev/project-b47cdba3-db5b-42a2-9a6/charlie-apps/moimroom')
        self.assertRegex(profile['image']['tag'], r'^git-[a-f0-9]{40}-run-[0-9]+-[0-9]+@sha256:[a-f0-9]{64}$')
        self.assertEqual(profile['image']['pullPolicy'], 'IfNotPresent')
        self.assertEqual(profile['resources'], {'limits': {'cpu': '500m', 'memory': '256Mi'},
                                                'requests': {'cpu': '50m', 'memory': '128Mi'}})
        self.assertEqual(profile['nodeSelector'], {})

    def test_missing_chart_is_rejected(self):
        result = self.render('MOIMROOM_CHART=')
        self.assertNotEqual(result.returncode, 0)
        self.assertIn('MOIMROOM_CHART', result.stderr)

    def test_helm_failure_is_not_hidden(self):
        with tempfile.TemporaryDirectory() as folder:
            result = self.render(f'MOIMROOM_CHART={Path(folder).as_posix()}/missing-chart')
        self.assertNotEqual(result.returncode, 0)
        self.assertIn('missing-chart', result.stderr)


if __name__ == '__main__':
    unittest.main()
