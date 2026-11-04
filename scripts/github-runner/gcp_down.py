#!/usr/bin/env python3
"""Remove the GCP self-hosted runner host that gcp_up.py made (ADR 0045).

Without --apply it changes nothing and prints what it would remove. With --apply
it deletes the VM hopcharlie-github-runner with its disk, deletes the org runners
named hopcharlie-gcp-runner-* through the GitHub API and deletes the two firewall
rules. The AWS runners hopcharlie-runner-1..3 are left alone (down.py removes
them). Jobs with runs-on: [self-hosted, hopcharlie] wait in the queue when no
runner is left.

Needs gcloud with project project-b47cdba3-db5b-42a2-9a6 active and gh with the
admin:org scope:
    gh auth refresh -h github.com -s admin:org
"""
import argparse
from pathlib import Path
import subprocess
import sys

from gcp_up import (FIREWALLS, NAME, RUNNER_PREFIX, ZONE, check_tools_and_project, gcloud, gcloud_try, instance,
                    show_runners)
from up import ORG, Refused, gh_scope_problem, org_runners, require, run


def main(argv=None):
    parser = argparse.ArgumentParser(description=__doc__, formatter_class=argparse.RawDescriptionHelpFormatter)
    parser.add_argument('--apply', action='store_true', help='delete the VM, the GCP runners and the firewall rules')
    args = parser.parse_args(argv)
    try:
        check_tools_and_project()
        gh_problem = gh_scope_problem()
        require(not gh_problem or not args.apply, gh_problem)
        runners, gh_error = org_runners()
        vm = instance()
        rules = [name for name in FIREWALLS if gcloud_try('compute', 'firewall-rules', 'describe', name) is not None]

        print('org runners:')
        if runners is None:
            print(f'  (gh cannot list them: {gh_error})')
        else:
            show_runners(runners)
            for runner in runners:
                if runner['name'].startswith(f'{RUNNER_PREFIX}-'):
                    print(f"runner {runner['name']}: delete")
        print(f"VM {NAME}: {vm['status'] + ', delete with its disk' if vm else 'none'}")
        for name in FIREWALLS:
            print(f"firewall rule {name}: {'delete' if name in rules else 'none'}")
        print(f"gh: {gh_problem or 'may delete the runners'}")
        if not args.apply:
            print('Nothing changed. Re-run with --apply to remove all of the above.')
            return 0

        if vm:
            gcloud('compute', 'instances', 'delete', NAME, f'--zone={ZONE}', '--delete-disks=all')
            print(f'deleted VM {NAME}')
        # VM first, so its runners take no more jobs. GitHub refuses to delete a runner it still
        # shows as busy; then the gh call fails and a later re-run deletes the rest.
        runners, gh_error = org_runners()
        require(runners is not None, f'gh cannot list the org runners ({gh_error})')
        for runner in runners:
            if runner['name'].startswith(f'{RUNNER_PREFIX}-'):
                run(['gh', 'api', '-X', 'DELETE', f"orgs/{ORG}/actions/runners/{runner['id']}"])
                print(f"deleted runner {runner['name']} from the org")
        if rules:
            gcloud('compute', 'firewall-rules', 'delete', *rules)
            print(f"deleted firewall rules {', '.join(rules)}")
        print('Done. Jobs with runs-on: [self-hosted, hopcharlie] run only on the remaining runners, if any.')
    except Refused as error:
        print(f'github-runner gcp down: {error}', file=sys.stderr)
        return 1
    except subprocess.CalledProcessError as error:
        print(f'github-runner gcp down: {Path(error.cmd[0]).name} failed; see its error above. Run it again after fixing that.',
              file=sys.stderr)
        return 1
    except (KeyError, IndexError, ValueError, TypeError):
        print('github-runner gcp down: unexpected CLI response; nothing more was changed.', file=sys.stderr)
        return 1
    return 0


if __name__ == '__main__':
    sys.exit(main())
