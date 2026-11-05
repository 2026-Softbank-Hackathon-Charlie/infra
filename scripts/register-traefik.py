#!/usr/bin/env python3
"""Add the workload cluster's shared app entry, Traefik (ADR 0058), to the
management Argo CD without syncing charlie-root.

  preview   changes nothing: renders helm/gitops with charlie-root's own values,
            shows what the AppProject change would do (kubectl diff) and
            dry-runs the new Application on the server
  create    applies AppProject charlie-addons-workload as rendered (one more
            source repository and the traefik namespace) and creates
            Application charlie-workload-traefik without automated sync, with
            the resources finalizer
  sync      asks for one sync of that Application and reports its resources

Every command uses the management kubeconfig and names only these objects. The
Traefik Service carries Delete=false, so deleting the Application keeps the NLB;
see docs/runbooks/app-entry.md.
"""
import argparse
import getpass
import json
from pathlib import Path
import shutil
import subprocess
import sys
import time

import yaml

ROOT = Path(__file__).resolve().parents[1]
OUT = ROOT / '.generated/traefik'
NAMESPACE = 'argocd'
ROOT_APP = 'charlie-root'
PROJECT = 'charlie-addons-workload'
APPLICATION = 'charlie-workload-traefik'
FINALIZER = 'resources-finalizer.argocd.argoproj.io'
# The objects must match what charlie-root renders from these files.
SOURCES = ['helm/gitops', 'clusters/aws-dev-workload/values/traefik.yaml', 'helm/versions.json']
TRAEFIK_REPO = 'https://traefik.github.io/charts'
TRAEFIK_NAMESPACE = 'traefik'


class Refused(Exception):
    pass


def require(ok, message):
    if not ok:
        raise Refused(message)


def run(args, stdin=None, check=True):
    return subprocess.run(args, input=stdin, check=check, text=True, encoding='utf-8', stdout=subprocess.PIPE)


class Argo:
    def __init__(self, kubeconfig, context):
        self.base = ['kubectl', '--kubeconfig', str(kubeconfig)] + (['--context', context] if context else [])

    def __call__(self, *args, check=True):
        return run([*self.base, *args], check=check)

    def exists(self, kind, name):
        return bool(self('-n', NAMESPACE, 'get', kind, name, '-o', 'name', '--ignore-not-found').stdout.strip())

    def get(self, kind, name):
        return json.loads(self('-n', NAMESPACE, 'get', kind, name, '-o', 'json').stdout)


def pick(docs):
    """The AppProject and the Traefik Application of charlie-root's render. The
    Application gets the resources finalizer and no automated sync, like the
    other workload addons."""
    project = [d for d in docs if d['kind'] == 'AppProject' and d['metadata']['name'] == PROJECT]
    app = [d for d in docs if d['kind'] == 'Application' and d['metadata']['name'] == APPLICATION]
    require(len(project) == 1 and len(app) == 1, f'helm/gitops did not render {PROJECT} and {APPLICATION}')
    app = app[0]
    app['spec']['syncPolicy'].pop('automated', None)
    app['metadata']['finalizers'] = [FINALIZER]
    app.pop('operation', None)
    return project[0], app


