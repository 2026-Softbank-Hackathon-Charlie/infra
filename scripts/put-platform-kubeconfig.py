#!/usr/bin/env python3
"""Give the HopCharlie platform API read access to the workload EKS cluster
(its environment eks) and store the kubeconfig in SSM /hopcharlie/kube/eks.

Without --apply it changes nothing: it checks the AWS account, the workload
tunnel and the parameter, and shows the RBAC diff. With --apply it applies
clusters/aws-dev-workload/hopcharlie-platform-access.yaml, builds a kubeconfig
(context eks-hopcharlie, the private EKS endpoint, the ServiceAccount token),
checks what that token may do through the tunnel and stores the kubeconfig as
a SecureString. The token is never printed or put on a command line, and the
temporary kubeconfig is deleted on exit. See docs/runbooks/app-routing.md.
"""
import argparse
import base64
import json
import os
from pathlib import Path
import shutil
import subprocess
import sys
import tempfile
import time
from urllib.parse import urlsplit

ROOT = Path(__file__).resolve().parents[1]
MANIFEST = ROOT / 'clusters/aws-dev-workload/hopcharlie-platform-access.yaml'
PARAMETER = '/hopcharlie/kube/eks'
CONTEXT = 'eks-hopcharlie'  # terraform/platform/aws/host/envs.yaml, environment eks
NAMESPACE, ACCOUNT, TOKEN_SECRET = 'charlie-system', 'hopcharlie-platform', 'hopcharlie-platform-token'
FIELD_MANAGER = 'put-platform-kubeconfig'
# What the platform's token must and must not be allowed to do (ADR 0036).
ALLOWED = [['get', 'services', '-n', 'default'], ['list', 'services', '-A'], ['list', 'nodes'],
           ['list', 'pods', '-A'], ['list', 'networkpolicies.networking.k8s.io', '-A'],
           ['list', 'clusterrolebindings.rbac.authorization.k8s.io'],
           ['list', 'clusters.postgresql.cnpg.io', '-A']]
DENIED = [['get', 'secrets', '-n', NAMESPACE], ['list', 'secrets', '-A'], ['list', 'configmaps', '-A'],
          ['patch', 'services', '-n', 'default'], ['create', 'deployments', '-n', 'default'],
          ['patch', 'clusters.postgresql.cnpg.io', '-A'], ['delete', 'namespaces']]


class Refused(Exception):
    pass


def require(ok, message):
    if not ok:
        raise Refused(message)


def run(args, check=True):
    # stdout is captured and never echoed; CLI errors stay visible on stderr.
    return subprocess.run(args, check=check, text=True, encoding='utf-8', stdout=subprocess.PIPE)


def project():
    return json.loads((ROOT / 'config/project.json').read_text(encoding='utf-8'))


