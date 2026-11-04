#!/usr/bin/env python3
"""Start the app ApplicationSet (ADR 0023, 0036) in the management Argo CD
without syncing charlie-root.

  preview          changes nothing: renders helm/gitops with charlie-root's own
                   values, dry-runs the two objects below on the server and
                   diffs what their sync would create
  add-repository   creates the gitops repository Secret from a read-only token
                   file; the token goes to kubectl on stdin and is never printed
  create           creates AppProject charlie-hopcharlie-apps and Application
                   charlie-management-hopcharlie-apps, without automated sync
  sync             asks for one sync of that Application and reports the
                   ApplicationSet and the app Applications it created

Every command uses the management kubeconfig of make aws-tunnel and names only
these objects. See docs/runbooks/app-routing.md.
"""
import argparse
import base64
import getpass
import json
from pathlib import Path
import shutil
import subprocess
import sys
import time

import yaml

ROOT = Path(__file__).resolve().parents[1]
OUT = ROOT / '.generated/hopcharlie-apps'
NAMESPACE = 'argocd'
ROOT_APP = 'charlie-root'
PROJECT = 'charlie-hopcharlie-apps'
APPLICATION = 'charlie-management-hopcharlie-apps'
APPSET = 'hopcharlie-apps'
REPO_SECRET = 'charlie-gitops-repository'
# The Application's source must match what charlie-root would render.
SOURCES = ['helm/gitops', 'helm/charts/hopcharlie-apps', 'clusters/aws-dev-management/values/hopcharlie-apps.yaml']


class Refused(Exception):
    pass


def require(ok, message):
    if not ok:
        raise Refused(message)


def run(args, stdin=None, check=True):
    return subprocess.run(args, input=stdin, check=check, text=True, encoding='utf-8', stdout=subprocess.PIPE)


def gitops_url():
    values = yaml.safe_load((ROOT / 'helm/charts/hopcharlie-apps/values.yaml').read_text(encoding='utf-8'))
    return values['gitops']['repoURL']


def repository_secret(token):
    """The Argo CD repository Secret for the gitops repository (HTTPS, read-only token)."""
    return {
        'apiVersion': 'v1', 'kind': 'Secret',
        'metadata': {'name': REPO_SECRET, 'namespace': NAMESPACE,
                     'labels': {'argocd.argoproj.io/secret-type': 'repository'}},
        'type': 'Opaque',
        'stringData': {'type': 'git', 'url': gitops_url(), 'username': 'x-access-token', 'password': token},
    }


def repositories(argo):
    """Name to URL of every Argo CD repository Secret; only the url field is decoded."""
    listing = argo('-n', NAMESPACE, 'get', 'secret', '-l', 'argocd.argoproj.io/secret-type=repository', '-o',
                   'jsonpath={range .items[*]}{.metadata.name}{"\t"}{.data.url}{"\n"}{end}').stdout
    repos = {}
    for line in listing.splitlines():
        name, _, url = line.partition('\t')
        repos[name] = base64.b64decode(url).decode() if url else ''
    return repos


def read_token(path):
    token = path.read_text(encoding='utf-8-sig').strip()
    require(token and len(token.split()) == 1, f'{path} must hold one token on one line')
    return token


def pick(docs):
    """The AppProject and the Application of charlie-root's render, without automated sync."""
    project = [d for d in docs if d['kind'] == 'AppProject' and d['metadata']['name'] == PROJECT]
    app = [d for d in docs if d['kind'] == 'Application' and d['metadata']['name'] == APPLICATION]
    require(len(project) == 1 and len(app) == 1, f'helm/gitops did not render {PROJECT} and {APPLICATION}')
    app = app[0]
    app['spec'].get('syncPolicy', {}).pop('automated', None)
    app.pop('operation', None)
    return project[0], app


class Argo:
    def __init__(self, kubeconfig, context):
        self.base = ['kubectl', '--kubeconfig', str(kubeconfig), '--context', context]

    def __call__(self, *args, stdin=None, check=True):
        return run([*self.base, *args], stdin=stdin, check=check)

    def exists(self, kind, name):
        return bool(self('-n', NAMESPACE, 'get', kind, name, '-o', 'name', '--ignore-not-found').stdout.strip())

    def get(self, kind, name):
        return json.loads(self('-n', NAMESPACE, 'get', kind, name, '-o', 'json').stdout)