def project_change(live, rendered, workload):
    """What applying the rendered AppProject would change, ignoring list order.
    Returns the problems: anything but adding the Traefik repository and the
    traefik destination of the workload cluster."""
    problems = []
    keys = set(live.get('spec', {})) | set(rendered['spec'])
    for key in sorted(keys - {'sourceRepos', 'destinations'}):
        if live.get('spec', {}).get(key) != rendered['spec'].get(key):
            problems.append(f'spec.{key} differs')
    repos_live, repos_new = set(live.get('spec', {}).get('sourceRepos', [])), set(rendered['spec'].get('sourceRepos', []))
    if repos_live - repos_new:
        problems.append(f'removes source repositories {sorted(repos_live - repos_new)}')
    if repos_new - repos_live - {TRAEFIK_REPO}:
        problems.append(f'adds source repositories {sorted(repos_new - repos_live - {TRAEFIK_REPO})}')
    def dests(project):
        return {(d.get('server', ''), d.get('name', ''), d.get('namespace', '')) for d in project.get('spec', {}).get('destinations', [])}
    dests_live, dests_new = dests(live), dests(rendered)
    if dests_live - dests_new:
        problems.append(f'removes destinations {sorted(dests_live - dests_new)}')
    if dests_new - dests_live - {(workload, '', TRAEFIK_NAMESPACE)}:
        problems.append(f'adds destinations {sorted(dests_new - dests_live - {(workload, "", TRAEFIK_NAMESPACE)})}')
    notes = live.get('metadata', {}).get('annotations') or {}
    for key, value in (rendered['metadata'].get('annotations') or {}).items():
        if notes.get(key) != value:
            problems.append(f'annotation {key} differs')
    return problems


def check_project(argo, values, project):
    problems = project_change(argo.get('appproject', PROJECT), project, values['targets']['workload']['endpoint'])
    require(not problems, f'{PROJECT} would change more than the Traefik repository and destination: ' + '; '.join(problems))
    print(f'{PROJECT}: only the Traefik repository and the traefik destination are added (or already there)')


def render(argo):
    values = argo.get('application', ROOT_APP)['spec']['source']['helm']['valuesObject']
    require(values.get('targets', {}).get('workload', {}).get('endpoint'), f'{ROOT_APP} has no targets.workload')
    OUT.mkdir(parents=True, exist_ok=True)
    (OUT / 'root-values.json').write_text(json.dumps(values, indent=2) + '\n', encoding='utf-8')
    kube = ['--kube-version', json.loads((ROOT / 'helm/versions.json').read_text())['kubernetes'] + '.0']
    docs = [d for d in yaml.safe_load_all(run(['helm', 'template', ROOT_APP, str(ROOT / 'helm/gitops'), *kube,
                                               '--namespace', NAMESPACE, '-f', str(OUT / 'root-values.json')]).stdout) if d]
    project, app = pick(docs)
    (OUT / 'project.yaml').write_text(yaml.safe_dump(project, sort_keys=False), encoding='utf-8')
    (OUT / 'application.yaml').write_text(yaml.safe_dump(app, sort_keys=False), encoding='utf-8')
    return values, project, app


def at_main():
    run(['git', '-C', str(ROOT), 'fetch', '--quiet', 'origin', 'main'])
    head = run(['git', '-C', str(ROOT), 'rev-parse', 'HEAD']).stdout.strip()
    main = run(['git', '-C', str(ROOT), 'rev-parse', 'origin/main']).stdout.strip()
    clean = not run(['git', '-C', str(ROOT), 'status', '--porcelain', '--', *SOURCES]).stdout.strip()
    return head, head == main and clean


def preview(argo, args):
    head, ok = at_main()
    print(f'checkout {head[:12]}' + ('' if ok else ' (not origin/main or has local changes: preview only)'))
    values, project, app = render(argo)
    print(f"{ROOT_APP} values: repo {values['repoURL']} revision {values['revision']}, "
          f"workload {values['targets']['workload']['endpoint']}")
    chart = next(s for s in app['spec']['sources'] if 'chart' in s)
    print(f"{APPLICATION}: chart {chart['chart']} {chart['targetRevision']} from {chart['repoURL']}, "
          f"namespace {app['spec']['destination']['namespace']}, skipCrds {chart['helm'].get('skipCrds', False)}, "
          f"namespace labels {app['spec']['syncPolicy'].get('managedNamespaceMetadata', {}).get('labels')}")
    print(f'{PROJECT} change (kubectl diff):', flush=True)
    diff = subprocess.run([*argo.base, 'diff', '-f', str(OUT / 'project.yaml')])
    require(diff.returncode in (0, 1), 'kubectl diff failed')
    check_project(argo, values, project)
    if argo.exists('application', APPLICATION):
        print(f'{APPLICATION}: exists')
    else:
        argo('create', '--dry-run=server', '-f', str(OUT / 'application.yaml'))
        print(f'{APPLICATION}: missing; server dry-run of its create: ok')


