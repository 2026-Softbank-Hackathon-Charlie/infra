#!/usr/bin/env python3
"""Bring up the org's self-hosted GitHub Actions runner host (ADR 0045).

In the hackathon account (525091405886, ap-northeast-2) it reuses or creates
the IAM role and instance profile hopcharlie-github-runner (EC2 trust, one
inline policy with what the SSM agent needs: AmazonSSMManagedInstanceCore
without ssm:GetParameter(s), so job code that reaches the instance role
cannot read SSM parameters), a security group without ingress in the default
VPC, and one Ubuntu 24.04 t3.xlarge tagged Name=hopcharlie-github-runner (80 GiB
encrypted gp3, public IP in a default subnet, IMDSv2 with hop limit 1). When SSM
reports the instance Online, it runs bootstrap.sh there through SSM Run Command
with an org registration token: tools, three runners hopcharlie-runner-1..3 with
the label hopcharlie, and a daily Docker prune. Last it lists the org's runners.

Safe to run again: existing resources are reused and configured runners are
skipped. --dry-run only reads and prints what would change.

The registration token is never printed or put on a command line. It does go
into the SSM command's parameters (command history) and expires after an hour.

Needs aws (profile hopcharlie, or AWS_PROFILE) and gh with the admin:org scope:
    gh auth refresh -h github.com -s admin:org
Remove everything with down.py.
"""
import argparse
import base64
import gzip
import json
import os
from pathlib import Path
import re
import shlex
import shutil
import subprocess
import sys
import tempfile
import time

HERE = Path(__file__).resolve().parent
ACCOUNT, REGION = '525091405886', 'ap-northeast-2'
ORG = '2026-Softbank-Hackathon-Charlie'
NAME = 'hopcharlie-github-runner'  # IAM role, instance profile, security group, instance Name tag
TAGS = {'Name': NAME, 'Project': 'hopcharlie', 'Component': 'github-runner', 'ManagedBy': 'infra/scripts/github-runner'}
INSTANCE_TYPE, DISK_GIB = 't3.xlarge', 80
LABEL = 'hopcharlie'
RUNNER_NAMES = [f'hopcharlie-runner-{n}' for n in (1, 2, 3)]
AMI_PARAMETER = '/aws/service/canonical/ubuntu/server/24.04/stable/current/amd64/hvm/ebs-gp3/ami-id'
# Attached by earlier versions of this script; detached when found.
SSM_POLICY = 'arn:aws:iam::aws:policy/AmazonSSMManagedInstanceCore'
AGENT_POLICY_NAME = 'ssm-agent'
# AmazonSSMManagedInstanceCore (2026-11-04) without ssm:GetParameter and
# ssm:GetParameters.
AGENT_POLICY = {'Version': '2012-10-17', 'Statement': [
    {'Effect': 'Allow', 'Resource': '*', 'Action': [
        'ssm:DescribeAssociation', 'ssm:GetDeployablePatchSnapshotForInstance', 'ssm:GetDocument',
        'ssm:DescribeDocument', 'ssm:GetManifest', 'ssm:ListAssociations', 'ssm:ListInstanceAssociations',
        'ssm:PutInventory', 'ssm:PutComplianceItems', 'ssm:PutConfigurePackageResult',
        'ssm:UpdateAssociationStatus', 'ssm:UpdateInstanceAssociationStatus', 'ssm:UpdateInstanceInformation']},
    {'Effect': 'Allow', 'Resource': '*', 'Action': [
        'ssmmessages:CreateControlChannel', 'ssmmessages:CreateDataChannel',
        'ssmmessages:OpenControlChannel', 'ssmmessages:OpenDataChannel']},
    {'Effect': 'Allow', 'Resource': '*', 'Action': [
        'ec2messages:AcknowledgeMessage', 'ec2messages:DeleteMessage', 'ec2messages:FailMessage',
        'ec2messages:GetEndpoint', 'ec2messages:GetMessages', 'ec2messages:SendReply']},
]}
TRUST = {'Version': '2012-10-17', 'Statement': [
    {'Effect': 'Allow', 'Principal': {'Service': 'ec2.amazonaws.com'}, 'Action': 'sts:AssumeRole'}]}
LIVE_STATES = 'pending,running,stopping,stopped'
COMMAND_DONE = ('Success', 'Failed', 'Cancelled', 'TimedOut')


