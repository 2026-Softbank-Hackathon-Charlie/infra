#!/usr/bin/env python3
"""Render the dedicated AI gateway's source-only quality controller. No API/apply."""
import argparse
import copy
import hashlib
import ipaddress
import json
import math
from pathlib import Path
import re
import yaml

ROOT = Path(__file__).resolve().parents[1]
IDENTIFIER = re.compile(r'[A-Za-z0-9][A-Za-z0-9_.-]{0,127}\Z')
MODEL = re.compile(r'[A-Za-z0-9][A-Za-z0-9_.:/@-]{0,127}\Z')
PINNED_MODEL = re.compile(r'[A-Za-z0-9][A-Za-z0-9_.:/-]{0,47}@sha256:[a-f0-9]{64}\Z')
FIELDS = {'app','projectId','sourceBackend','targetBackend','sourceModel','targetModel',
          'datasetRevision','promptRevision','retrievalRevision','evaluatorRevision','judgeModel',
          'scoreSource','items','metrics','maxAgeSeconds','minSoakSeconds'}
LABEL = {'app.kubernetes.io/name': 'ai-gateway'}
WEB = {'app.kubernetes.io/name':'langfuse','app.kubernetes.io/instance':'langfuse',
       'app.kubernetes.io/component':'web'}


def validate(p):
    if not isinstance(p,dict) or set(p) != FIELDS:
        raise ValueError('only the complete non-secret quality policy is accepted')
    ids=FIELDS-{'sourceModel','targetModel','judgeModel','items','metrics','maxAgeSeconds','minSoakSeconds'}
    if not all(isinstance(p[k],str) and IDENTIFIER.fullmatch(p[k]) for k in ids):
        raise ValueError('invalid policy identity/revision')
    if p['app']!='moimroom-ai' or p['sourceBackend']!='gcp' or p['targetBackend']!='laptop':
        raise ValueError('scope must be the dedicated moimroom-ai gcp to laptop gateway')
    if p['projectId']=='hopcharlie-llm-dev':
        raise ValueError('use a separate evaluation project, not the existing app observation project')
    if not all(isinstance(p[k],str) and PINNED_MODEL.fullmatch(p[k]) for k in ('sourceModel','targetModel')):
        raise ValueError('both model revisions must contain the reviewed artifact sha256')
    if (not isinstance(p['judgeModel'],str) or not MODEL.fullmatch(p['judgeModel'])
            or p['judgeModel'] in (p['sourceModel'],p['targetModel'])):
        raise ValueError('an independent reviewed judge revision is required')
    if p['scoreSource'] not in ('API','EVAL'):
        raise ValueError('unsupported score producer')
    for key,lower in [('maxAgeSeconds',60),('minSoakSeconds',30)]:
        if type(p[key]) is not int or not lower<=p[key]<=86400:
            raise ValueError('age/soak must be bounded integers')
    items=p['items']
    if (not isinstance(items,list) or not 20<=len(items)<=100
            or not all(isinstance(i,str) and IDENTIFIER.fullmatch(i) for i in items)
            or len(set(items))!=len(items)):
        raise ValueError('20-100 distinct reviewed dataset IDs are required')
    metrics=p['metrics']
    if not isinstance(metrics,list) or not 2<=len(metrics)<=4:
        raise ValueError('2-4 reviewed metrics are required')
    names,configs=set(),set()
    safety,task=False,False
    for m in metrics:
        if not isinstance(m,dict) or not {'name','configId','minimum','maxRegression'}<=set(m) or set(m)-{'name','configId','minimum','maxRegression','hardFailure'}:
            raise ValueError('invalid metric fields')
        for field,seen in [('name',names),('configId',configs)]:
            if not isinstance(m[field],str) or not IDENTIFIER.fullmatch(m[field]) or m[field] in seen:
                raise ValueError('metric names and config IDs must be unique')
            seen.add(m[field])
        for field,limit in [('minimum',1),('maxRegression',.2)]:
            n=m[field]
            if type(n) not in (int,float) or not math.isfinite(n) or not 0<=n<=limit:
                raise ValueError('invalid metric threshold')
        hard=m.get('hardFailure',False)
        if type(hard) is not bool: raise ValueError('hardFailure must be boolean')
        safety |= m['name']=='safety' and hard and m['minimum']==1 and m['maxRegression']==0
        task |= not hard
    if not safety or not task:
        raise ValueError('a strict safety gate and business metric are required')


