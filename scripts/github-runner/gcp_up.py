#!/usr/bin/env python3
"""Bring up the org's self-hosted GitHub Actions runner host on GCP (ADR 0045).

In project project-b47cdba3-db5b-42a2-9a6, zone asia-northeast3-a, it reuses or
creates two firewall rules on the default network for the network tag
hopcharlie-github-runner (allow tcp:22 from the IAP range at priority 800, deny
all other ingress at priority 900, so default-allow-ssh/rdp at 65534 do not
reach the VM) and one VM hopcharlie-github-runner: e2-custom-12-49152, Ubuntu 24.04,
200 GB pd-balanced, an ephemeral external IP for egress, no service account (job
code gets no GCP credentials), shielded VM, OS Login and no project SSH keys.
When IAP SSH works, it copies bootstrap.sh and a file with an org registration
token to the VM and runs bootstrap.sh with sudo: tools, nine runners
hopcharlie-gcp-runner-1..9 with the label hopcharlie, and a daily Docker prune.
Last it lists the org's runners.

An existing VM of another machine type is resized only with --resize: the VM
stops, so the run refuses while one of its runners is busy. bootstrap.sh then
adds the missing runners and leaves the configured ones alone.

Safe to run again: existing resources are reused and configured runners are
skipped. --dry-run only reads (gcloud config get-value and describe, gh GET
calls) and prints what would change.

The registration token is never printed and never goes on a command line, into
metadata or into a log: it travels as a file over IAP SSH into a mode 700
directory, and bootstrap.sh deletes the file after reading it. It expires after
an hour.

Needs gcloud logged in with the project above active, and gh with admin:org:
    gh auth refresh -h github.com -s admin:org
On Windows gcloud runs PuTTY (plink, pscp) and answers the host key prompt
itself (gcloud property ssh/putty_force_connect, default true). Run this with
the Windows python, not MSYS2's python3.
Remove everything with gcp_down.py. The AWS host (up.py, down.py) is separate.
"""
import argparse
import json
import os
from pathlib import Path
import shutil
import subprocess
import sys
import tempfile
import time

from up import LABEL, ORG, Refused, gh_scope_problem, org_runners, require, run, runner_release

HERE = Path(__file__).resolve().parent
PROJECT, ZONE, NETWORK = 'project-b47cdba3-db5b-42a2-9a6', 'asia-northeast3-a', 'default'
NAME = 'hopcharlie-github-runner'  # VM name and the network tag the firewall rules target
LABELS = {'project': 'hopcharlie', 'component': 'github-runner', 'managed-by': 'infra-scripts-github-runner'}
# e2 custom 12 vCPU / 48 GB and 200 GB for nine runners; it was e2-standard-4, 80 GB and three
# runners until 2026-11-06. --resize changes only the machine type; grow the disk with
# gcloud compute disks resize (Ubuntu grows the root partition on the next boot).
MACHINE_TYPE, DISK_GB, DISK_TYPE = 'e2-custom-12-49152', 200, 'pd-balanced'
IMAGE_FAMILY, IMAGE_PROJECT = 'ubuntu-2404-lts-amd64', 'ubuntu-os-cloud'
IAP_RANGE = '35.235.240.0/20'
# name: (priority, action, rules, source range); both INGRESS on NETWORK for the tag NAME.
FIREWALLS = {
    f'{NAME}-allow-iap-ssh': (800, 'allow', 'tcp:22', IAP_RANGE),
    f'{NAME}-deny-ingress': (900, 'deny', 'all', '0.0.0.0/0'),
}
RUNNER_PREFIX = 'hopcharlie-gcp-runner'
RUNNER_NAMES = [f'{RUNNER_PREFIX}-{n}' for n in range(1, 10)]
REMOTE_DIR = 'hopcharlie-runner-bootstrap'  # in the OS Login user's home, mode 700


def gcloud_path():
    found = shutil.which('gcloud')
    if not found and os.environ.get('LOCALAPPDATA'):
        candidate = Path(os.environ['LOCALAPPDATA'], 'Google', 'Cloud SDK', 'google-cloud-sdk', 'bin', 'gcloud.cmd')
        found = str(candidate) if candidate.is_file() else None
    return found


GCLOUD = gcloud_path()
ENV = dict(os.environ)
if os.name == 'nt':
    # gcloud.cmd takes the last Python 3 that `where python` lists. From Git Bash that is
    # MSYS2's, which crashes gcloud (LayoutException); use the Python running this script.
    ENV.setdefault('CLOUDSDK_PYTHON', sys.executable)


def gcloud_args(*args, project=PROJECT):
    return [GCLOUD, *args, '--quiet', *([f'--project={project}'] if project else [])]