class Refused(Exception):
    pass


def require(ok, message):
    if not ok:
        raise Refused(message)


def run(args, check=True, stdin=None):
    # stdout is captured and never echoed; CLI errors stay visible on stderr.
    return subprocess.run(args, input=stdin, check=check, text=True, encoding='utf-8', stdout=subprocess.PIPE)


def aws_args(*args):
    return ['aws', '--profile', os.environ.get('AWS_PROFILE') or 'hopcharlie', '--region', REGION, '--output', 'json', *args]


def aws(*args):
    out = run(aws_args(*args)).stdout
    return json.loads(out) if out.strip() else {}


def aws_try(*args, missing):
    """Like aws(), but returns None when the error output contains `missing` (NoSuchEntity and the like)."""
    result = subprocess.run(aws_args(*args), text=True, encoding='utf-8', stdout=subprocess.PIPE, stderr=subprocess.PIPE)
    if result.returncode == 0:
        return json.loads(result.stdout) if result.stdout.strip() else {}
    if missing in result.stderr:
        return None
    sys.stderr.write(result.stderr)
    raise subprocess.CalledProcessError(result.returncode, result.args)


def tags():
    return [{'Key': k, 'Value': v} for k, v in TAGS.items()]


def check_tools_and_account():
    for tool in ('aws', 'gh'):
        require(shutil.which(tool), f'{tool} is not on PATH')
    got = aws('sts', 'get-caller-identity')['Account']
    require(got == ACCOUNT, f'AWS profile is account {got}, not the hackathon account {ACCOUNT}')


def instances():
    data = aws('ec2', 'describe-instances', '--filters', f'Name=tag:Name,Values={NAME}',
               f'Name=instance-state-name,Values={LIVE_STATES}')
    return [i for r in data['Reservations'] for i in r['Instances']]


def default_vpc():
    vpcs = aws('ec2', 'describe-vpcs', '--filters', 'Name=is-default,Values=true')['Vpcs']
    require(vpcs, f'there is no default VPC in {REGION}')
    return vpcs[0]['VpcId']


def security_group(vpc):
    groups = aws('ec2', 'describe-security-groups', '--filters', f'Name=group-name,Values={NAME}',
                 f'Name=vpc-id,Values={vpc}')['SecurityGroups']
    return groups[0] if groups else None


def iam_state():
    """(role exists, attached policy ARNs, inline policy names, instance profile exists, roles in the profile)"""
    role = aws_try('iam', 'get-role', '--role-name', NAME, missing='NoSuchEntity')
    attached, inline = [], []
    if role is not None:
        attached = [p['PolicyArn'] for p in aws('iam', 'list-attached-role-policies', '--role-name', NAME)['AttachedPolicies']]
        inline = aws('iam', 'list-role-policies', '--role-name', NAME)['PolicyNames']
    profile = aws_try('iam', 'get-instance-profile', '--instance-profile-name', NAME, missing='NoSuchEntity')
    roles = [r['RoleName'] for r in profile['InstanceProfile']['Roles']] if profile else []
    return role is not None, attached, inline, profile is not None, roles


def gh_scope_problem():
    """Why gh cannot create runner tokens (OAuth and classic tokens need admin:org), or ''."""
    result = subprocess.run(['gh', 'api', '-i', 'user'], text=True, encoding='utf-8',
                            stdout=subprocess.PIPE, stderr=subprocess.PIPE)
    if result.returncode != 0:
        return result.stderr.strip() or 'gh api user failed'
    scopes = re.search(r'^x-oauth-scopes:(.*)$', result.stdout, re.IGNORECASE | re.MULTILINE)
    if scopes and 'admin:org' not in [s.strip() for s in scopes[1].split(',')]:
        return 'the gh token lacks admin:org; run: gh auth refresh -h github.com -s admin:org'
    return ''


def org_runners():
    """(the org's self-hosted runners, '') or (None, gh's error)."""
    result = subprocess.run(['gh', 'api', '--paginate', f'orgs/{ORG}/actions/runners', '--jq', '.runners[]'],
                            text=True, encoding='utf-8', stdout=subprocess.PIPE, stderr=subprocess.PIPE)
    if result.returncode != 0:
        return None, result.stderr.strip()
    return [json.loads(line) for line in result.stdout.splitlines() if line.strip()], ''


