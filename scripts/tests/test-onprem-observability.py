#!/usr/bin/env python3
"""Onprem preparation, exact signal provenance and real pinned chart renders."""
import importlib.util
import json
import os
from pathlib import Path
import subprocess
import unittest
import yaml

ROOT = Path(__file__).resolve().parents[2]
spec = importlib.util.spec_from_file_location('onprem_observability', ROOT / 'scripts/render-onprem-observability.py')
module = importlib.util.module_from_spec(spec)
spec.loader.exec_module(module)


class Prepare(unittest.TestCase):
    def test_inactive_bundle_isolated_and_bound_to_onprem(self):
        contract, values = module.prepare()
        self.assertFalse(contract['enabled'])
        self.assertTrue(contract['activationRequiresExecutionApproval'])
        self.assertFalse(values['app-observability.yaml']['environments']['onprem']['profile']['observability']['enabled'])
        text = json.dumps(values)
        for old in ('charlie-dev-gcp-workload', 'charlie-gcp-balanced', 'asia-northeast3'):
            self.assertNotIn(old, text)
        gateway = values['otel-gateway-automatic.yaml']
        attrs = {a['key']: a['value'] for a in gateway['config']['processors']['resource/cluster']['attributes']}
        self.assertEqual(attrs['k8s.cluster.name'], contract['clusterName'])
        self.assertEqual(attrs['deployment.environment.name'], 'dev')
        self.assertEqual(attrs['hopcharlie.environment.name'], 'onprem')
        self.assertIn('hopcharlie.environment.name', values['alloy.yaml']['alloy']['configMap']['content'])
        self.assertEqual(gateway['extraManifests'][0]['spec']['storageClassName'], 'local-path')
        self.assertIn('@sha256:', gateway['image']['tag'])
        self.assertFalse(gateway['config']['exporters']['otlphttp/central']['tls'].get('insecure', False))
        self.assertIn('filter/sensitive', gateway['config']['service']['pipelines']['logs/app']['processors'])
        self.assertTrue(values['alloy.yaml']['alloy']['mounts']['varlog'])
        self.assertFalse(values['alloy.yaml']['rbac']['create'])
        for scrape in values['kube-prometheus-stack.yaml']['prometheus']['prometheusSpec']['additionalScrapeConfigs']:
            bindings = {r['target_label']: r.get('replacement') for r in scrape['relabel_configs'] if 'replacement' in r}
            self.assertEqual(bindings['environment'], 'onprem')
            self.assertEqual(bindings['cluster'], contract['clusterName'])

    def test_collector_owns_environment_not_the_semantic_stage(self):
        for directory, env in [('aws-dev-workload', 'eks'), ('gcp-dev-workload', 'gke')]:
            cfg = json.loads((ROOT / f'clusters/{directory}/values/otel-gateway-automatic.yaml').read_text())
            attrs = {a['key']: a['value'] for a in cfg['config']['processors']['resource/cluster']['attributes']}
            self.assertEqual(attrs['hopcharlie.environment.name'], env)
            self.assertEqual(attrs['deployment.environment.name'], 'dev')


