#!/usr/bin/env python3
"""Install hash-pinned Linux tools into a private, disposable runner directory."""
import hashlib
import json
import os
from pathlib import Path
import platform
import subprocess
import sys
import tempfile
import urllib.request


def main():
    if sys.platform != 'linux' or platform.machine() != 'x86_64':
        raise ValueError('Linux x86_64 runner required')
    os.umask(0o077)
    root = Path(__file__).resolve().parents[1]
    lock = json.loads((root / 'config/clickstack-bootstrap-tools.json').read_text())
    target = Path(tempfile.mkdtemp(prefix='clickstack-tools-' + os.environ['GITHUB_RUN_ID'] + '-', dir=os.environ['RUNNER_TEMP']))
    for name, pin in lock.items():
        with urllib.request.urlopen(pin['url'], timeout=60) as stream:
            content = stream.read(100 * 1024 * 1024)
        if hashlib.sha256(content).hexdigest() != pin['sha256']:
            raise ValueError('Tool checksum rejected')
        path = target / ('plugin.deb' if name == 'session-manager-plugin' else name)
        path.write_bytes(content)
        if name == 'kubectl':
            path.chmod(0o700)
        else:
            subprocess.run(['dpkg-deb', '-x', str(path), str(target / 'ssm')], check=True,
                           stdout=subprocess.DEVNULL, stderr=subprocess.DEVNULL)
            path.unlink()
    paths = [target, target / 'ssm/usr/local/sessionmanagerplugin/bin']
    if not (paths[1] / 'session-manager-plugin').is_file():
        raise ValueError('Plugin archive layout rejected')
    with open(os.environ['GITHUB_PATH'], 'a') as stream:
        stream.write('\n'.join(str(p) for p in paths) + '\n')
    # Later workflow steps own this path and remove it with an always cleanup step.
    with open(os.environ['GITHUB_OUTPUT'], 'a') as stream:
        stream.write('directory=' + str(target) + '\n')
    print('Pinned kubectl and SSM plugin installed in private runner directory.')


if __name__ == '__main__':
    try:
        main()
    except Exception:
        sys.exit('Tool installation rejected; no credential output.')