def show_runners(runners):
    ours = [r for r in runners if r['name'] in RUNNER_NAMES]
    for r in sorted(ours, key=lambda r: r['name']):
        labels = ','.join(label['name'] for label in r['labels'])
        print(f"  {r['name']}: {r['status']}{' busy' if r['busy'] else ''} ({labels})")
    if not ours:
        print('  (none of hopcharlie-runner-1..3)')


def runner_release():
    release = json.loads(run(['gh', 'api', 'repos/actions/runner/releases/latest']).stdout)
    version = release['tag_name'].removeprefix('v')
    match = re.search(r'<!-- BEGIN SHA linux-x64 -->([0-9a-f]{64})<!-- END SHA linux-x64 -->', release.get('body') or '')
    require(re.fullmatch(r'[0-9]+\.[0-9]+\.[0-9]+', version) and match,
            'the latest actions/runner release has no linux-x64 sha256 in its notes')
    return version, match[1]


def launch_spec(vpc):
    ami = aws('ssm', 'get-parameter', '--name', AMI_PARAMETER)['Parameter']['Value']
    image = aws('ec2', 'describe-images', '--image-ids', ami)['Images'][0]
    offered = {o['Location'] for o in aws('ec2', 'describe-instance-type-offerings', '--location-type', 'availability-zone',
                                          '--filters', f'Name=instance-type,Values={INSTANCE_TYPE}')['InstanceTypeOfferings']}
    subnets = sorted((s for s in aws('ec2', 'describe-subnets', '--filters', f'Name=vpc-id,Values={vpc}',
                                     'Name=default-for-az,Values=true')['Subnets'] if s['AvailabilityZone'] in offered),
                     key=lambda s: s['AvailabilityZone'])
    require(subnets, f'no default subnet of {vpc} is in a zone that offers {INSTANCE_TYPE}')
    spec = {
        'ImageId': ami, 'InstanceType': INSTANCE_TYPE, 'MinCount': 1, 'MaxCount': 1,
        'IamInstanceProfile': {'Name': NAME},
        # The default VPC has no NAT: the host reaches GitHub, apt and SSM through its public IP.
        'NetworkInterfaces': [{'DeviceIndex': 0, 'SubnetId': subnets[0]['SubnetId'], 'Groups': [],
                               'AssociatePublicIpAddress': True, 'DeleteOnTermination': True}],
        'BlockDeviceMappings': [{'DeviceName': image['RootDeviceName'], 'Ebs': {
            'VolumeSize': DISK_GIB, 'VolumeType': 'gp3', 'Encrypted': True, 'DeleteOnTermination': True}}],
        # Hop limit 1: containers of jobs cannot reach the instance role (bootstrap.sh also blocks the runner user).
        'MetadataOptions': {'HttpTokens': 'required', 'HttpPutResponseHopLimit': 1, 'HttpEndpoint': 'enabled'},
        'TagSpecifications': [{'ResourceType': t, 'Tags': tags()} for t in ('instance', 'volume', 'network-interface')],
    }
    return spec, f"{image['Name']} ({ami}), {subnets[0]['SubnetId']} in {subnets[0]['AvailabilityZone']}"


def ensure_iam(state):
    role, attached, _, profile, roles = state
    if not role:
        aws('iam', 'create-role', '--role-name', NAME, '--assume-role-policy-document', json.dumps(TRUST),
            '--description', 'GitHub Actions runner host (infra ADR 0045): SSM only',
            '--tags', *[f'Key={k},Value={v}' for k, v in TAGS.items()])
        print(f'created IAM role {NAME}')
    aws('iam', 'put-role-policy', '--role-name', NAME, '--policy-name', AGENT_POLICY_NAME,
        '--policy-document', json.dumps(AGENT_POLICY))
    print(f'set inline policy {AGENT_POLICY_NAME} (SSM agent, no parameter reads)')
    if SSM_POLICY in attached:
        aws('iam', 'detach-role-policy', '--role-name', NAME, '--policy-arn', SSM_POLICY)
        print('detached AmazonSSMManagedInstanceCore')
    if not profile:
        aws('iam', 'create-instance-profile', '--instance-profile-name', NAME,
            '--tags', *[f'Key={k},Value={v}' for k, v in TAGS.items()])
        print(f'created instance profile {NAME}')
    if NAME not in roles:
        aws('iam', 'add-role-to-instance-profile', '--instance-profile-name', NAME, '--role-name', NAME)
        print(f'added the role to instance profile {NAME}')