class Render(unittest.TestCase):
    def test_onprem_transport_does_not_replace_existing_management_ingress_or_gke_query(self):
        for location, expected in [('onprem-dev-workload', {'observability-central', 'observability-onprem-query-ingress'}),
                                   ('aws-dev-management', {'observability-onprem-metrics'})]:
            filename = 'observability-transport.yaml' if location.startswith('onprem') else 'observability-onprem-query-transport.yaml'
            proc = subprocess.run([os.environ.get('HELM', 'helm'), 'template', 'proof',
                                   str(ROOT / 'helm/charts/observability-transport'), '-n', 'observability',
                                   '-f', str(ROOT / 'clusters' / location / 'values' / filename)],
                                  capture_output=True, text=True, timeout=30)
            self.assertEqual(proc.returncode, 0, proc.stderr)
            docs = [d for d in yaml.safe_load_all(proc.stdout) if d]
            self.assertEqual({d['metadata']['name'] for d in docs if d['kind'] == 'Service'}, expected)
            self.assertFalse(any(d['metadata']['name'] == 'observability-gke-metrics' for d in docs))
            if location.startswith('aws'):
                self.assertFalse(any(d['metadata']['name'] == 'observability-central-ingress' for d in docs))
                self.assertEqual({d['metadata']['name'] for d in docs if d['kind'] == 'NetworkPolicy'},
                                 {'observability-transport-egress-client-onprem'})
            else:
                tags = [d['metadata']['annotations']['tailscale.com/tags'] for d in docs if d['kind'] == 'Service']
                self.assertEqual(set(tags), {'tag:hop-observability-onprem-export', 'tag:hop-observability-onprem-query'})

    def test_actual_pinned_charts_and_no_automatic_activation(self):
        paths = dict(zip(('alloy', 'opentelemetry-collector', 'kube-prometheus-stack'),
                         (os.environ['ALLOY_CHART'], os.environ['OTEL_CHART'], os.environ['KPS_CHART'])))
        _, values = module.prepare()
        rendered = module.render(values, paths)
        docs = [d for d in yaml.safe_load_all(rendered) if d]
        self.assertTrue(any(d['kind'] == 'DaemonSet' and d['metadata']['name'] == 'observability-alloy' for d in docs))
        self.assertTrue(any(d['kind'] == 'Deployment' and d['metadata']['name'] == 'observability-gateway' for d in docs))
        self.assertTrue(any(d['kind'] == 'Prometheus' for d in docs))
        self.assertFalse(any(d['kind'] in ('Application', 'AppProject', 'CustomResourceDefinition') for d in docs))
        secrets = [d for d in docs if d['kind'] == 'Secret']
        self.assertEqual({d['metadata']['name'] for d in secrets}, {'monitoring-prometheus-scrape-confg'})
        self.assertTrue(all(d['spec'].get('type', 'ClusterIP') != 'LoadBalancer' for d in docs if d['kind'] == 'Service'))
        self.assertTrue(any(d['kind'] == 'ClusterRole' for d in docs))
        profile = yaml.safe_load((ROOT / 'helm/charts/hopcharlie-app/ci/stateful-values.yaml').read_text())
        profile['env'] = 'onprem'
        profile.setdefault('profile', {})['observability'] = {
            'enabled': True, 'metricsEnabled': True, 'endpoint': 'https://observability-gateway.observability.svc:443',
            'caBundle': '-----BEGIN CERTIFICATE-----\nSYNTHETIC-PUBLIC-CA\n-----END CERTIFICATE-----',
            'clusterName': 'charlie-dev-onprem-workload', 'cloudProvider': 'onprem', 'region': 'onprem'}
        result = subprocess.run([os.environ.get('HELM', 'helm'), 'template', 'proof', str(ROOT / 'helm/charts/hopcharlie-app'),
                                 '-n', 'proof', '--kube-version', '1.35.0', '-f', '-'],
                                input=json.dumps(profile), capture_output=True, text=True, timeout=30)
        self.assertEqual(result.returncode, 0, result.stderr)
        appdocs = [d for d in yaml.safe_load_all(result.stdout) if d]
        monitor = next(d for d in appdocs if d['kind'] == 'PodMonitor')
        labels = {x['targetLabel']: x['replacement'] for x in monitor['spec']['podMetricsEndpoints'][0]['relabelings']}
        self.assertEqual(labels, {'environment': 'onprem', 'cluster': 'charlie-dev-onprem-workload'})
        for d in appdocs:
            if d['kind'] == 'Deployment':
                env = {e['name']: e.get('value') for e in d['spec']['template']['spec']['containers'][0]['env']}
                self.assertIn('hopcharlie.environment.name=onprem', env['OTEL_RESOURCE_ATTRIBUTES'])


if __name__ == '__main__':
    unittest.main()
