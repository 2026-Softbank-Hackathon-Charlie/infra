#!/usr/bin/env python3
"""Verify local ClickStack and real pinned upstream operator/agent charts; no cluster access."""
import hashlib
import json
import os
from pathlib import Path
import subprocess
import sys
import tarfile
import tempfile

ROOT = Path(__file__).resolve().parents[1]
HELM = os.environ.get('HELM', 'helm')
VERSIONS = json.loads((ROOT / 'helm/versions.json').read_text())


def main():
    with tempfile.TemporaryDirectory(prefix='charlie-clickstack-check-') as tmp:
        env = dict(os.environ)
        for key, name in (('CLICKSTACK_OPERATORS_CHART', 'clickstack-operators'), ('OTEL_CHART', 'opentelemetry-collector'), ('ALLOY_CHART', 'alloy'), ('KPS_CHART', 'kube-prometheus-stack')):
            if env.get(key):
                if not (Path(env[key]) / 'Chart.yaml').is_file():
                    raise SystemExit(f'{key} does not contain a chart')
                continue
            pin = VERSIONS['charts'][name]
            # Helm's public repository resolves a fixed chart version, never latest.
            subprocess.run([HELM, 'pull', name, '--repo', pin['repo'], '--version', pin['version'], '--destination', tmp], check=True)
            archive = Path(tmp) / f'{name}-{pin["version"]}.tgz'
            if pin.get('sha256') and hashlib.sha256(archive.read_bytes()).hexdigest() != pin['sha256']:
                raise SystemExit(f'Chart checksum mismatch: {name}')
            with tarfile.open(archive) as bundle:
                bundle.extractall(tmp, filter='data')
            env[key] = str(Path(tmp) / name)
        subprocess.run([sys.executable, str(ROOT / 'scripts/tests/test-clickstack.py')], env=env, check=True)
        subprocess.run([sys.executable, str(ROOT / 'scripts/tests/test-automatic-observability.py')], env=env, check=True)
        subprocess.run([sys.executable, str(ROOT / 'scripts/tests/test-gke-observability.py')], env=env, check=True)
        subprocess.run([sys.executable, str(ROOT / 'scripts/tests/test-onprem-observability.py')], env=env, check=True)


if __name__ == '__main__':
    main()