def ensure_group(vpc, group):
    if group:
        return group['GroupId']
    group_id = aws('ec2', 'create-security-group', '--group-name', NAME, '--vpc-id', vpc,
                   '--description', 'GitHub Actions runner host, no ingress (infra ADR 0045)',
                   '--tag-specifications', json.dumps([{'ResourceType': 'security-group', 'Tags': tags()}]))['GroupId']
    print(f'created security group {group_id} (no ingress, all egress)')
    return group_id


def launch(spec, group_id):
    spec['NetworkInterfaces'][0]['Groups'] = [group_id]
    # A new instance profile takes a few seconds before EC2 accepts it.
    for attempt in range(1, 19):
        result = subprocess.run(aws_args('ec2', 'run-instances', '--cli-input-json', json.dumps(spec)),
                                text=True, encoding='utf-8', stdout=subprocess.PIPE, stderr=subprocess.PIPE)
        if result.returncode == 0:
            instance_id = json.loads(result.stdout)['Instances'][0]['InstanceId']
            print(f'launched {instance_id}')
            return instance_id
        if 'iamInstanceProfile' not in result.stderr or attempt == 18:
            sys.stderr.write(result.stderr)
            raise subprocess.CalledProcessError(result.returncode, result.args)
        time.sleep(10)


def wait_ssm_online(instance_id, timeout=900):
    print(f'waiting for SSM to report {instance_id} Online ...', flush=True)
    deadline = time.monotonic() + timeout
    while True:
        info = aws('ssm', 'describe-instance-information', '--filters',
                   f'Key=InstanceIds,Values={instance_id}')['InstanceInformationList']
        if info and info[0]['PingStatus'] == 'Online':
            return
        require(time.monotonic() < deadline, f'SSM does not report {instance_id} Online after {timeout}s')
        time.sleep(10)


def run_on_instance(instance_id, commands, comment, secret, timeout=3600):
    """Runs commands as root through AWS-RunShellScript and prints the result with `secret` masked."""
    with tempfile.TemporaryDirectory() as directory:
        path = Path(directory) / 'parameters.json'
        fd = os.open(path, os.O_WRONLY | os.O_CREAT | os.O_EXCL, 0o600)
        with os.fdopen(fd, 'w', encoding='utf-8') as f:
            json.dump({'commands': commands, 'executionTimeout': [str(timeout)]}, f)
        command_id = aws('ssm', 'send-command', '--instance-ids', instance_id, '--document-name', 'AWS-RunShellScript',
                         '--comment', comment, '--timeout-seconds', '600',
                         '--parameters', f'file://{path}')['Command']['CommandId']
    print(f'SSM command {command_id} on {instance_id}: {comment} ...', flush=True)
    deadline = time.monotonic() + timeout + 300
    while True:
        time.sleep(10)
        invocation = aws_try('ssm', 'get-command-invocation', '--command-id', command_id, '--instance-id', instance_id,
                             missing='InvocationDoesNotExist')
        if invocation and invocation['Status'] in COMMAND_DONE:
            break
        require(time.monotonic() < deadline, f'SSM command {command_id} did not finish')
    for stream in ('StandardOutputContent', 'StandardErrorContent'):
        text = (invocation.get(stream) or '').replace(secret, '***').strip()
        if text:
            print(text)
    require(invocation['Status'] == 'Success', f"SSM command {command_id} ended with {invocation['Status']}")


