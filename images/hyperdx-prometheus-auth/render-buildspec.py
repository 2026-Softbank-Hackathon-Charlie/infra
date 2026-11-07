#!/usr/bin/env python3
"""Render existing hopcharlie-app-build override; no AWS calls or local builds."""
import argparse,base64,json,shlex,subprocess,re
from pathlib import Path
root=Path(__file__).resolve().parent
parser=argparse.ArgumentParser();parser.add_argument('--publish-source-sha');args=parser.parse_args()
if args.publish_source_sha and not re.fullmatch('[0-9a-f]{40}',args.publish_source_sha):parser.error('full source SHA required')
commands=['set -eu', 'task_dir=$(mktemp -d /tmp/hyperdx-auth.XXXXXXXX)', 'cd "$task_dir"', 'mkdir -p tests']
for name in ['Dockerfile','patch-prometheus-auth.cjs','tests/test-prometheus-auth.cjs']:
    raw=subprocess.check_output(['git','show',args.publish_source_sha+':images/hyperdx-prometheus-auth/'+name],cwd=root) if args.publish_source_sha else (root/name).read_bytes()
    data=base64.b64encode(raw).decode()
    script=f'import base64;from pathlib import Path;Path({name!r}).write_bytes(base64.b64decode({data!r}))'
    commands.append('python3 -c '+shlex.quote(script))
base='docker.hyperdx.io/hyperdx/hyperdx:2.39.1@sha256:619783ca66d6c81f72a0106f4013d65865ac42e064491b19c0019ffec268f316'
commands += ['docker build --pull -t hyperdx-prometheus-auth:verified .',
'docker run --rm --network none --entrypoint node -v "$PWD/tests:/tests:ro" hyperdx-prometheus-auth:verified --test /tests/test-prometheus-auth.cjs',
'if docker run --rm --network none --entrypoint node -v "$PWD/tests:/tests:ro" '+base+' --test /tests/test-prometheus-auth.cjs; then echo "ERROR: original API unexpectedly passed regression"; exit 1; fi',
'docker image inspect hyperdx-prometheus-auth:verified --format "{{.Id}}"']
if args.publish_source_sha:
    data=base64.b64encode((root/'publish-image.py').read_bytes()).decode()
    script=f'import base64;from pathlib import Path;Path("publish-image.py").write_bytes(base64.b64decode({data!r}))'
    commands += ['python3 -c '+shlex.quote(script), 'python3 publish-image.py '+args.publish_source_sha]
print(json.dumps({'version':'0.2','phases':{'build':{'commands':commands}}},indent=2))