def gcloud(*args, project=PROJECT):
    out = subprocess.run(gcloud_args(*args, '--format=json', project=project), check=True, text=True,
                         encoding='utf-8', stdout=subprocess.PIPE, stdin=subprocess.DEVNULL, env=ENV).stdout
    return json.loads(out) if out.strip() else {}


def gcloud_try(*args):
    """Like gcloud(), but returns None when the resource does not exist."""
    result = subprocess.run(gcloud_args(*args, '--format=json'), text=True, encoding='utf-8',
                            stdout=subprocess.PIPE, stderr=subprocess.PIPE, stdin=subprocess.DEVNULL, env=ENV)
    if result.returncode == 0:
        return json.loads(result.stdout) if result.stdout.strip() else {}
    if 'was not found' in result.stderr:
        return None
    sys.stderr.write(result.stderr)
    raise subprocess.CalledProcessError(result.returncode, result.args)


def config_value(name):
    # stderr only says which configuration is active, unless gcloud fails.
    result = subprocess.run([GCLOUD, 'config', 'get-value', name], text=True, encoding='utf-8',
                            stdout=subprocess.PIPE, stderr=subprocess.PIPE, stdin=subprocess.DEVNULL, env=ENV)
    if result.returncode != 0:
        sys.stderr.write(result.stderr)
        raise subprocess.CalledProcessError(result.returncode, result.args)
    return result.stdout.strip()


def check_tools_and_project():
    """Returns the active gcloud account."""
    require(GCLOUD, 'gcloud is not on PATH or in %LOCALAPPDATA%\\Google\\Cloud SDK')
    require(shutil.which('gh'), 'gh is not on PATH')
    active = config_value('project')
    # The active project may be set by number (414068132732); compare the project ID.
    got = gcloud('projects', 'describe', active, project=None)['projectId'] if active.isdigit() else active
    require(got == PROJECT, f'the active gcloud project is {active or "unset"}, not {PROJECT}; '
                            f'run: gcloud config set project {PROJECT}')
    return config_value('account')


def firewall_problem(name, rule):
    """Why an existing rule differs from FIREWALLS[name], or ''."""
    priority, action, rules, source = FIREWALLS[name]
    entries = rule.get('allowed' if action == 'allow' else 'denied') or []
    got = ','.join(e['IPProtocol'] + ''.join(f':{p}' for p in e.get('ports', [])) for e in entries)
    same = (rule.get('network', '').endswith(f'/networks/{NETWORK}') and rule.get('direction') == 'INGRESS'
            and rule.get('priority') == priority and rule.get('sourceRanges') == [source]
            and rule.get('targetTags') == [NAME] and got == rules and not rule.get('disabled')
            and not rule.get('allowed' if action == 'deny' else 'denied')
            and not rule.get('sourceTags') and not rule.get('targetServiceAccounts'))
    return '' if same else f'firewall rule {name} differs from {FIREWALLS[name]}; delete or fix it first'


def describe_firewall(name, priority, action, rules, source):
    return f'{NETWORK} network, INGRESS {action} {rules} from {source}, priority {priority}, tag {NAME}'


def instance():
    return gcloud_try('compute', 'instances', 'describe', NAME, f'--zone={ZONE}')


def instance_problem(vm):
    """Why an existing VM cannot be reused, or ''."""
    if vm.get('serviceAccounts'):
        return f'VM {NAME} has a service account; job code could take its credentials. Delete it with gcp_down.py first'
    if NAME not in (vm.get('tags') or {}).get('items', []):
        return f'VM {NAME} lacks the network tag {NAME}, so the firewall rules do not cover it'
    if not all(n.get('network', '').endswith(f'/networks/{NETWORK}') for n in vm.get('networkInterfaces', [])):
        return f'VM {NAME} is not on the {NETWORK} network'
    return ''


def machine_type(vm):
    return vm.get('machineType', '').rsplit('/', 1)[-1]


def resize_instance(runners):
    """Stops the VM and sets MACHINE_TYPE; wait_running() starts it again."""
    busy = sorted(r['name'] for r in runners if r['name'].startswith(RUNNER_PREFIX + '-') and r['busy'])
    require(not busy, f"runners {', '.join(busy)} are running jobs; stopping the VM would fail them. Run again when they are idle")
    gcloud('compute', 'instances', 'stop', NAME, f'--zone={ZONE}')
    print(f'stopped VM {NAME}')
    gcloud('compute', 'instances', 'set-machine-type', NAME, f'--zone={ZONE}', f'--machine-type={MACHINE_TYPE}')
    print(f'set VM {NAME} to {MACHINE_TYPE}')


def show_runners(runners):
    for r in sorted(runners, key=lambda r: r['name']):
        labels = ','.join(label['name'] for label in r['labels'])
        print(f"  {r['name']}: {r['status']}{' busy' if r['busy'] else ''} ({labels})")
    if not runners:
        print('  (none)')


