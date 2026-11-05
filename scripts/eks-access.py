#!/usr/bin/env python3
"""Foreground local access to private EKS and the management Argo CD service."""
import argparse
import importlib.util
import json
import os
from pathlib import Path
import re
import shutil
import socket
import subprocess
import sys
import tempfile
from urllib.parse import urlsplit

ROOT = Path(__file__).resolve().parents[1]
spec = importlib.util.spec_from_file_location('project_config', ROOT / 'scripts/project-config.py')
config = importlib.util.module_from_spec(spec)
spec.loader.exec_module(config)


class AccessError(ValueError):
    pass


def require(ok, message):
    if not ok:
        raise AccessError(message)


def port(value):
    require(bool(re.fullmatch(r'[0-9]+', str(value))) and 1024 <= int(value) <= 65535,
            'Port must be an integer from 1024 to 65535.')
    return int(value)


def free_port(number):
    try:
        with socket.socket() as listener:
            listener.bind(('127.0.0.1', number))
    except OSError as error:
        raise AccessError(f'127.0.0.1:{number} is unavailable. Choose another port; no process was stopped.') from error


def run(args):
    # Keep CLI errors visible without logging kubeconfig, CA data or token output.
    return subprocess.run(args, check=True, text=True, stdout=subprocess.PIPE).stdout


def read_kube(path):
    return json.loads(run(['kubectl', '--kubeconfig', str(path), 'config', 'view', '--raw', '-o', 'json']))


def token_exec(profile, region, name):
    return {'apiVersion': 'client.authentication.k8s.io/v1beta1', 'command': 'aws',
            'interactiveMode': 'IfAvailable' if profile else 'Never', 'provideClusterInfo': False,
            'args': (['--profile', profile] if profile else []) + ['--region', region, 'eks', 'get-token',
                     '--cluster-name', name, '--output', 'json'],
            'env': [{'name': 'AWS_PROFILE', 'value': profile}] if profile else []}


def tunnel(p, aid, kind, number, profile, path):
    name = p[f'{kind}_cluster_name']
    region = p['aws_region']
    arn = f'arn:aws:eks:{region}:{aid}:cluster/{name}'

    def aws(*args):
        return ['aws', *(['--profile', profile] if profile else []), '--region', region, *args]

    def query(*args):
        return json.loads(run(aws(*args, '--output', 'json')))

    require(query('sts', 'get-caller-identity')['Account'] == aid,
            'AWS account differs from config/account.local.json.')
    cluster = query('eks', 'describe-cluster', '--name', name)['cluster']
    endpoint = urlsplit(cluster['endpoint'])
    host = endpoint.hostname
    require(cluster['arn'] == arn and endpoint.scheme == 'https' and host
            and endpoint.netloc.lower() == host and endpoint.path in ('', '/')
            and not endpoint.query and not endpoint.fragment,
            'Unexpected EKS identity or endpoint.')
    ca = cluster['certificateAuthority']['data']
    require(isinstance(ca, str) and ca, 'EKS CA is missing.')
    vpc = cluster['resourcesVpcConfig']['vpcId']
    filters = [f"Name=tag:Project,Values={p['project']}",
               f"Name=tag:Environment,Values={p['environment']}",
               'Name=tag:Component,Values=access', 'Name=instance-state-name,Values=running',
               f'Name=vpc-id,Values={vpc}']
    reservations = query('ec2', 'describe-instances', '--filters', *filters)['Reservations']
    bridges = [i for r in reservations for i in r['Instances']]
    require(len(bridges) == 1 and bridges[0].get('VpcId') == vpc
            and bridges[0].get('State', {}).get('Name') == 'running',
            'Expected exactly one running access bridge in the EKS VPC.')
    bridge = bridges[0]['InstanceId']
    info = query('ssm', 'describe-instance-information', '--filters',
                 f'Key=InstanceIds,Values={bridge}')['InstanceInformationList']
    require(len(info) == 1 and info[0].get('InstanceId') == bridge
            and info[0].get('PingStatus') == 'Online', 'SSM bridge is not Online.')

    path.parent.mkdir(parents=True, exist_ok=True, mode=0o700)
    path.parent.chmod(0o700)
    with tempfile.TemporaryDirectory(prefix='.kube-', dir=path.parent) as directory:
        temporary = Path(directory) / 'config'
        # The status line can use the Windows locale; only the generated file is needed.
        subprocess.run(aws('eks', 'update-kubeconfig', '--name', name, '--alias', name,
                           '--kubeconfig', str(temporary)), check=True, stdout=subprocess.DEVNULL)
        generated = read_kube(temporary)
        entries = generated.get('clusters', [])
        require(len(entries) == 1 and entries[0]['name'] == arn
                and entries[0]['cluster'].get('server') == cluster['endpoint']
                and entries[0]['cluster'].get('certificate-authority-data') == ca,
                'Generated kubeconfig does not match the described EKS cluster.')
        # A single deterministic context prevents accidental global kubeconfig merging.
        document = {
            'apiVersion': 'v1', 'kind': 'Config', 'current-context': name,
            'clusters': [{'name': arn, 'cluster': {
                'server': f'https://127.0.0.1:{number}', 'tls-server-name': host,
                'certificate-authority-data': ca}}],
            'contexts': [{'name': name, 'context': {'cluster': arn, 'user': arn}}],
            'users': [{'name': arn, 'user': {'exec': token_exec(profile, region, name)}}],
            'extensions': [{'name': 'charlie-access', 'extension': {'profile': profile, 'hostname': host}}],
        }
        temporary.write_text(json.dumps(document, indent=2) + '\n')
        temporary.chmod(0o600)
        os.replace(temporary, path)
    print(f'Kubeconfig: {path}\nSSM tunnel: {name} → 127.0.0.1:{number}; keep this terminal open (Ctrl+C to stop).', flush=True)
    parameters = json.dumps({'host': [host], 'portNumber': ['443'], 'localPortNumber': [str(number)]})
    command = aws('ssm', 'start-session', '--target', bridge, '--document-name',
                  'AWS-StartPortForwardingSessionToRemoteHost', '--parameters', parameters)
    if os.name == 'nt':
        # subprocess quotes JSON arguments correctly for Windows executables.
        raise SystemExit(subprocess.call(command))
    os.execvpe(command[0], command, os.environ.copy())