def create(argo, args):
    head, ok = at_main()
    require(ok, f'check out origin/main without local changes in {", ".join(SOURCES)} (now {head[:12]})')
    require(not argo.exists('application', APPLICATION), f'application {APPLICATION} exists; it is not replaced')
    values, project, _ = render(argo)
    check_project(argo, values, project)
    argo('apply', '--dry-run=server', '-f', str(OUT / 'project.yaml'))
    argo('create', '--dry-run=server', '-f', str(OUT / 'application.yaml'))
    print(argo('apply', '-f', str(OUT / 'project.yaml')).stdout.strip())
    print(argo('create', '-f', str(OUT / 'application.yaml')).stdout.strip())
    for _ in range(24):  # Argo CD compares the new Application within a few seconds
        if argo.get('application', APPLICATION).get('status', {}).get('sync', {}).get('status'):
            break
        time.sleep(5)
    report(argo)
    print(f'{APPLICATION} has no automated sync. Review its diff, then run: sync')


def sync(argo, args):
    app = argo.get('application', APPLICATION)
    require('operation' not in app, f'{APPLICATION} already has a pending operation')
    before = app.get('status', {}).get('operationState', {})
    require(before.get('phase') not in ('Running', 'Terminating'), f'{APPLICATION} has an operation running')
    options = app['spec'].get('syncPolicy', {}).get('syncOptions', [])
    patch = {'metadata': {'resourceVersion': app['metadata']['resourceVersion']},
             'operation': {'initiatedBy': {'username': getpass.getuser()}, 'sync': {'syncOptions': options}}}
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
    require(phase == 'Succeeded', f'the sync is {phase}; nothing was retried')


def report(argo):
    status = argo.get('application', APPLICATION).get('status', {})
    print(f"{APPLICATION}: sync {status.get('sync', {}).get('status')}, health {status.get('health', {}).get('status')}")
    for r in status.get('resources', []):
        print(f"  {r.get('kind')} {r.get('namespace', '')}/{r.get('name')}: {r.get('status')} {r.get('health', {}).get('status', '')}".rstrip())
    for c in status.get('conditions', []):
        print(f"  condition {c.get('type')}: {c.get('message')}")


def main(argv=None):
    parser = argparse.ArgumentParser(description=__doc__, formatter_class=argparse.RawDescriptionHelpFormatter)
    parser.add_argument('command', choices=('preview', 'create', 'sync'))
    parser.add_argument('--kubeconfig', type=Path, default=ROOT / '.generated/access/management.kubeconfig')
    parser.add_argument('--context', default='charlie-dev-management', help='empty: the kubeconfig\'s current context')
    args = parser.parse_args(argv)
    try:
        for tool in ('kubectl', 'helm', 'git') if args.command in ('preview', 'create') else ('kubectl',):
            require(shutil.which(tool), f'{tool} is not on PATH')
        require(args.kubeconfig.is_file(), f'{args.kubeconfig} is missing; run make aws-tunnel first')
        argo = Argo(args.kubeconfig, args.context)
        {'preview': preview, 'create': create, 'sync': sync}[args.command](argo, args)
    except Refused as error:
        print(f'register-traefik: {error}', file=sys.stderr)
        return 1
    except subprocess.CalledProcessError as error:
        print(f'register-traefik: {error.cmd[0]} failed; see its error above. Nothing was retried.', file=sys.stderr)
        return 1
    except (KeyError, IndexError, TypeError, ValueError, OSError, StopIteration) as error:
        print(f'register-traefik: unexpected input ({type(error).__name__}); nothing more was changed.', file=sys.stderr)
        return 1
    return 0


if __name__ == '__main__':
    sys.exit(main())
