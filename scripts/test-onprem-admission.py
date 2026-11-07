#!/usr/bin/env python3
"""Disposable kind API admission checks, never a production kubeconfig.

Uses real Kubernetes RBAC/CEL and official Argo CRDs. It does not install
Argo/Tailscale controllers or claim a working tailnet/remote deployment.
"""
import base64
import copy
import importlib.util
import json
import os
from pathlib import Path
import subprocess
import tempfile
import time
import urllib.request

import yaml

ROOT = Path(__file__).resolve().parents[1]
CONTEXT = 'kind-onprem-admission'
ACTOR = 'system:serviceaccount:charlie-platform:hopcharlie-platform-api'
ID = 'enr_0123456789abcdef01234567'
UID = '01234567-89ab-cdef-0123-456789abcdef'
NAME = 'hop-onprem-' + ID[4:]
HOST = 'hop-k3s-' + ID[4:]
DOMAIN = 'tail567fe8.ts.net'
ENV = 'laptop-test'
OWNER = 'hopcharlie.dev/onprem-enrollment'
CLUSTER_UID = 'hopcharlie.dev/onprem-cluster-uid'
ARGO_SHA = 'c9c369efcc5b2a0bd720803f8d14a1c3eaddf579'  # v3.5.3, chart 10.9.6


def kube(args, obj=None, actor=False, allow_failure=False):
    command = ['kubectl', '--context', CONTEXT, '--request-timeout=20s']
    if actor:
        command += ['--as=' + ACTOR, '--as-group=system:serviceaccounts',
                    '--as-group=system:serviceaccounts:charlie-platform', '--as-group=system:authenticated']
    result = subprocess.run(command + args, input=json.dumps(obj) if obj else None,
                            text=True, capture_output=True)
    if result.returncode and not allow_failure:
        raise AssertionError(result.stderr)
    return result


def render(chart, values, namespace):
    with tempfile.TemporaryDirectory() as directory:
        path = Path(directory) / 'values.json'
        path.write_text(json.dumps(values), encoding='utf-8')
        output = subprocess.check_output(['helm', 'template', 'fixture', str(ROOT / chart),
                    '-n', namespace, '-f', str(path)], text=True)
    return [d for d in yaml.safe_load_all(output) if d]


def decision(label, obj, allowed, verb='create'):
    result = kube([verb, '--dry-run=server', '--validate=false', '-f', '-'], obj, actor=True, allow_failure=True)
    if allowed:
        assert result.returncode == 0, label + ': ' + result.stderr
    else:
        assert result.returncode != 0 and ('hopcharlie-' in result.stderr or 'forbidden' in result.stderr.lower()), label + ': ' + result.stderr
    print(('ALLOW ' if allowed else 'DENY  ') + label, flush=True)


def apply(obj):
    return json.loads(kube(['apply', '--server-side', '-f', '-', '-o', 'json'], obj).stdout)


def secret():
    encode = lambda value: base64.b64encode(value.encode()).decode()
    config = json.dumps({'bearerToken':'eyJ.test.signature','tlsClientConfig':{'caData':'Y2E=','insecure':False,'serverName':'kubernetes.default.svc'}},separators=(',',':'),sort_keys=True)
    return {'apiVersion':'v1','kind':'Secret','metadata':{'name':NAME,'namespace':'argocd','labels':{OWNER:ID,CLUSTER_UID:UID,'argocd.argoproj.io/secret-type':'cluster'},'annotations':{'hopcharlie.dev/registration-version':'2','hopcharlie.dev/token-expires-at':'2027-01-01T00:00:00Z'}},'type':'Opaque',
        'data':{k:encode(v) for k,v in {'name':NAME,'server':'https://'+HOST+'.hopcharlie-db-network.svc.cluster.local:6443','config':config,'clusterResources':'true'}.items()}}


def route():
    return {'apiVersion':'v1','kind':'Service','metadata':{'name':HOST,'namespace':'hopcharlie-db-network','labels':{OWNER:ID,CLUSTER_UID:UID},'annotations':{
        'tailscale.com/tailnet-fqdn':HOST+'.'+DOMAIN,'tailscale.com/proxy-class':'hopcharlie-onprem-api-egress-management','tailscale.com/tags':'tag:hop-onprem-api-client'}},
        'spec':{'type':'ExternalName','externalName':HOST+'.'+DOMAIN,'ports':[{'name':'kubernetes','port':6443,'protocol':'TCP'}]}}