def main(argv=None):
    parser = argparse.ArgumentParser(description=__doc__, formatter_class=argparse.RawDescriptionHelpFormatter)
    parser.add_argument('--apply', action='store_true', help='apply the RBAC and store the kubeconfig')
    parser.add_argument('--replace', action='store_true', help=f'overwrite an existing {PARAMETER}')
    parser.add_argument('--kubeconfig', type=Path, default=ROOT / '.generated/access/workload.kubeconfig',
                        help='kubeconfig of make aws-tunnel CLUSTER=workload')
    args = parser.parse_args(argv)
    p = project()
    profile = os.environ.get('AWS_PROFILE') or 'hopcharlie'
    account = os.environ.get('HOPCHARLIE_AWS_ACCOUNT') or '525091405886'
    region, cluster = p['aws_region'], p['workload_cluster_name']
    try:
        for tool in ('aws', 'kubectl'):
            require(shutil.which(tool), f'{tool} is not on PATH')
        require(args.kubeconfig.is_file(), f'{args.kubeconfig} is missing; run make aws-tunnel CLUSTER=workload first')

        def aws(*a):
            return ['aws', '--profile', profile, '--region', region, *a]

        def operator(*a):
            return ['kubectl', '--kubeconfig', str(args.kubeconfig), '--context', cluster, *a]

        got = run(aws('sts', 'get-caller-identity', '--query', 'Account', '--output', 'text')).stdout.strip()
        require(got == account, f"profile '{profile}' is account {got}, not the hackathon account {account}")
        described = json.loads(run(aws('eks', 'describe-cluster', '--name', cluster, '--output', 'json')).stdout)['cluster']
        endpoint, ca = described['endpoint'], described['certificateAuthority']['data']
        host = urlsplit(endpoint).hostname
        require(urlsplit(endpoint).scheme == 'https' and host and ca, 'unexpected EKS endpoint or CA')

        # The tunnel kubeconfig must point at this cluster: loopback server, real TLS name.
        tunnel = json.loads(run(operator('config', 'view', '--raw', '--minify', '-o', 'json')).stdout)
        local = tunnel['clusters'][0]['cluster']
        require(urlsplit(local['server']).hostname == '127.0.0.1'
                and (local.get('tls-server-name') or '').lower() == host.lower(),
                f'{args.kubeconfig} is not the tunnel to {cluster}; run make aws-tunnel CLUSTER=workload again')
        run(operator('--request-timeout=5s', 'get', '--raw=/readyz'))
        # Every kubeconfig the API merges (names and who wrote them, no values): the first
        # file wins on a context name clash, and an existing eks may point elsewhere.
        kube = json.loads(run(aws('ssm', 'describe-parameters', '--parameter-filters',
                                  f'Key=Path,Values={PARAMETER.rsplit("/", 1)[0]}', '--output', 'json')).stdout)['Parameters']
        exists = any(k['Name'] == PARAMETER for k in kube)
        print(f'account {account}, cluster {cluster}, endpoint {endpoint}')
        for k in kube:
            print(f"  {k['Name']}: modified {k.get('LastModifiedDate')} by {k.get('LastModifiedUser')}")
        print(f'{PARAMETER}: {"exists" if exists else "missing"}')
        print(f'RBAC diff of {MANIFEST.relative_to(ROOT).as_posix()} (empty: nothing to change):', flush=True)
        diff = subprocess.run(operator('diff', '--server-side', f'--field-manager={FIELD_MANAGER}', '-f', str(MANIFEST)))
        require(diff.returncode in (0, 1), 'kubectl diff failed')
        if not args.apply:
            print('Nothing changed. Re-run with --apply to apply the RBAC and store the kubeconfig.')
            return 0
        require(not exists or args.replace, f'{PARAMETER} exists; add --replace to overwrite it')

        run(operator('apply', '--server-side', f'--field-manager={FIELD_MANAGER}', '-f', str(MANIFEST)))
        token = ''
        for _ in range(30):  # the token controller fills the Secret asynchronously
            data = run(operator('-n', NAMESPACE, 'get', 'secret', TOKEN_SECRET, '-o', 'jsonpath={.data.token}'),
                       check=False).stdout.strip()
            if data:
                token = base64.b64decode(data).decode()
                break
            time.sleep(1)
        require(token, f'{NAMESPACE}/{TOKEN_SECRET} has no token yet')
        kubeconfig = {
            'apiVersion': 'v1', 'kind': 'Config', 'current-context': CONTEXT,
            'clusters': [{'name': CONTEXT, 'cluster': {'server': endpoint, 'certificate-authority-data': ca}}],
            'users': [{'name': f'{ACCOUNT}@{CONTEXT}', 'user': {'token': token}}],
            'contexts': [{'name': CONTEXT, 'context': {'cluster': CONTEXT, 'user': f'{ACCOUNT}@{CONTEXT}'}}],
        }
        work = ROOT / '.generated/platform-kube'
        work.mkdir(parents=True, exist_ok=True, mode=0o700)
        with tempfile.TemporaryDirectory(dir=work) as directory:
            path = Path(directory) / 'eks.kubeconfig'
            fd = os.open(path, os.O_WRONLY | os.O_CREAT | os.O_EXCL, 0o600)
            with os.fdopen(fd, 'w', encoding='utf-8') as f:
                json.dump(kubeconfig, f, indent=2)

            # Same token and CA, through the tunnel: the private endpoint is not reachable from here.
            def platform(*a):
                return ['kubectl', '--kubeconfig', str(path), '--server', local['server'],
                        '--tls-server-name', host, *a]

            who = run(platform('auth', 'whoami', '-o', 'jsonpath={.status.userInfo.username}')).stdout.strip()
            require(who == f'system:serviceaccount:{NAMESPACE}:{ACCOUNT}', f'the token authenticates as {who}')
            for rule, want in [(r, 'yes') for r in ALLOWED] + [(r, 'no') for r in DENIED]:
                answer = run(platform('auth', 'can-i', *rule), check=False).stdout.strip()
                print(f'  can-i {" ".join(rule)}: {answer}')
                # "no" may carry a reason ("no - ..."); an error leaves it empty.
                require((answer.split() or [''])[0] == want, f'expected {want} for {" ".join(rule)}')
            command = aws('ssm', 'put-parameter', '--name', PARAMETER, '--type', 'SecureString',
                          '--tier', 'Intelligent-Tiering', '--value', f'file://{path}',
                          '--query', 'Version', '--output', 'text')
            version = run(command + (['--overwrite'] if exists else [])).stdout.strip()
        print(f'stored {PARAMETER} version {version} (context {CONTEXT}, server {endpoint})')
        print('Next: restart hopcharlie-api on the management cluster so its init container reads it.')
    except Refused as error:
        print(f'put-platform-kubeconfig: {error}', file=sys.stderr)
        return 1
    except subprocess.CalledProcessError as error:
        print(f'put-platform-kubeconfig: {error.cmd[0]} failed; see its error above. Nothing was retried.', file=sys.stderr)
        return 1
    except (KeyError, IndexError, ValueError, TypeError):
        print('put-platform-kubeconfig: unexpected CLI response; nothing more was changed.', file=sys.stderr)
        return 1
    return 0


if __name__ == '__main__':
    sys.exit(main())