def create_firewall(name):
    priority, action, rules, source = FIREWALLS[name]
    gcloud('compute', 'firewall-rules', 'create', name, f'--network={NETWORK}', '--direction=INGRESS',
           f'--priority={priority}', f'--action={action.upper()}', f'--rules={rules}', f'--source-ranges={source}',
           f'--target-tags={NAME}', '--description=GitHub Actions runner host (infra ADR 0045)')
    print(f'created firewall rule {name}')


def create_instance():
    gcloud('compute', 'instances', 'create', NAME, f'--zone={ZONE}', f'--machine-type={MACHINE_TYPE}',
           f'--image-family={IMAGE_FAMILY}', f'--image-project={IMAGE_PROJECT}',
           f'--boot-disk-size={DISK_GB}GB', f'--boot-disk-type={DISK_TYPE}',
           # With an ephemeral external IP: the default network has no Cloud NAT, and the
           # VM needs egress to GitHub, apt and the AWS CLI download.
           f'--network={NETWORK}',
           '--no-service-account', '--no-scopes',
           '--shielded-secure-boot', '--shielded-vtpm', '--shielded-integrity-monitoring',
           '--metadata=enable-oslogin=TRUE,block-project-ssh-keys=TRUE',
           f'--tags={NAME}', '--labels=' + ','.join(f'{k}={v}' for k, v in LABELS.items()))
    print(f'created VM {NAME}')


def wait_running(timeout=600):
    deadline = time.monotonic() + timeout
    asked = False
    while True:
        status = gcloud('compute', 'instances', 'describe', NAME, f'--zone={ZONE}')['status']
        if status == 'RUNNING':
            return
        if status in ('TERMINATED', 'SUSPENDED') and not asked:
            verb = 'start' if status == 'TERMINATED' else 'resume'
            gcloud('compute', 'instances', verb, NAME, f'--zone={ZONE}')
            print(f'{verb}ed VM {NAME}')
            asked = True
        require(time.monotonic() < deadline, f'VM {NAME} is {status}, not RUNNING, after {timeout}s')
        time.sleep(5)


def ssh_args(command):
    return gcloud_args('compute', 'ssh', NAME, f'--zone={ZONE}', '--tunnel-through-iap', f'--command={command}')


def wait_ssh(timeout=600):
    """Waits until IAP SSH works; the probe also makes REMOTE_DIR (mode 700)."""
    print(f'waiting for IAP SSH to {NAME} ...', flush=True)
    deadline = time.monotonic() + timeout
    # A file, not a pipe: the IAP proxy that plink/ssh starts can outlive gcloud and hold a pipe open.
    with tempfile.TemporaryFile() as out:
        while True:
            out.seek(0)
            out.truncate()
            result = subprocess.run(ssh_args(f'install -d -m 700 {REMOTE_DIR}'), stdout=out, stderr=out,
                                    stdin=subprocess.DEVNULL, env=ENV)
            if result.returncode == 0:
                return
            if time.monotonic() > deadline:
                out.seek(0)
                sys.stderr.write(out.read().decode('utf-8', 'replace')[-3000:])
                raise Refused(f'IAP SSH to {NAME} does not work after {timeout}s; see the last error above')
            time.sleep(10)


def bootstrap(token, version, sha256):
    with tempfile.TemporaryDirectory() as directory:
        script = Path(directory) / 'bootstrap.sh'
        # Git Bash on Windows checks files out with CRLF; bash on the VM needs LF.
        script.write_bytes((HERE / 'bootstrap.sh').read_bytes().replace(b'\r\n', b'\n'))
        token_file = Path(directory) / 'token'
        fd = os.open(token_file, os.O_WRONLY | os.O_CREAT | os.O_EXCL, 0o600)
        with os.fdopen(fd, 'w', encoding='utf-8') as f:
            f.write(token)
        subprocess.run(gcloud_args('compute', 'scp', str(script), str(token_file), f'{NAME}:{REMOTE_DIR}/',
                                   f'--zone={ZONE}', '--tunnel-through-iap'),
                       check=True, stdin=subprocess.DEVNULL, env=ENV)
    inputs = {'RUNNER_TOKEN_FILE': f'$HOME/{REMOTE_DIR}/token', 'RUNNER_VERSION': version, 'RUNNER_SHA256': sha256,
              'RUNNER_ORG_URL': f'https://github.com/{ORG}', 'RUNNER_LABEL': LABEL,
              'RUNNER_COUNT': str(len(RUNNER_NAMES)), 'RUNNER_NAME_PREFIX': RUNNER_PREFIX}
    # Only the token file's path is on the command line (and in sudo's log), never the token.
    command = 'sudo env ' + ' '.join(f'{k}={v}' for k, v in inputs.items()) + f' bash $HOME/{REMOTE_DIR}/bootstrap.sh'
    print(f'running bootstrap.sh on {NAME} over IAP SSH ...', flush=True)
    subprocess.run(ssh_args(command), check=True, stdin=subprocess.DEVNULL, env=ENV)