def main(argv=None):
    parser = argparse.ArgumentParser(description=__doc__, formatter_class=argparse.RawDescriptionHelpFormatter)
    parser.add_argument('--dry-run', action='store_true', help='only read and print what would change')
    args = parser.parse_args(argv)
    try:
        check_tools_and_account()
        gh_problem = gh_scope_problem()
        require(not gh_problem or args.dry_run, gh_problem)
        runners, gh_error = org_runners()
        vpc = default_vpc()
        group = security_group(vpc)
        state = iam_state()
        found = instances()
        require(len(found) <= 1, f"more than one instance is tagged Name={NAME}: {', '.join(i['InstanceId'] for i in found)}")
        role, attached, inline, profile, _ = state
        require(set(inline) <= {AGENT_POLICY_NAME} and set(attached) <= {SSM_POLICY},
                f'role {NAME} has other policies ({", ".join(inline + attached)}); remove them first')
        require(not group or not group['IpPermissions'], f"security group {group and group['GroupId']} has ingress rules; remove them first")
        version, sha256 = runner_release()

        print(f'account {ACCOUNT}, region {REGION}')
        print(f"IAM role {NAME}: {'exists' if role else 'create'} (EC2 trust, inline policy {AGENT_POLICY_NAME}"
              f" for the SSM agent without parameter reads{'; detach AmazonSSMManagedInstanceCore' if SSM_POLICY in attached else ''})")
        print(f"instance profile {NAME}: {'exists' if profile else 'create'}")
        print(f"security group {NAME} in {vpc}: {group['GroupId'] if group else 'create'} (no ingress)")
        spec = None
        if found:
            instance = found[0]
            state_name = instance['State']['Name']
            print(f"instance {instance['InstanceId']}: {state_name}{', start it' if state_name in ('stopped', 'stopping') else ''}")
        else:
            spec, where = launch_spec(vpc)
            print(f'instance: launch {INSTANCE_TYPE}, {DISK_GIB} GiB gp3 encrypted, public IP, IMDSv2 hop limit 1, {where}')
        print(f"runners: bootstrap.sh through SSM, {', '.join(RUNNER_NAMES)} with label {LABEL}, actions/runner {version}")
        print(f"gh: {gh_problem or 'may create the registration token'}")
        print('org runners now:')
        if runners is None:
            print(f'  (gh cannot list them: {gh_error})')
        else:
            show_runners(runners)
        if args.dry_run:
            print('Nothing changed (--dry-run).')
            return 0

        ensure_iam(state)
        group_id = ensure_group(vpc, group)
        if found:
            instance_id = found[0]['InstanceId']
            if found[0]['State']['Name'] in ('stopped', 'stopping'):
                aws('ec2', 'wait', 'instance-stopped', '--instance-ids', instance_id)
                aws('ec2', 'start-instances', '--instance-ids', instance_id)
                print(f'started {instance_id}')
        else:
            instance_id = launch(spec, group_id)
        aws('ec2', 'wait', 'instance-running', '--instance-ids', instance_id)
        wait_ssm_online(instance_id)

        token = run(['gh', 'api', '-X', 'POST', f'orgs/{ORG}/actions/runners/registration-token', '--jq', '.token']).stdout.strip()
        require(token, 'gh returned no registration token')
        # Git Bash on Windows checks files out with CRLF; bash on the host needs LF.
        script = (HERE / 'bootstrap.sh').read_bytes().replace(b'\r\n', b'\n')
        inputs = {'RUNNER_TOKEN': token, 'RUNNER_VERSION': version, 'RUNNER_SHA256': sha256,
                  'RUNNER_ORG_URL': f'https://github.com/{ORG}', 'RUNNER_LABEL': LABEL, 'RUNNER_COUNT': str(len(RUNNER_NAMES))}
        run_on_instance(instance_id, [
            'set -eu',
            f'echo {base64.b64encode(gzip.compress(script)).decode()} | base64 -d | gunzip > /root/github-runner-bootstrap.sh',
            ' '.join(f'{k}={shlex.quote(v)}' for k, v in inputs.items()) + ' bash /root/github-runner-bootstrap.sh',
        ], 'github runner bootstrap (infra ADR 0045)', token)

        print('org runners:')
        for _ in range(12):  # the services connect a few seconds after they start
            runners, gh_error = org_runners()
            require(runners is not None, f'gh cannot list the org runners ({gh_error})')
            ours = {r['name']: r for r in runners if r['name'] in RUNNER_NAMES}
            if len(ours) == len(RUNNER_NAMES) and all(r['status'] == 'online' for r in ours.values()):
                break
            time.sleep(5)
        show_runners(runners)
        print(f'Jobs with runs-on: [self-hosted, {LABEL}] now run on {instance_id}.')
    except Refused as error:
        print(f'github-runner up: {error}', file=sys.stderr)
        return 1
    except subprocess.CalledProcessError as error:
        print(f'github-runner up: {error.cmd[0]} failed; see its error above. Run it again after fixing that.', file=sys.stderr)
        return 1
    except (KeyError, IndexError, ValueError, TypeError):
        print('github-runner up: unexpected CLI response; nothing more was changed.', file=sys.stderr)
        return 1
    return 0


if __name__ == '__main__':
    sys.exit(main())