def argo(p, aid, number, path):
    require(path.is_file(), 'Run make aws-tunnel first to generate the management kubeconfig.')
    document = read_kube(path)
    name = p['management_cluster_name']
    arn = f"arn:aws:eks:{p['aws_region']}:{aid}:cluster/{name}"
    metadata = next(x['extension'] for x in document['extensions'] if x['name'] == 'charlie-access')
    profile, host = metadata['profile'], metadata['hostname']
    require(isinstance(profile, str) and profile and isinstance(host, str) and host,
            'Invalid access metadata. Run make aws-tunnel again.')
    require(not os.environ.get('AWS_PROFILE') or os.environ['AWS_PROFILE'] == profile,
            'AWS_PROFILE differs from the saved profile. Re-run make aws-tunnel with the desired AWS_PROFILE.')
    require(document['current-context'] == name
            and document['contexts'] == [{'name': name, 'context': {'cluster': arn, 'user': arn}}]
            and document['users'] == [{'name': arn, 'user': {'exec': token_exec(profile, p['aws_region'], name)}}]
            and len(document['clusters']) == 1 and document['clusters'][0]['name'] == arn,
            'Unexpected management kubeconfig identity/authentication. Run make aws-tunnel again.')
    cluster = document['clusters'][0]['cluster']
    endpoint = urlsplit(cluster['server'])
    require(endpoint.scheme == 'https' and endpoint.hostname == '127.0.0.1'
            and endpoint.netloc == f'127.0.0.1:{endpoint.port}' and not endpoint.path
            and not endpoint.query and not endpoint.fragment
            and cluster.get('tls-server-name') == host and cluster.get('certificate-authority-data')
            and not cluster.get('insecure-skip-tls-verify'),
            'Expected a loopback tunnel with EKS CA and TLS server name. Run make aws-tunnel again.')
    port(endpoint.port)
    base = ['kubectl', '--kubeconfig', str(path), '--context', name]
    # Preserve real authentication/RBAC errors; do not relabel every error as a tunnel failure.
    run([*base, '--request-timeout=5s', 'get', '--raw=/readyz'])
    print(f'Argo CD: https://localhost:{number} (Ctrl+C to stop). Keep make aws-tunnel running.', flush=True)
    command = [*base, '-n', 'argocd', 'port-forward', '--address', '127.0.0.1',
               'svc/argocd-server', f'{number}:443']
    if os.name == 'nt':
        raise SystemExit(subprocess.call(command))
    os.execvpe(command[0], command, os.environ.copy())


def main():
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument('command', choices=('tunnel', 'argo-forward'))
    args = parser.parse_args()
    try:
        kind = os.environ.get('CLUSTER') or 'management'
        require(kind in ('management', 'workload'), 'CLUSTER must be management or workload.')
        require(args.command == 'tunnel' or kind == 'management', 'Argo CD forwarding supports management only.')
        number = port((os.environ.get('EKS_LOCAL_PORT') or ('10443' if kind == 'management' else '11443'))
                      if args.command == 'tunnel' else os.environ.get('ARGO_LOCAL_PORT') or '8080')
        for tool in ('aws', 'kubectl', 'session-manager-plugin') if args.command == 'tunnel' else ('aws', 'kubectl'):
            require(shutil.which(tool), f'Required command not found: {tool}')
        p, a = config.project(), config.account(False)
        for variable in ('AWS_REGION', 'AWS_DEFAULT_REGION'):
            if os.environ.get(variable) and os.environ[variable] != p['aws_region']:
                print(f"Ignoring {variable}; using project region {p['aws_region']}.", file=sys.stderr)
        free_port(number)
        path = ROOT / '.generated/access' / f'{kind}.kubeconfig'
        if args.command == 'tunnel':
            tunnel(p, a['aws_account_id'], kind, number, os.environ.get('AWS_PROFILE') or 'charlie-admin', path)
        else:
            argo(p, a['aws_account_id'], number, path)
    except (AccessError, config.ConfigError) as error:
        print(str(error), file=sys.stderr)
        return 1
    except subprocess.CalledProcessError as error:
        print('CLI command failed; see its error above. No automatic retry.', file=sys.stderr)
        return error.returncode if error.returncode > 0 else 1
    except (OSError, ValueError, KeyError, TypeError, StopIteration, AttributeError):
        print('Access configuration/CLI response rejected. Check configuration and re-run make aws-tunnel.', file=sys.stderr)
        return 1
    return 0


if __name__ == '__main__':
    sys.exit(main())