def main(argv=None):
    parser = argparse.ArgumentParser(description=__doc__, formatter_class=argparse.RawDescriptionHelpFormatter)
    parser.add_argument('--dry-run', action='store_true', help='only read and print what would change')
    parser.add_argument('--resize', action='store_true',
                        help=f'stop an existing VM of another machine type and set {MACHINE_TYPE}')
    args = parser.parse_args(argv)
    try:
        account = check_tools_and_project()
        gh_problem = gh_scope_problem()
        require(not gh_problem or args.dry_run, gh_problem)
        runners, gh_error = org_runners()
        rules = {name: gcloud_try('compute', 'firewall-rules', 'describe', name) for name in FIREWALLS}
        for name, rule in rules.items():
            problem = rule and firewall_problem(name, rule)
            require(not problem, problem)
        vm = instance()
        problem = vm and instance_problem(vm)
        require(not problem, problem)
        version, sha256 = runner_release()

        print(f'project {PROJECT} (gcloud account {account}), zone {ZONE}')
        for name, rule in rules.items():
            print(f"firewall rule {name}: {'exists' if rule else 'create'} ({describe_firewall(name, *FIREWALLS[name])})")
        resize = bool(vm) and machine_type(vm) != MACHINE_TYPE
        if vm:
            print(f"VM {NAME}: {vm['status']}, {machine_type(vm)}"
                  f"{', start it' if vm['status'] in ('TERMINATED', 'SUSPENDED') else ''}"
                  f"{f', stop and resize to {MACHINE_TYPE} (needs --resize)' if resize else ''}")
        else:
            image = gcloud('compute', 'images', 'describe-from-family', IMAGE_FAMILY, project=IMAGE_PROJECT)['name']
            print(f'VM {NAME}: create {MACHINE_TYPE}, {image}, {DISK_GB} GB {DISK_TYPE}, {NETWORK} network with an'
                  ' ephemeral external IP, no service account, shielded VM, OS Login, project SSH keys blocked')
        print(f"runners: bootstrap.sh over IAP SSH, {', '.join(RUNNER_NAMES)} with label {LABEL}, actions/runner {version}")
        print(f"gh: {gh_problem or 'may create the registration token'}")
        print('org runners now:')
        if runners is None:
            print(f'  (gh cannot list them: {gh_error})')
        else:
            show_runners(runners)
        if args.dry_run:
            print('Nothing changed (--dry-run).')
            return 0
        require(not resize or args.resize, f'VM {NAME} is {machine_type(vm) if vm else ""}, not {MACHINE_TYPE}; '
                                           'run again with --resize to stop and resize it')

        # Firewall first, so default-allow-ssh/rdp never reach the VM.
        for name, rule in rules.items():
            if rule is None:
                create_firewall(name)
        if vm is None:
            create_instance()
        elif resize:
            require(runners is not None, f'gh cannot list the org runners ({gh_error}), so busy runners are unknown')
            resize_instance(runners)
        wait_running()
        wait_ssh()

        token = run(['gh', 'api', '-X', 'POST', f'orgs/{ORG}/actions/runners/registration-token', '--jq', '.token']).stdout.strip()
        require(token, 'gh returned no registration token')
        bootstrap(token, version, sha256)

        print('org runners:')
        for _ in range(12):  # the services connect a few seconds after they start
            runners, gh_error = org_runners()
            require(runners is not None, f'gh cannot list the org runners ({gh_error})')
            ours = {r['name']: r for r in runners if r['name'] in RUNNER_NAMES}
            if len(ours) == len(RUNNER_NAMES) and all(r['status'] == 'online' for r in ours.values()):
                break
            time.sleep(5)
        show_runners(runners)
        print(f'Jobs with runs-on: [self-hosted, {LABEL}] now also run on the GCP VM {NAME}.')
    except Refused as error:
        print(f'github-runner gcp up: {error}', file=sys.stderr)
        return 1
    except subprocess.CalledProcessError as error:
        print(f'github-runner gcp up: {Path(error.cmd[0]).name} failed; see its error above. Run it again after fixing that.',
              file=sys.stderr)
        return 1
    except (KeyError, IndexError, ValueError, TypeError):
        print('github-runner gcp up: unexpected CLI response; nothing more was changed.', file=sys.stderr)
        return 1
    return 0


if __name__ == '__main__':
    sys.exit(main())
