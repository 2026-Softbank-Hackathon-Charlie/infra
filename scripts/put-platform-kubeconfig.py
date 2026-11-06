#!/usr/bin/env python3
"""Give the HopCharlie platform API read access to a workload cluster and store
the kubeconfig in SSM: --env eks (default) is the workload EKS cluster and
/hopcharlie/kube/eks (ADR 0036), --env gke the GKE cluster and
/hopcharlie/kube/gke (ADR 0044).

Without --apply it changes nothing: it checks the AWS account, the workload
tunnel and the parameter, and shows the RBAC diff. With --apply it applies
the environment's hopcharlie-platform-access.yaml, builds a kubeconfig
(context <env>-hopcharlie, the cluster endpoint, the ServiceAccount token),
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

import yaml

ROOT = Path(__file__).resolve().parents[1]
# Per environment: the manifest, the SSM parameter, the context the API looks
# up (terraform/platform/aws/host/envs.yaml), and the operator's tunnel.
ENVS = {
    'eks': {'manifest': 'clusters/aws-dev-workload/hopcharlie-platform-access.yaml',
            'kubeconfig': '.generated/access/workload.kubeconfig', 'tunnel': 'make aws-tunnel CLUSTER=workload'},
    'gke': {'manifest': 'clusters/gcp-dev-workload/hopcharlie-platform-access.yaml',
            'kubeconfig': '.generated/access/gke.kubeconfig', 'tunnel': 'the GKE access tunnel (ADR 0021)',
            'context': 'charlie-dev-gcp-workload'},
}
MANIFEST = ROOT / ENVS['eks']['manifest']
PARAMETER = '/hopcharlie/kube/eks'
CONTEXT = 'eks-hopcharlie'
NAMESPACE, ACCOUNT, TOKEN_SECRET = 'charlie-system', 'hopcharlie-platform', 'hopcharlie-platform-token'
FIELD_MANAGER = 'put-platform-kubeconfig'
# What the platform's token must and must not be allowed to do (ADR 0036).
# CNPG Cluster get and patch are for migrations (ADR 0063); RBAC allows patch
# everywhere and the manifest's ValidatingAdmissionPolicy, which can-i does
# not see, keeps it to spec.replica in labelled namespaces or Clusters with
# a db-migration-access.py session (verb migrate, never standing).
ALLOWED = [['get', 'services', '-n', 'default'], ['list', 'services', '-A'], ['list', 'nodes'],
           ['list', 'pods', '-A'], ['list', 'networkpolicies.networking.k8s.io', '-A'],
           ['list', 'clusterrolebindings.rbac.authorization.k8s.io'],
           ['list', 'clusters.postgresql.cnpg.io', '-A'], ['list', 'objectstores.barmancloud.cnpg.io', '-A'],
           ['get', 'clusters.postgresql.cnpg.io', '-A'], ['patch', 'clusters.postgresql.cnpg.io', '-A']]
DENIED = [['get', 'secrets', '-n', NAMESPACE], ['list', 'secrets', '-A'], ['list', 'configmaps', '-A'],
          ['patch', 'services', '-n', 'default'], ['create', 'deployments', '-n', 'default'],
          ['create', 'clusters.postgresql.cnpg.io', '-A'], ['delete', 'clusters.postgresql.cnpg.io', '-A'],
          ['patch', 'clusters.postgresql.cnpg.io', '-A', '--subresource=status'],
          ['patch', 'objectstores.barmancloud.cnpg.io', '-A'], ['delete', 'namespaces'],
          ['migrate', 'clusters.postgresql.cnpg.io', '-A']]


class Refused(Exception):
    pass


def require(ok, message):
    if not ok:
        raise Refused(message)


def run(args, check=True, stdin=None):
    # stdout is captured and never echoed; CLI errors stay visible on stderr.
    return subprocess.run(args, input=stdin, check=check, text=True, encoding='utf-8', stdout=subprocess.PIPE)


def project():
    return json.loads((ROOT / 'config/project.json').read_text(encoding='utf-8'))


def main(argv=None):
    parser = argparse.ArgumentParser(description=__doc__, formatter_class=argparse.RawDescriptionHelpFormatter)
    parser.add_argument('--env', choices=sorted(ENVS), default='eks', help='platform environment (default eks)')
    parser.add_argument('--apply', action='store_true', help='apply the RBAC and store the kubeconfig')
    parser.add_argument('--replace', action='store_true', help='overwrite an existing /hopcharlie/kube/<env>')
    parser.add_argument('--kubeconfig', type=Path, help='operator kubeconfig of the tunnel to the cluster')
    args = parser.parse_args(argv)
    env = ENVS[args.env]
    manifest, parameter, context = MANIFEST, PARAMETER, CONTEXT
    if args.env != 'eks':
        manifest, parameter, context = ROOT / env['manifest'], f'/hopcharlie/kube/{args.env}', f'{args.env}-hopcharlie'
    args.kubeconfig = args.kubeconfig or ROOT / env['kubeconfig']
    p = project()
    profile = os.environ.get('AWS_PROFILE') or 'hopcharlie'
    account = os.environ.get('HOPCHARLIE_AWS_ACCOUNT') or '525091405886'
    region, cluster = p['aws_region'], p['workload_cluster_name']
    try:
        for tool in ('aws', 'kubectl'):
            require(shutil.which(tool), f'{tool} is not on PATH')
        require(args.kubeconfig.is_file(), f"{args.kubeconfig} is missing; open {env['tunnel']} first")
        cluster = env.get('context', cluster)

        def aws(*a):
            return ['aws', '--profile', profile, '--region', region, *a]

        def operator(*a):
            return ['kubectl', '--kubeconfig', str(args.kubeconfig), '--context', cluster, *a]

        got = run(aws('sts', 'get-caller-identity', '--query', 'Account', '--output', 'text')).stdout.strip()
        require(got == account, f"profile '{profile}' is account {got}, not the hackathon account {account}")
        tunnel = json.loads(run(operator('config', 'view', '--raw', '--minify', '-o', 'json')).stdout)
        local = tunnel['clusters'][0]['cluster']
        if args.env == 'eks':
            described = json.loads(run(aws('eks', 'describe-cluster', '--name', cluster, '--output', 'json')).stdout)['cluster']
            endpoint, ca = described['endpoint'], described['certificateAuthority']['data']
        else:
            # The GKE tunnel keeps the real public endpoint as its TLS name and the cluster CA.
            endpoint, ca = f"https://{local.get('tls-server-name', '')}", local.get('certificate-authority-data')
        host = urlsplit(endpoint).hostname
        require(urlsplit(endpoint).scheme == 'https' and host and ca, f'unexpected {args.env} endpoint or CA')

        # The tunnel kubeconfig must point at this cluster: loopback server, real TLS name.
        require(urlsplit(local['server']).hostname == '127.0.0.1'
                and (local.get('tls-server-name') or '').lower() == host.lower(),
                f"{args.kubeconfig} is not the tunnel to {cluster}; open {env['tunnel']} again")
        run(operator('--request-timeout=5s', 'get', '--raw=/readyz'))
        # Every kubeconfig the API merges (names and who wrote them, no values): the first
        # file wins on a context name clash, and an existing eks may point elsewhere.
        kube = json.loads(run(aws('ssm', 'describe-parameters', '--parameter-filters',
                                  f'Key=Path,Values={parameter.rsplit("/", 1)[0]}', '--output', 'json')).stdout)['Parameters']
        exists = any(k['Name'] == parameter for k in kube)
        print(f'account {account}, cluster {cluster}, endpoint {endpoint}')
        for k in kube:
            print(f"  {k['Name']}: modified {k.get('LastModifiedDate')} by {k.get('LastModifiedUser')}")
        print(f'{parameter}: {"exists" if exists else "missing"}')

        # A server-side diff of an object in a missing namespace fails with NotFound, so
        # before the namespace exists only the cluster-scoped objects can be diffed.
        docs = [d for d in yaml.safe_load_all(manifest.read_text(encoding='utf-8')) if d]
        namespace = [d for d in docs if d['kind'] == 'Namespace']
        namespaced = [d for d in docs if d['metadata'].get('namespace')]
        cluster_scoped = [d for d in docs if d not in namespace and d not in namespaced]

        def diff(objects, what):
            print(f'diff of {what} (empty: nothing to change):', flush=True)
            result = subprocess.run(operator('diff', '--server-side', f'--field-manager={FIELD_MANAGER}', '-f', '-'),
                                    input=yaml.safe_dump_all(objects), text=True, encoding='utf-8')
            require(result.returncode in (0, 1), 'kubectl diff failed')

        def apply(objects):
            run(operator('apply', '--server-side', f'--field-manager={FIELD_MANAGER}', '-f', '-'),
                stdin=yaml.safe_dump_all(objects))

        has_namespace = bool(run(operator('get', 'namespace', NAMESPACE, '-o', 'name', '--ignore-not-found')).stdout.strip())
        if has_namespace:
            diff(docs, manifest.relative_to(ROOT).as_posix())
        else:
            print(f'namespace {NAMESPACE} is missing: --apply creates it and the ServiceAccount and token Secret in it')
            diff(cluster_scoped, 'the cluster-scoped objects')
        if not args.apply:
            print('Nothing changed. Re-run with --apply to apply the RBAC and store the kubeconfig.')
            return 0
        require(not exists or args.replace, f'{parameter} exists; add --replace to overwrite it')

        if not has_namespace:
            apply(namespace)
            diff(namespaced, f'the objects in {NAMESPACE}')
        apply(docs)
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
            'apiVersion': 'v1', 'kind': 'Config', 'current-context': context,
            'clusters': [{'name': context, 'cluster': {'server': endpoint, 'certificate-authority-data': ca}}],
            'users': [{'name': f'{ACCOUNT}@{context}', 'user': {'token': token}}],
            'contexts': [{'name': context, 'context': {'cluster': context, 'user': f'{ACCOUNT}@{context}'}}],
        }
        work = ROOT / '.generated/platform-kube'
        work.mkdir(parents=True, exist_ok=True, mode=0o700)
        with tempfile.TemporaryDirectory(dir=work) as directory:
            path = Path(directory) / f'{args.env}.kubeconfig'
            fd = os.open(path, os.O_WRONLY | os.O_CREAT | os.O_EXCL, 0o600)
            with os.fdopen(fd, 'w', encoding='utf-8') as f:
                json.dump(kubeconfig, f, indent=2)

            # Same token and CA, through the tunnel: the endpoint is not reachable from here.
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
            command = aws('ssm', 'put-parameter', '--name', parameter, '--type', 'SecureString',
                          '--tier', 'Intelligent-Tiering', '--value', f'file://{path}',
                          '--query', 'Version', '--output', 'text')
            version = run(command + (['--overwrite'] if exists else [])).stdout.strip()
        print(f'stored {parameter} version {version} (context {context}, server {endpoint})')
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
