#!/usr/bin/env python3
"""Validate only the three HopCharlie roots in a clean, credential-free tree."""
import os
from pathlib import Path
import shutil
import subprocess
import tempfile

SOURCE = Path(__file__).resolve().parents[1] / 'terraform/platform/aws'
ASSETS = (
    'backend.tf', 'main.tf', 'eks.tf', 'app-build.tf', 'tests/app-build.tftest.hcl', 'outputs.tf', 'variables.tf', 'versions.tf',
    '.terraform.lock.hcl', 'user_data.sh.tftpl',
    'state/backend.tf', 'state/main.tf', 'state/.terraform.lock.hcl',
    'dns/backend.tf', 'dns/main.tf', 'dns/.terraform.lock.hcl',
    'host/Caddyfile', 'host/compose.yaml', 'host/compose.tailscale.yaml',
    'host/sync.sh', 'host/envs.yaml', 'host/apps.yaml',
)


def clean_copy(source, destination):
    for name in ASSETS:
        relative = Path(name)
        path = source / relative
        if source.is_symlink() or any((source / Path(*relative.parts[:i])).is_symlink()
                                      for i in range(1, len(relative.parts) + 1)):
            raise ValueError(f'Refusing linked asset: {name}')
        target = destination / relative
        target.parent.mkdir(parents=True, exist_ok=True)
        shutil.copy2(path, target)


def main():
    with tempfile.TemporaryDirectory(prefix='hopcharlie-validate-') as temp:
        work = Path(temp)
        tree = work / 'aws'
        clean_copy(SOURCE, tree)
        env = {key: value for key, value in os.environ.items()
               if not key.startswith(('TF_', 'AWS_', 'CLOUDFLARE_'))}
        # Ignore local CLI overrides, profiles and metadata credentials entirely.
        for key, name in (('TF_CLI_CONFIG_FILE', 'terraform.rc'),
                          ('AWS_CONFIG_FILE', 'config'),
                          ('AWS_SHARED_CREDENTIALS_FILE', 'credentials')):
            path = work / name
            path.write_text('', encoding='utf-8')
            env[key] = str(path)
        env.update(TF_IN_AUTOMATION='true', TF_INPUT='false', AWS_EC2_METADATA_DISABLED='true')
        for path in (tree, tree / 'state', tree / 'dns'):
            for command in (['fmt', '-check'],
                            ['init', '-backend=false', '-input=false', '-lockfile=readonly'],
                            ['validate']):
                subprocess.run(['terraform', f'-chdir={path}', *command], cwd=path, env=env, check=True)
            if path == tree:
                subprocess.run(['terraform', f'-chdir={path}', 'test'], cwd=path, env=env, check=True)


if __name__ == '__main__':
    main()