def render(policy, service=None):
    validate(policy)
    if service is not None:
        if (not isinstance(service,dict) or set(service)!={'namespace','name','uid','clusterIP','port'}
                or service['namespace']!='langfuse' or service['name']!='langfuse-web'
                or type(service['port']) is not int or service['port']!=3000
                or not isinstance(service['uid'],str)
                or not re.fullmatch(r'[a-f0-9]{8}(?:-[a-f0-9]{4}){3}-[a-f0-9]{12}',service['uid'])):
            raise ValueError('only reviewed Langfuse web Service metadata is accepted')
        try:
            vip=ipaddress.ip_address(service['clusterIP'])
        except (ValueError,TypeError):
            raise ValueError('invalid exact Langfuse Service IP') from None
        network=ipaddress.ip_network('172.20.0.0/16')
        if vip.version!=4 or vip not in network or vip in (network.network_address,network.broadcast_address):
            raise ValueError('Service IP must be one host in the management service CIDR')
    # The caller supplies only non-secret provenance; endpoint, key paths,
    # target namespace and image are bound to reviewed repository configuration.
    policy=copy.deepcopy(policy)
    encoded=json.dumps(policy,sort_keys=True,separators=(',',':'))
    name='ai-gateway-quality-'+hashlib.sha256(encoded.encode()).hexdigest()[:12]
    config={'baseUrl':'http://langfuse-web.langfuse.svc.cluster.local:3000','apps':{
        'moimroom-ai':{'projectId':policy['projectId'],'metadataKey':'app',
                       'publicKeyFile':'/quality-auth/public-key','secretKeyFile':'/quality-auth/secret-key'}}}
    configmap={'apiVersion':'v1','kind':'ConfigMap','immutable':True,
        'metadata':{'name':name,'namespace':'hopcharlie-ai'},
        'data':{'policy.json':encoded,'langfuse.json':json.dumps(config,separators=(',',':'))}}
    pvc={'apiVersion':'v1','kind':'PersistentVolumeClaim',
         'metadata':{'name':'ai-gateway-canary-state','namespace':'hopcharlie-ai'},
         'spec':{'accessModes':['ReadWriteOnce'],'storageClassName':'gp3',
                 'resources':{'requests':{'storage':'1Gi'}}}}
    docs=list(yaml.safe_load_all((ROOT/'clusters/aws-dev-management/manifests/hopcharlie-ai.yaml').read_text(encoding='utf8')))
    deployment=copy.deepcopy(next(d for d in docs if d and d['kind']=='Deployment' and d['metadata']['name']=='ai-gateway'))
    pod=deployment['spec']['template']['spec']
    gateway=pod['containers'][0]
    current=json.loads((ROOT/'clusters/aws-dev-management/values/hopcharlie-platform.yaml').read_text(encoding='utf8'))
    image=current['images']['platform']
    if not re.fullmatch(r'525091405886\.dkr\.ecr\.ap-northeast-2\.amazonaws\.com/hopcharlie/platform:[A-Za-z0-9_.-]+@sha256:[a-f0-9]{64}',image):
        raise ValueError('management platform image must be immutable in the reviewed ECR repository')
    gateway['image']=image
    gateway['args']=[a for a in gateway['args'] if not a.startswith('-ai-follow-')]
    gateway['args']+=['-ai-quality-policy=/quality/policy.json','-ai-quality-langfuse-config=/quality/langfuse.json']
    gateway['volumeMounts'] += [{'name':'quality','mountPath':'/quality','readOnly':True},
                              {'name':'quality-auth','mountPath':'/quality-auth','readOnly':True}]
    pod['volumes']=[{'name':'state','persistentVolumeClaim':{'claimName':'ai-gateway-canary-state'}} if v['name']=='state' else v for v in pod['volumes']]
    pod['volumes'] += [{'name':'quality','configMap':{'name':name}},
                      {'name':'quality-auth','secret':{'secretName':'ai-gateway-quality-auth','defaultMode':0o440,
                          'items':[{'key':'public-key','path':'public-key'},{'key':'secret-key','path':'secret-key'}]}}]
    pod['terminationGracePeriodSeconds']=30
    ports=[{'protocol':'TCP','port':3000}]
    egress={'apiVersion':'networking.k8s.io/v1','kind':'NetworkPolicy',
            'metadata':{'name':'ai-gateway-langfuse-egress','namespace':'hopcharlie-ai'},
            'spec':{'podSelector':{'matchLabels':LABEL},'policyTypes':['Egress'],
                    'egress':[{'to':[{'namespaceSelector':{'matchLabels':{'kubernetes.io/metadata.name':'langfuse'}},
                                     'podSelector':{'matchLabels':WEB}}],'ports':ports}]}}
    if service is not None:
        # Some EKS NetworkPolicy enforcement paths see the pre-DNAT ClusterIP.
        # Keep the identity selector and add only this reviewed web VIP/port.
        egress['spec']['egress'].append({'to':[{'ipBlock':{'cidr':str(vip)+'/32'}}],'ports':copy.deepcopy(ports)})
    ingress={'apiVersion':'networking.k8s.io/v1','kind':'NetworkPolicy',
             'metadata':{'name':'langfuse-ai-gateway-ingress','namespace':'langfuse'},
             'spec':{'podSelector':{'matchLabels':WEB},'policyTypes':['Ingress'],
                     'ingress':[{'from':[{'namespaceSelector':{'matchLabels':{'kubernetes.io/metadata.name':'hopcharlie-ai'}},
                                         'podSelector':{'matchLabels':LABEL}}],'ports':ports}]}}
    return [configmap,pvc,deployment,egress,ingress]


def main():
    parser=argparse.ArgumentParser(description=__doc__)
    parser.add_argument('--policy-file',type=Path,required=True,help='complete, reviewed policy; never include credentials')
    parser.add_argument('--langfuse-service-file',type=Path,required=True,
                        help='live-verified public Service metadata only: namespace/name/uid/clusterIP/port')
    parser.add_argument('--output',type=Path,required=True)
    args=parser.parse_args()
    try:
        docs=render(json.loads(args.policy_file.read_text(encoding='utf8')),
                    json.loads(args.langfuse_service_file.read_text(encoding='utf8')))
        with args.output.open('x',encoding='utf8') as f: yaml.safe_dump_all(docs,f,sort_keys=False)
    except (ValueError,OSError,StopIteration,KeyError):
        parser.error('activation rejected: invalid policy, immutable image, source manifest, or output already exists')
    print('Rendered source-only controller: ConfigMap1/PVC1/Deployment1/NetworkPolicy2. No API access/apply/model calls.')


if __name__=='__main__': main()