def render(argo):
    """Render helm/gitops with charlie-root's live values, and the hopcharlie-apps chart
    with the parameters the Application passes. Neither holds a secret."""
    values = argo.get('application', ROOT_APP)['spec']['source']['helm']['valuesObject']
    for purpose in ('management', 'workload'):
        require(values.get('targets', {}).get(purpose, {}).get('endpoint'), f'{ROOT_APP} has no targets.{purpose}')
    OUT.mkdir(parents=True, exist_ok=True)
    (OUT / 'root-values.json').write_text(json.dumps(values, indent=2) + '\n', encoding='utf-8')
    kube = ['--kube-version', json.loads((ROOT / 'helm/versions.json').read_text())['kubernetes'] + '.0']
    docs = [d for d in yaml.safe_load_all(run(['helm', 'template', ROOT_APP, str(ROOT / 'helm/gitops'), *kube,
                                               '--namespace', NAMESPACE, '-f', str(OUT / 'root-values.json')]).stdout) if d]
    project, app = pick(docs)
    (OUT / 'register.yaml').write_text(yaml.safe_dump_all([project, app], sort_keys=False), encoding='utf-8')
    source = app['spec']['source']
    chart = ROOT / source['path']
    args = ['helm', 'template', source['helm']['releaseName'], str(chart), *kube, '--namespace', NAMESPACE]
    for name in source['helm']['valueFiles']:
        args += ['-f', str((chart / name).resolve())]
    for p in source['helm']['parameters']:
        args += ['--set-string', f"{p['name']}={p['value']}"]
    (OUT / 'children.yaml').write_text(run(args).stdout, encoding='utf-8')
    return values, app


def at_main():
    run(['git', '-C', str(ROOT), 'fetch', '--quiet', 'origin', 'main'])
    head = run(['git', '-C', str(ROOT), 'rev-parse', 'HEAD']).stdout.strip()
    main = run(['git', '-C', str(ROOT), 'rev-parse', 'origin/main']).stdout.strip()
    clean = not run(['git', '-C', str(ROOT), 'status', '--porcelain', '--', *SOURCES]).stdout.strip()
    return head, head == main and clean


def preview(argo, args):
    head, ok = at_main()
    print(f'checkout {head[:12]}' + ('' if ok else ' (not origin/main or has local changes: preview only)'))
    values, app = render(argo)
    print(f"{ROOT_APP} values: repo {values['repoURL']} revision {values['revision']}, "
          f"workload {values['targets']['workload']['endpoint']}")
    print(f'rendered {OUT.relative_to(ROOT).as_posix()}/register.yaml and children.yaml')
    repos = repositories(argo)
    print('repository Secrets:' + ('' if repos else ' none'))
    for name, url in repos.items():
        print(f'  {name}: {url}')
    print(f'  {REPO_SECRET} for {gitops_url()}: {"exists" if REPO_SECRET in repos else "missing"}')
    present = {name: argo.exists(kind, name) for kind, name in
               (('appproject', PROJECT), ('application', APPLICATION), ('applicationset', APPSET))}
    for name, found in present.items():
        print(f'  {name}: {"exists" if found else "missing"}')
    if not any(present.values()):
        argo('create', '--dry-run=server', '-f', str(OUT / 'register.yaml'))
        print('server dry-run of register.yaml: ok')
    print('What the sync would create or change (kubectl diff --server-side):', flush=True)
    diff = subprocess.run([*argo.base, 'diff', '--server-side', '-f', str(OUT / 'children.yaml')])
    require(diff.returncode in (0, 1), 'kubectl diff failed')
    print('Nothing changed. The ApplicationSet turns every apps/*/eks.yaml of the gitops repository into an '
          'Application that deploys to the workload cluster; list them before sync:\n'
          '  gh api repos/2026-Softbank-Hackathon-Charlie/gitops/contents/apps --jq ".[].name"')


def add_repository(argo, args):
    require(not argo.exists('secret', REPO_SECRET), f'{NAMESPACE}/{REPO_SECRET} exists; it is not replaced')
    other = [name for name, url in repositories(argo).items() if url == gitops_url()]
    require(not other, f'{", ".join(other)} already holds a credential for {gitops_url()}')
    token = read_token(args.token_file)
    payload = json.dumps(repository_secret(token))
    for dry in (['--dry-run=server'], []):
        result = subprocess.run([*argo.base, 'create', *dry, '-f', '-'], input=payload, text=True, encoding='utf-8',
                                stdout=subprocess.PIPE, stderr=subprocess.PIPE)
        message = (result.stdout + result.stderr).replace(token, '***').strip()
        require(result.returncode == 0, f'kubectl create failed: {message}')
    print(f'created {NAMESPACE}/{REPO_SECRET} for {gitops_url()}; delete the token file now')