def argo(profile):
    profile=copy.deepcopy(profile)
    profile['onpremRegistration']={'enrollmentID':ID,'clusterUID':UID}
    meta = {'name':NAME,'namespace':'argocd','labels':{OWNER:ID,CLUSTER_UID:UID,'hopcharlie.dev/env':ENV}}
    project = {'apiVersion':'argoproj.io/v1alpha1','kind':'AppProject','metadata':copy.deepcopy(meta),'spec':{
        'sourceRepos':['https://github.com/2026-Softbank-Hackathon-Charlie/infra.git','https://github.com/2026-Softbank-Hackathon-Charlie/gitops.git'],
        'destinations':[{'name':NAME,'namespace':ns} for ns in ['*','!kube-*','!argocd','!charlie-system','!default','!hopcharlie-*']],
        'clusterResourceWhitelist':[{'group':'','kind':'Namespace'}],
        'namespaceResourceBlacklist':[{'group':'rbac.authorization.k8s.io','kind':'*'},{'group':'','kind':'Secret'}]}}
    appset = {'apiVersion':'argoproj.io/v1alpha1','kind':'ApplicationSet','metadata':meta,'spec':{
        'goTemplate':True,'goTemplateOptions':['missingkey=error'],
        'generators':[{'git':{'repoURL':'https://github.com/2026-Softbank-Hackathon-Charlie/gitops.git','revision':'main','files':[{'path':'apps/*/'+ENV+'.yaml'}],'requeueAfterSeconds':30}}],
        'template':{'metadata':{'name':'{{ .namespace }}-'+ENV,'labels':{'hopcharlie.dev/app':'{{ .app }}','hopcharlie.dev/instance':'{{ .namespace }}','hopcharlie.dev/env':ENV,OWNER:ID}},'spec':{
            'project':NAME,'destination':{'name':NAME,'namespace':'{{ .namespace }}'},'sources':[
                {'repoURL':'https://github.com/2026-Softbank-Hackathon-Charlie/infra.git','targetRevision':'main','path':'helm/charts/hopcharlie-app','helm':{'releaseName':'{{ .namespace }}','valueFiles':['$gitops/{{ .path.path }}/{{ .path.filename }}'],'valuesObject':{'profile':profile}}},
                {'repoURL':'https://github.com/2026-Softbank-Hackathon-Charlie/gitops.git','targetRevision':'main','ref':'gitops'}],
            'syncPolicy':{'automated':{'prune':True,'selfHeal':True},'retry':{'limit':10,'backoff':{'duration':'5s','factor':2,'maxDuration':'3m'}}}}}}}
    return project, appset


