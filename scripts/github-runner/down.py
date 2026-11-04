#!/usr/bin/env python3
"""Remove the self-hosted runner host that up.py made (ADR 0042).

Without --apply it changes nothing and prints what it would remove. With --apply
it unregisters the runners on the instance through SSM with an org removal token
(config.sh remove), deletes any of hopcharlie-runner-1..3 that GitHub still lists,
terminates the instance tagged Name=hopcharlie-github-runner and deletes its
security group, instance profile and IAM role. The removal token is never
printed. Jobs with runs-on: [self-hosted, hopcharlie] then wait in the queue.

Needs aws (profile hopcharlie, or AWS_PROFILE) and gh with the admin:org scope:
    gh auth refresh -h github.com -s admin:org
"""
import argparse
import shlex
import subprocess
import sys
import time

from up import (NAME, ORG, RUNNER_NAMES, SSM_POLICY, Refused, aws, aws_try, check_tools_and_account, default_vpc,
                gh_scope_problem, iam_state, instances, org_runners, require, run, run_on_instance, security_group,
                show_runners)

REMOVE = r'''
for dir in /opt/actions-runner/*/; do
  [ -f "$dir.runner" ] || continue
  cd "$dir"
  if [ -f .service ]; then ./svc.sh stop; ./svc.sh uninstall; fi
  runuser -u runner -- ./config.sh remove --token "$RUNNER_TOKEN" || echo "config.sh remove failed in $dir"
done
'''


def main(argv=None):
    parser = argparse.ArgumentParser(description=__doc__, formatter_class=argparse.RawDescriptionHelpFormatter)
    parser.add_argument('--apply', action='store_true', help='remove the runners and delete the AWS resources')
    args = parser.parse_args(argv)
    try:
        check_tools_and_account()
        gh_problem = gh_scope_problem()
        require(not gh_problem or not args.apply, gh_problem)
        runners, gh_error = org_runners()
        found = instances()
        vpc = default_vpc()
        group = security_group(vpc)
        role, attached, _, profile, roles = iam_state()

        print('org runners:')
        if runners is None:
            print(f'  (gh cannot list them: {gh_error})')
        else:
            show_runners(runners)
        for instance in found:
            print(f"instance {instance['InstanceId']} ({instance['State']['Name']}): unregister its runners, terminate")
        print(f"security group: {group['GroupId'] + ' delete' if group else 'none'}")
        print(f"instance profile {NAME}: {'delete' if profile else 'none'}")
        print(f"IAM role {NAME}: {'delete' if role else 'none'}")
        print(f"gh: {gh_problem or 'may create the removal token'}")
        if not args.apply:
            print('Nothing changed. Re-run with --apply to remove all of the above.')
            return 0

        online = []
        for instance in found:
            if instance['State']['Name'] != 'running':
                continue
            info = aws('ssm', 'describe-instance-information', '--filters',
                       f"Key=InstanceIds,Values={instance['InstanceId']}")['InstanceInformationList']
            if info and info[0]['PingStatus'] == 'Online':
                online.append(instance['InstanceId'])
        for instance_id in online:
            token = run(['gh', 'api', '-X', 'POST', f'orgs/{ORG}/actions/runners/remove-token', '--jq', '.token']).stdout.strip()
            require(token, 'gh returned no removal token')
            try:
                run_on_instance(instance_id, ['set -eu', f'export RUNNER_TOKEN={shlex.quote(token)}', REMOVE],
                                'github runner removal (infra ADR 0042)', token, timeout=600)
            except Refused as error:
                print(f'{error}; deleting the runners through the API instead')

        # Whatever is still registered (instance gone, stopped or not in SSM) is deleted through the API.
        runners, gh_error = org_runners()
        require(runners is not None, f'gh cannot list the org runners ({gh_error})')
        for runner in runners:
            if runner['name'] in RUNNER_NAMES:
                run(['gh', 'api', '-X', 'DELETE', f"orgs/{ORG}/actions/runners/{runner['id']}"])
                print(f"deleted runner {runner['name']} from the org")

        ids = [i['InstanceId'] for i in found]
        if ids:
            aws('ec2', 'terminate-instances', '--instance-ids', *ids)
            aws('ec2', 'wait', 'instance-terminated', '--instance-ids', *ids)
            print(f"terminated {', '.join(ids)}")
        if group:
            # The instance's network interface can take a moment to go away after termination.
            for attempt in range(1, 13):
                result = aws_try('ec2', 'delete-security-group', '--group-id', group['GroupId'], missing='DependencyViolation')
                if result is not None:
                    break
                require(attempt < 12, f"security group {group['GroupId']} is still in use")
                time.sleep(10)
            print(f"deleted security group {group['GroupId']}")
        if profile:
            if NAME in roles:
                aws('iam', 'remove-role-from-instance-profile', '--instance-profile-name', NAME, '--role-name', NAME)
            aws('iam', 'delete-instance-profile', '--instance-profile-name', NAME)
            print(f'deleted instance profile {NAME}')
        if role:
            if SSM_POLICY in attached:
                aws('iam', 'detach-role-policy', '--role-name', NAME, '--policy-arn', SSM_POLICY)
            aws('iam', 'delete-role', '--role-name', NAME)
            print(f'deleted IAM role {NAME}')
        print('Done. Jobs with runs-on: [self-hosted, hopcharlie] now wait in the queue.')
    except Refused as error:
        print(f'github-runner down: {error}', file=sys.stderr)
        return 1
    except subprocess.CalledProcessError as error:
        print(f'github-runner down: {error.cmd[0]} failed; see its error above. Run it again after fixing that.', file=sys.stderr)
        return 1
    except (KeyError, IndexError, ValueError, TypeError):
        print('github-runner down: unexpected CLI response; nothing more was changed.', file=sys.stderr)
        return 1
    return 0


if __name__ == '__main__':
    sys.exit(main())