def create(argo, args):
    head, ok = at_main()
    require(ok, f'check out origin/main without local changes in {", ".join(SOURCES)} (now {head[:12]})')
    for kind, name in (('appproject', PROJECT), ('application', APPLICATION), ('applicationset', APPSET)):
        require(not argo.exists(kind, name), f'{kind} {name} exists; it is not replaced (if an earlier create '
                f'stopped halfway, review it and delete it with kubectl -n {NAMESPACE} delete {kind} {name})')
    render(argo)
    argo('create', '--dry-run=server', '-f', str(OUT / 'register.yaml'))
    print(argo('create', '-f', str(OUT / 'register.yaml')).stdout.strip())
    for _ in range(24):  # Argo CD compares the new Application within a few seconds
        status = argo.get('application', APPLICATION).get('status', {})
        if status.get('sync', {}).get('status'):
            break
        time.sleep(5)
    report(argo)
    print(f'{APPLICATION} has no automated sync. Review its diff, then run: sync')


def sync(argo, args):
    require(argo.exists('secret', REPO_SECRET), f'{NAMESPACE}/{REPO_SECRET} is missing; run add-repository first')
    app = argo.get('application', APPLICATION)
    require('operation' not in app, f'{APPLICATION} already has a pending operation')
    before = app.get('status', {}).get('operationState', {})
    require(before.get('phase') not in ('Running', 'Terminating'), f'{APPLICATION} has an operation running')
    # resourceVersion: the API server rejects the patch if the Application changed since the read.
    patch = {'metadata': {'resourceVersion': app['metadata']['resourceVersion']},
             'operation': {'initiatedBy': {'username': getpass.getuser()},
                           'sync': {'syncOptions': ['ServerSideApply=true']}}}
    argo('-n', NAMESPACE, 'patch', 'application', APPLICATION, '--type', 'merge', '-p', json.dumps(patch))
    print(f'sync requested for {APPLICATION}; waiting up to 5 minutes', flush=True)
    state, started = {}, False
    for _ in range(60):
        time.sleep(5)
        state = argo.get('application', APPLICATION).get('status', {}).get('operationState', {})
        started = state.get('startedAt') != before.get('startedAt')
        if started and state.get('phase') in ('Succeeded', 'Failed', 'Error'):
            break
    phase = state.get('phase', 'unknown') if started else 'not started'
    print(f"operation: {phase} {state.get('message', '') if started else ''}".rstrip())
    report(argo)
    if argo.exists('applicationset', APPSET):
        appset = argo.get('applicationset', APPSET)
        for c in appset.get('status', {}).get('conditions', []):
            print(f"  ApplicationSet {c.get('type')}={c.get('status')} {c.get('reason', '')}: {c.get('message', '')}")
    apps = json.loads(argo('-n', NAMESPACE, 'get', 'applications', '-l', 'hopcharlie.dev/app', '-o', 'json').stdout)['items']
    for a in apps:
        s = a.get('status', {})
        print(f"  app {a['metadata']['name']}: {s.get('sync', {}).get('status')} {s.get('health', {}).get('status')}")
    require(phase == 'Succeeded', f'the sync is {phase}; nothing was retried')


def report(argo):
    status = argo.get('application', APPLICATION).get('status', {})
    print(f"{APPLICATION}: sync {status.get('sync', {}).get('status')}, health {status.get('health', {}).get('status')}")
    for r in status.get('resources', []):
        print(f"  {r.get('kind')} {r.get('name')}: {r.get('status')}")
    for c in status.get('conditions', []):
        print(f"  condition {c.get('type')}: {c.get('message')}")


def main(argv=None):
    parser = argparse.ArgumentParser(description=__doc__, formatter_class=argparse.RawDescriptionHelpFormatter)
    parser.add_argument('command', choices=('preview', 'add-repository', 'create', 'sync'))
    parser.add_argument('--token-file', type=Path, help='add-repository: file with the read-only gitops token')
    parser.add_argument('--kubeconfig', type=Path, default=ROOT / '.generated/access/management.kubeconfig')
    parser.add_argument('--context', default='charlie-dev-management')
    args = parser.parse_args(argv)
    if args.command == 'add-repository' and not args.token_file:
        parser.error('add-repository needs --token-file')
    try:
        for tool in ('kubectl', 'helm', 'git') if args.command in ('preview', 'create') else ('kubectl',):
            require(shutil.which(tool), f'{tool} is not on PATH')
        require(args.kubeconfig.is_file(), f'{args.kubeconfig} is missing; run make aws-tunnel first')
        argo = Argo(args.kubeconfig, args.context)
        {'preview': preview, 'add-repository': add_repository, 'create': create, 'sync': sync}[args.command](argo, args)
    except Refused as error:
        print(f'register-hopcharlie-apps: {error}', file=sys.stderr)
        return 1
    except subprocess.CalledProcessError as error:
        print(f'register-hopcharlie-apps: {error.cmd[0]} failed; see its error above. Nothing was retried.', file=sys.stderr)
        return 1
    except (KeyError, IndexError, TypeError, ValueError, OSError) as error:
        print(f'register-hopcharlie-apps: unexpected input ({type(error).__name__}); nothing more was changed.', file=sys.stderr)
        return 1
    return 0


if __name__ == '__main__':
    sys.exit(main())
