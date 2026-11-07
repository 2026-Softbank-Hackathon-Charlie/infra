#!/usr/bin/env python3
"""Prepare onprem OTel/Alloy/Prometheus values locally; never apply or read credentials."""
import argparse
import json
import os
from pathlib import Path
import subprocess

ROOT = Path(__file__).resolve().parents[1]
FILES = ('alloy.yaml', 'otel-gateway-automatic.yaml', 'kube-prometheus-stack.yaml',
         'observability-boundary.yaml')


def prepare():
    contract = json.loads((ROOT / 'clusters/onprem-dev-workload/observability-contract.json').read_text())
    if contract['enabled'] or not contract['activationRequiresExecutionApproval']:
        raise ValueError('onprem preparation must remain inactive and require execution approval')
    source = ROOT / contract['sourceValuesDirectory']
    # Copy reviewed, digest-pinned collectors, queues, TLS, auth and redaction;
    # replace only identity/storage. No Kubernetes/cloud API is used.
    replacements = {
        'charlie-dev-gcp-workload': contract['clusterName'],
        'charlie-gcp-balanced': contract['storageClassName'],
        'asia-northeast3-a': contract['zone'],
        'asia-northeast3': contract['region'],
        'gke-kubelet': 'onprem-kubelet',
    }

    def translate(value):
        if isinstance(value, dict):
            return {k: translate(v) for k, v in value.items()}
        if isinstance(value, list):
            return [translate(v) for v in value]
        if isinstance(value, str):
            for old, new in replacements.items():
                value = value.replace(old, new)
            return value
        return value

    values = {name: translate(json.loads((source / name).read_text())) for name in FILES}
    gateway = values['otel-gateway-automatic.yaml']
    attributes = gateway['config']['processors']['resource/cluster']['attributes']
    for attr in attributes:
        if attr['key'] == 'cloud.provider':
            attr['value'] = contract['cloudProvider']
    found = False
    for attr in attributes:
        if attr['key'] == 'hopcharlie.environment.name':
            attr['value'], found = 'onprem', True
    if not found:
        raise ValueError('reviewed gateway physical environment binding missing')
    # The semantic deployment stage remains dev; physical location is separate.
    alloy = values['alloy.yaml']['alloy']['configMap']
    alloy['content'] = alloy['content'].replace(
        '`set(resource.attributes["cloud.provider"], "gcp")`,',
        '`set(resource.attributes["cloud.provider"], "onprem")`,')
    marker = '`set(resource.attributes["deployment.environment.name"], "dev")`,'
    if marker not in alloy['content']:
        raise ValueError('reviewed Alloy identity contract changed')
    alloy['content'] = alloy['content'].replace(marker, marker +
        '\n      `set(resource.attributes["hopcharlie.environment.name"], "onprem")`,')
    prom = values['kube-prometheus-stack.yaml']['prometheus']['prometheusSpec']
    prom['externalLabels'] = {'cluster': contract['clusterName'], 'environment': 'onprem'}
    # External labels are attached on export, not to local PromQL up selectors.
    # Local binding is also added to every additional scrape target.
    for scrape in prom.get('additionalScrapeConfigs', []):
        scrape.setdefault('relabel_configs', []).extend([
            {'target_label': 'cluster', 'replacement': contract['clusterName']},
            {'target_label': 'environment', 'replacement': 'onprem'},
        ])
    # Node-readable metadata roles are intentionally prepared for administrator
    # review, never granted to the existing delegated app enrollment identity.
    profile = {'enabled': False, 'metricsEnabled': False, 'endpoint': '', 'caBundle': '',
               'clusterName': contract['clusterName'], 'cloudProvider': 'onprem', 'region': 'onprem'}
    values['app-observability.yaml'] = {'environments': {'onprem': {'profile': {'observability': profile}}}}
    return contract, values


def render(values, chart_paths):
    """Render only explicitly supplied local pinned chart packages; no download/apply."""
    expected = {'alloy', 'opentelemetry-collector', 'kube-prometheus-stack'}
    if set(chart_paths) != expected:
        raise ValueError('exactly the three reviewed local chart paths are required')
    contract, _ = prepare()
    docs = []
    for chart, release, value_file in [
        ('alloy', 'observability-agent', 'alloy.yaml'),
        ('opentelemetry-collector', 'observability-gateway', 'otel-gateway-automatic.yaml'),
        ('kube-prometheus-stack', 'monitoring', 'kube-prometheus-stack.yaml'),
        ('boundary', 'observability-boundary', 'observability-boundary.yaml'),
    ]:
        path = ROOT / 'helm/charts/observability-boundary' if chart == 'boundary' else Path(chart_paths[chart]).resolve()
        if chart != 'boundary':
            show = subprocess.run([os.environ.get('HELM', 'helm'), 'show', 'chart', str(path)],
                                  capture_output=True, text=True, timeout=30)
            import yaml
            metadata = yaml.safe_load(show.stdout) if show.returncode == 0 else {}
            if metadata.get('name') != chart or metadata.get('version') != contract['charts'][chart]:
                raise ValueError('local chart name/version does not match reviewed pins')
        cmd = [os.environ.get('HELM', 'helm'), 'template', release, str(path), '-n', 'observability',
               '--kube-version', '1.35.0', '--skip-crds', '-f', '-']
        proc = subprocess.run(cmd, input=json.dumps(values[value_file]), capture_output=True,
                              text=True, timeout=60)
        if proc.returncode:
            raise ValueError('local Helm render failed')
        docs.append(proc.stdout)
    return '\n---\n'.join(docs)


def main():
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument('--output-dir', type=Path, required=True)
    parser.add_argument('--alloy-chart', type=Path)
    parser.add_argument('--otel-chart', type=Path)
    parser.add_argument('--prometheus-chart', type=Path)
    args = parser.parse_args()
    contract, values = prepare()
    selected = (args.alloy_chart, args.otel_chart, args.prometheus_chart)
    if any(selected) and not all(selected):
        parser.error('provide all three local pinned charts or none')
    args.output_dir.mkdir(parents=True, exist_ok=True)
    for filename, value in values.items():
        (args.output_dir / filename).write_text(json.dumps(value, indent=2) + '\n')
    (args.output_dir / 'contract.json').write_text(json.dumps(contract, indent=2) + '\n')
    if all(selected):
        result = render(values, dict(zip(('alloy', 'opentelemetry-collector', 'kube-prometheus-stack'), selected)))
        (args.output_dir / 'prepared-resources.yaml').write_text(result)
    print('Prepared local files only. No collector is installed; no receiving evidence is claimed.')
    print('TLS/auth secrets, transport/ACL, CRDs/RBAC, host-path access and app activation require separate review and approval.')


if __name__ == '__main__':
    main()
