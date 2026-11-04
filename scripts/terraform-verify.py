#!/usr/bin/env python3
"""Run backend-free validation/mock tests in a disposable clean tree."""
import argparse
import os
from pathlib import Path
import shutil
import subprocess
import tempfile
import time

ROOT = Path(__file__).resolve().parents[1]
ROOTS = ('bootstrap/aws', 'account/aws', 'aws/dev/foundation', 'modules/eks', 'aws/dev/management', 'aws/dev/workload', 'integrations/aws-gcp/tailscale')


def relative(stack):
    return Path('environments') / stack if stack.startswith('aws/dev/') else Path(stack)


def clean_copy(src, dest):
    for p in src.rglob('*'):
        rel = p.relative_to(src)
        if '.terraform' in rel.parts or any((src / Path(*rel.parts[:i])).is_symlink() for i in range(1, len(rel.parts) + 1)) or not p.is_file():
            continue
        if p.name in ('override.tf', 'override.tf.json') or p.name.endswith(('_override.tf', '_override.tf.json')):
            continue
        if p.name == '.terraform.lock.hcl' or p.suffix in ('.tf', '.tftpl') or p.name.endswith('.tftest.hcl') or (rel.parts[0] == 'config' and p.suffix == '.json'):
            target = dest / rel
            target.parent.mkdir(parents=True, exist_ok=True)
            shutil.copy2(p, target)


def main():
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument('--mode', choices=('validate', 'test', 'all'), default='all')
    parser.add_argument('--stack', choices=ROOTS)
    args = parser.parse_args()
    roots = (args.stack,) if args.stack else ROOTS
    with tempfile.TemporaryDirectory(prefix='charlie-verify-') as temp:
        work = Path(temp)
        tree = work / 'terraform'
        clean_copy(ROOT / 'terraform', tree)
        env = {k: v for k, v in os.environ.items() if not k.startswith(('TF_VAR_', 'TF_CLI_ARGS')) and k not in ('TF_DATA_DIR', 'TF_WORKSPACE', 'TF_CLI_CONFIG_FILE')}
        # CI restores only providers here; init remains serial within this process.
        cache = Path(env.get('TF_PLUGIN_CACHE_DIR', str(work / 'provider-cache')))
        cache.mkdir(parents=True, exist_ok=True)
        env['TF_PLUGIN_CACHE_DIR'] = str(cache.resolve())
        env['TF_IN_AUTOMATION'] = 'true'
        env['TF_INPUT'] = 'false'
        for stack in roots:
            path = tree / relative(stack)
            has_tests = (path / 'tests').is_dir()
            if args.mode == 'test' and not has_tests:
                continue
            started = time.monotonic()
            commands = [['init', '-backend=false', '-input=false', '-lockfile=readonly']]
            if args.mode in ('validate', 'all'):
                commands.append(['validate'])
            if args.mode in ('test', 'all') and has_tests:
                commands.append(['test'])
            for command in commands:
                subprocess.run(['terraform', f'-chdir={path}', *command], env=env, check=True)
            print(f'Timing: {stack}: {time.monotonic() - started:.1f}s ({args.mode})', flush=True)


if __name__ == '__main__':
    main()
