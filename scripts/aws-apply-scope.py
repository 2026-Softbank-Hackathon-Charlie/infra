"""Report whether a main push changes inputs used by automatic AWS apply."""
import os
import re
import subprocess


AWS_ROOTS = (
    'terraform/bootstrap/aws/',
    'terraform/environments/aws/dev/foundation/',
    'terraform/environments/aws/dev/management/',
    'terraform/environments/aws/dev/workload/',
    'terraform/modules/eks/',
)
# Keep external file()/templatefile() inputs in sync when Terraform gains one.
SHARED_INPUTS = {
    '.terraform-version',
    'config/project.json',
    'scripts/tf-ci.sh',
    'scripts/common.sh',
    'scripts/project-config.py',
    'terraform/config/aws-load-balancer-controller-policy.json',
}
DOC_SUFFIXES = ('.md', '.mdx', '.markdown', '.rst', '.adoc', '.asciidoc')


def is_aws_input(path):
    if path in SHARED_INPUTS:
        return True
    return path.startswith(AWS_ROOTS) and not {'docs', 'tests'}.intersection(path.split('/')) and not path.lower().endswith(DOC_SUFFIXES + ('.tftest.hcl', '.example'))


def main():
    if os.environ.get('GITHUB_EVENT_NAME') != 'push' or os.environ.get('GITHUB_REF') != 'refs/heads/main':
        print('changed=false')
        return 0

    before = os.environ['BEFORE_SHA']
    after = os.environ['AFTER_SHA']
    if not all(re.fullmatch(r'(?:[0-9a-f]{40}|[0-9a-f]{64})', sha) for sha in (before, after)):
        raise ValueError('BEFORE_SHA and AFTER_SHA must be full Git commit hashes')
    if set(before) == {'0'}:
        command = ['git', 'ls-tree', '-r', '--name-only', '-z', after]
    else:
        command = ['git', 'diff', '--no-renames', '--name-only', '-z', before, after, '--']
    try:
        paths = subprocess.check_output(command, shell=False).split(b'\0')
    except subprocess.CalledProcessError as error:
        return error.returncode
    changed = any(is_aws_input(path.decode('utf-8', errors='surrogateescape')) for path in paths if path)
    print('changed=' + str(changed).lower())
    return 0


if __name__ == '__main__':
    raise SystemExit(main())
