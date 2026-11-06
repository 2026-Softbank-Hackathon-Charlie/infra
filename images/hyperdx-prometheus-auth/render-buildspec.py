#!/usr/bin/env python3
"""Render existing hopcharlie-app-build override; no AWS calls or local builds."""
import base64,json,shlex
from pathlib import Path
root=Path(__file__).resolve().parent
commands=['set -eu', 'task_dir=$(mktemp -d /tmp/hyperdx-auth.XXXXXXXX)', 'cd "$task_dir"', 'mkdir -p tests']
for name in ['Dockerfile','patch-prometheus-auth.cjs','tests/test-prometheus-auth.cjs']:
    data=base64.b64encode((root/name).read_bytes()).decode()
    script=f'import base64;from pathlib import Path;Path({name!r}).write_bytes(base64.b64decode({data!r}))'
    commands.append('python3 -c '+shlex.quote(script))
base='docker.hyperdx.io/hyperdx/hyperdx:2.39.1@sha256:619783ca66d6c81f72a0106f4013d65865ac42e064491b19c0019ffec268f316'
commands += ['docker build --pull -t hyperdx-prometheus-auth:verified .',
'docker run --rm --network none --entrypoint node -v "$PWD/tests:/tests:ro" hyperdx-prometheus-auth:verified --test /tests/test-prometheus-auth.cjs',
'if docker run --rm --network none --entrypoint node -v "$PWD/tests:/tests:ro" '+base+' --test /tests/test-prometheus-auth.cjs; then echo "ERROR: original API unexpectedly passed regression"; exit 1; fi',
'docker image inspect hyperdx-prometheus-auth:verified --format "{{.Id}}"']
print(json.dumps({'version':'0.2','phases':{'build':{'commands':commands}}},indent=2))