def main():
    assert os.environ.get('ONPREM_ADMISSION_FIXTURE') == '1', 'explicit disposable fixture opt-in required'
    context = subprocess.check_output(['kubectl','config','current-context'],text=True).strip()
    assert context == CONTEXT, 'refusing any kube context except the disposable kind fixture'
    for ns in ['argocd','charlie-platform','hopcharlie-db-network']:
        apply({'apiVersion':'v1','kind':'Namespace','metadata':{'name':ns}})
    for kind in ['appproject','applicationset','application']:
        url = f'https://raw.githubusercontent.com/argoproj/argo-cd/{ARGO_SHA}/manifests/crds/{kind}-crd.yaml'
        with urllib.request.urlopen(url, timeout=20) as response:
            data = response.read(4 << 20).decode()
        obj = yaml.safe_load(data)
        apply(obj)
        # A just-created CRD can temporarily have no status.conditions at all;
        # kubectl wait treats that as an accessor error instead of polling.
        for _ in range(60):
            current=json.loads(kube(['get','crd',obj['metadata']['name'],'-o','json']).stdout)
            if any(c.get('type')=='Established' and c.get('status')=='True' for c in current.get('status',{}).get('conditions',[])):
                break
            time.sleep(1)
        else:
            raise AssertionError('official Argo CRD did not become Established')
    module_spec = importlib.util.spec_from_file_location('render_test', ROOT/'scripts/tests/test-onprem-registration.py')
    module = importlib.util.module_from_spec(module_spec); module_spec.loader.exec_module(module)
    docs = module.render(True)
    apps = json.loads((ROOT/'clusters/aws-dev-management/values/hopcharlie-apps.yaml').read_text())
    docs += render('helm/charts/hopcharlie-apps', apps, 'argocd')
    docs += render('helm/charts/hopcharlie-db-network', {'enabled':False,'managementAppRoutes':{'enabled':True,'tailnet':DOMAIN,'tag':'tag:hop-db-client-management'}},'hopcharlie-db-network')
    kinds = {'ServiceAccount','Role','RoleBinding','ValidatingAdmissionPolicy','ValidatingAdmissionPolicyBinding'}
    for obj in docs:
        if obj['kind'] not in kinds: continue
        if obj['kind'] in {'ServiceAccount','Role','RoleBinding'}:
            obj['metadata'].setdefault('namespace','charlie-platform')
        apply(obj)
    # Admission policy propagation is asynchronous; wait for a real denial.
    invalid = route(); invalid['spec']['ports'][0]['port'] = 22
    for _ in range(30):
        result = kube(['create','--dry-run=server','-f','-'], invalid, actor=True, allow_failure=True)
        if result.returncode and 'hopcharlie-onprem-api-route' in result.stderr: break
        time.sleep(1)
    else: raise AssertionError('registration admission policy did not become active')
    for obj in docs:
        if obj['kind'] == 'ValidatingAdmissionPolicy':
            current = json.loads(kube(['get','validatingadmissionpolicy',obj['metadata']['name'],'-o','json']).stdout)
            warnings = current.get('status',{}).get('typeChecking',{}).get('expressionWarnings',[])
            assert not warnings, (obj['metadata']['name'],warnings)
    s = secret(); decision('owned cluster credential',s,True)
    for label, mutate in [
        ('foreign name',lambda o:o['metadata'].update(name='existing-eks-cluster')),
        ('wrong UID',lambda o:o['metadata']['labels'].update({CLUSTER_UID:'not-a-uid'})),
        ('wrong endpoint',lambda o:o['data'].update(server=base64.b64encode(b'https://evil.example:6443').decode())),
        ('insecure TLS',lambda o:o['data'].update(config=base64.b64encode(base64.b64decode(o['data']['config']).replace(b'false',b'true')).decode())),
        ('additional secret field',lambda o:o['data'].update(admin='c2VjcmV0'))]:
        bad=copy.deepcopy(s);mutate(bad);decision('credential '+label,bad,False)
    saved = apply(s)
    decision('owned credential unconditional update without GET',s,True,'replace')
    decision('owned credential rotation',saved,True,'replace')
    rotated=copy.deepcopy(saved);rotated['metadata']['annotations']['hopcharlie.dev/registration-version']='3';rotated['data']['config']=base64.b64encode(base64.b64decode(rotated['data']['config']).replace(b'eyJ.test.signature',b'eyJ.new.signature')).decode();decision('newer token rotation',rotated,True,'replace')
    for label,mutate in [
        ('stale version',lambda o:o['metadata']['annotations'].update({'hopcharlie.dev/registration-version':'1'})),
        ('invalid decimal',lambda o:o['metadata']['annotations'].update({'hopcharlie.dev/registration-version':'2x'})),
        ('expiry regression',lambda o:o['metadata']['annotations'].update({'hopcharlie.dev/token-expires-at':'2026-01-01T00:00:00Z'})),
        ('invalid timestamp',lambda o:o['metadata']['annotations'].update({'hopcharlie.dev/token-expires-at':'2027-99-01T00:00:00Z'})),
        ('same version changed token',lambda o:o['data'].update(config=base64.b64encode(base64.b64decode(o['data']['config']).replace(b'eyJ.test.signature',b'eyJ.new.signature')).decode())),
        ('CA substitution',lambda o:(o['metadata']['annotations'].update({'hopcharlie.dev/registration-version':'3'}),o['data'].update(config=base64.b64encode(base64.b64decode(o['data']['config']).replace(b'Y2E=',b'YmFk')).decode())))]:
        bad=copy.deepcopy(saved);mutate(bad);decision('credential '+label,bad,False,'replace')
    bad=copy.deepcopy(saved);bad['metadata']['labels'][CLUSTER_UID]='ffffffff-ffff-ffff-ffff-ffffffffffff';decision('credential UID takeover',bad,False,'replace')
    result = kube(['get','secret',NAME,'-n','argocd'],actor=True,allow_failure=True)
    assert result.returncode and 'forbidden' in result.stderr.lower();print('DENY  credential GET (including own)',flush=True)
    r=route();decision('owned API route',r,True)
    for label,mutate in [
        ('SSH port',lambda o:o['spec']['ports'][0].update(port=22)),
        ('arbitrary tailnet',lambda o:o['metadata']['annotations'].update({'tailscale.com/tailnet-fqdn':'other.tail567fe8.ts.net'})),
        ('legacy tag',lambda o:o['metadata']['annotations'].update({'tailscale.com/tags':'tag:hop-db-client-management'})),
        ('extra expose annotation',lambda o:o['metadata']['annotations'].update({'tailscale.com/expose':'true'}))]:
        bad=copy.deepcopy(r);mutate(bad);decision('route '+label,bad,False)
    saved=apply(r);saved['spec']['externalName']='ts-proxy.hopcharlie-db-network.svc.cluster.local';saved=apply(saved)
    decision('operator externalName preserved',saved,True,'replace')
    bad=copy.deepcopy(saved);bad['spec']['externalName']=HOST+'.'+DOMAIN;decision('operator externalName overwritten',bad,False,'replace')
    static=route();static['metadata']['name']='db-eks';static=apply(static);decision('static DB route update',static,False,'replace')
    static['metadata']['name']='db-gke';decision('static DB route creation',static,False)
    profile = json.loads(next(d for d in docs if d['kind']=='ConfigMap' and d['metadata']['name']=='hopcharlie-onprem-profile')['data']['profile.json'])
    p,a=argo(profile)
    decision('owned fixed project',p,True);decision('owned fixed application set',a,True)
    for label,original,mutate in [
        ('project management destination',p,lambda o:o['spec']['destinations'].append({'server':'https://kubernetes.default.svc','namespace':'*'})),
        ('project protected namespace',p,lambda o:o['spec']['destinations'].pop()),
        ('project cluster admin kinds',p,lambda o:o['spec']['clusterResourceWhitelist'].append({'group':'*','kind':'*'})),
        ('application source repo',a,lambda o:o['spec']['template']['spec']['sources'][0].update(repoURL='https://evil.example/repo.git')),
        ('application profile privilege',a,lambda o:o['spec']['template']['spec']['sources'][0]['helm']['valuesObject']['profile'].update(podSecurity='privileged')),
        ('application other environment',a,lambda o:o['spec']['generators'][0]['git']['files'][0].update(path='apps/*/eks.yaml')),
        ('application template patch',a,lambda o:o['spec'].update(templatePatch='spec: {}'))]:
        bad=copy.deepcopy(original);mutate(bad);decision(label,bad,False)
    for original in [p,a]:
        saved=apply(original);decision('owned '+saved['kind']+' update',saved,True,'replace')
        bad=copy.deepcopy(saved);bad['metadata']['labels'][CLUSTER_UID]='ffffffff-ffff-ffff-ffff-ffffffffffff';decision(saved['kind']+' UID takeover',bad,False,'replace')
        foreign=copy.deepcopy(original);foreign['metadata']['name']='existing-infra';foreign=apply(foreign);decision('foreign '+foreign['kind']+' update',foreign,False,'replace')
    # Actual DELETE must be authorized by stored ownership, not caller labels.
    for kind,namespace,name in [('secret','argocd',NAME),('service','hopcharlie-db-network',HOST),('appproject','argocd',NAME),('applicationset','argocd',NAME)]:
        result=kube(['delete',kind,name,'-n',namespace,'--dry-run=server'],actor=True,allow_failure=True)
        assert result.returncode == 0,(kind,result.stderr);print('ALLOW owned '+kind+' delete',flush=True)
    result=kube(['delete','service','db-eks','-n','hopcharlie-db-network','--dry-run=server'],actor=True,allow_failure=True)
    assert result.returncode and 'hopcharlie-' in result.stderr;print('DENY  static DB route delete',flush=True)
    print('PASS real Kubernetes RBAC/CEL with official Argo CRDs; no production resources used',flush=True)


if __name__ == '__main__':
    main()
