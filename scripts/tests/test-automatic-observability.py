#!/usr/bin/env python3
"""Automatic onboarding render contracts. No credentials, cluster or cloud API."""
import copy
import hashlib
import json
import os
from pathlib import Path
import subprocess
import tempfile
import unittest
import yaml
ROOT=Path(__file__).resolve().parents[2]
HELM=os.environ.get('HELM','helm')
LOCK=json.loads((ROOT/'helm/images.lock.json').read_text())['images']

def load(path): return yaml.safe_load((ROOT/path).read_text())
def render(chart,values=None,namespace='proof',release='proof',extra=()):
    with tempfile.TemporaryDirectory() as temp:
        args=[HELM,'template',release,str(chart if isinstance(chart,Path) else ROOT/'helm/charts'/chart),'-n',namespace,'--kube-version','1.35.0',*extra]
        if values is not None:
            path=Path(temp)/'values.json';path.write_text(json.dumps(values));args+=['-f',str(path)]
        result=subprocess.run(args,capture_output=True,text=True)
        if result.returncode: raise ValueError(result.stderr)
        return [d for d in yaml.safe_load_all(result.stdout) if d]
def of(docs,kind): return [x for x in docs if x['kind']==kind]
def images(obj):
    if isinstance(obj,dict):
        for k,v in obj.items():
            if k=='image' and isinstance(v,str): yield v
            yield from images(v)
    elif isinstance(obj,list):
        for v in obj:yield from images(v)

class AutomaticObservability(unittest.TestCase):
    def app(self,stateful=False):
        v=load('helm/charts/hopcharlie-app/ci/'+('stateful' if stateful else 'stateless')+'-values.yaml')
        v.update(app='unregistered-app',namespace='unregistered-app')
        v['values']['app']='unregistered-app'
        v.setdefault('profile',{})['observability']={'enabled':True,'metricsEnabled':stateful,'endpoint':'https://observability-gateway.observability.svc:443','caBundle':'-----BEGIN CERTIFICATE-----\nSYNTHETIC-PUBLIC-CA\n-----END CERTIFICATE-----','clusterName':'proof-workload','cloudProvider':'aws','region':'ap-northeast-2'}
        return v
    def test_new_app_services_replicas_and_ca_rotation(self):
        v=self.app(True); docs=render('hopcharlie-app',v,'unregistered-app')
        ns=of(docs,'Namespace')[0];self.assertEqual(ns['metadata']['labels']['observability.hopcharlie.dev/enabled'],'true')
        for d in of(docs,'Deployment'):
            pod=d['spec']['template']; env={e['name']:e for e in pod['spec']['containers'][0]['env']}
            self.assertEqual(env['OTEL_EXPORTER_OTLP_INSECURE']['value'],'false')
            self.assertEqual(pod['metadata']['labels']['app.kubernetes.io/version'],v['commit'])
            self.assertNotIn('OTEL_EXPORTER_OTLP_HEADERS',env)
            self.assertIn('OTEL_RESOURCE_ATTRIBUTES',env)
            self.assertEqual(pod['spec']['volumes'][-1]['configMap']['name'],'hopcharlie-observability-ca')
        self.assertEqual(len(of(docs,'Secret')),0)
        old=[d['spec']['template']['metadata']['annotations'] for d in of(docs,'Deployment')]
        v['profile']['observability']['caBundle']+='\nROTATED-PUBLIC-CA'
        new=render('hopcharlie-app',v,'unregistered-app')
        self.assertNotEqual(old,[d['spec']['template']['metadata']['annotations'] for d in of(new,'Deployment')])
        service=copy.deepcopy(v['values']['services'][-1]);service['name']='brand-new-worker';service.pop('port',None);service['replicas']=3
        v['values']['services'].append(service)
        newer=render('hopcharlie-app',v,'unregistered-app')
        d=next(d for d in of(newer,'Deployment') if d['metadata']['name'].endswith('brand-new-worker'))
        self.assertEqual(d['spec']['replicas'],3)
        self.assertIn('OTEL_SERVICE_NAME',[e['name'] for e in d['spec']['template']['spec']['containers'][0]['env']])
        self.assertEqual(len(of(newer,'PodMonitor')),1)
    def test_cnpg_and_exact_network_peers(self):
        docs=render('hopcharlie-app',self.app(True),'unregistered-app')
        self.assertEqual(of(docs,'Cluster')[0]['spec']['inheritedMetadata']['labels']['observability.hopcharlie.dev/kind'],'database')
        monitor=of(docs,'PodMonitor')[0]
        self.assertEqual(monitor['spec']['selector']['matchLabels']['cnpg.io/cluster'],'unregistered-app-db')
        self.assertEqual(monitor['spec']['podMetricsEndpoints'][0]['port'],'metrics')
        policies={d['metadata']['name']:d['spec'] for d in of(docs,'NetworkPolicy')}
        e=policies['hopcharlie-otel-egress']['egress'][0];self.assertEqual(e['ports'][0]['port'],4319)
        self.assertEqual(set(e['to'][0]),{'namespaceSelector','podSelector'})
        i=policies['hopcharlie-cnpg-metrics']['ingress'][0];self.assertEqual(i['ports'][0]['port'],9187)
        self.assertEqual(set(i['from'][0]),{'namespaceSelector','podSelector'})
        v=self.app(True);v['profile']['observability']['metricsEnabled']=False
        self.assertFalse(of(render('hopcharlie-app',v),'PodMonitor'))
    def test_reject_conflicting_or_insecure_settings(self):
        for patch in ({'endpoint':'http://gateway:443'},{'endpoint':'https://user:pass@gateway:443'},{'caBundle':''},{'clusterName':''}):
            v=self.app();v['profile']['observability'].update(patch)
            with self.assertRaises(ValueError): render('hopcharlie-app',v)
        v=self.app();v['values']['services'][0].setdefault('env',{})['OTEL_EXPORTER_OTLP_HEADERS']='must-not-render'
        with self.assertRaises(ValueError): render('hopcharlie-app',v)
    def test_eks_and_gke_application_enrollment_enabled(self):
        self.assertFalse(load('helm/gitops/values.yaml')['observability']['automatic']['enabled'])
        environments=load('clusters/aws-dev-management/values/hopcharlie-apps.yaml')['environments']
        eks=environments['eks']['profile']['observability']
        self.assertTrue(eks['enabled'])
        self.assertTrue(eks['metricsEnabled'])
        self.assertEqual(eks['clusterName'],'charlie-dev-workload')
        self.assertEqual(eks['endpoint'],'https://observability-gateway.observability.svc:443')
        self.assertIn('-----BEGIN CERTIFICATE-----',eks['caBundle'])
        self.assertTrue(environments['gke']['profile']['observability']['enabled'])
        self.assertFalse(environments['onprem']['profile']['observability']['enabled'])
        self.assertTrue(load('clusters/aws-dev-management/values/hopcharlie-platform.yaml')['observability']['automaticJobs'])
        for purpose in ('management','workload'):
            self.assertFalse(load(f'clusters/aws-dev-{purpose}/values/baseline.yaml')['networkPolicy']['isolateObservabilityGateway'])
    def test_platform_job_enrollment_and_retention(self):
        v=load('clusters/aws-dev-management/values/hopcharlie-platform.yaml');v['observability']['automaticJobs']=True;v['hopai']['keepFinishedSeconds']=1
        docs=render('hopcharlie-platform',v,'charlie-platform')
        cm=next(d for d in of(docs,'ConfigMap') if d['metadata']['name']=='hopcharlie-hopai-job')
        job=yaml.safe_load(cm['data']['job.yaml'])
        self.assertEqual(job['spec']['ttlSecondsAfterFinished'],3600)
        self.assertEqual(job['spec']['template']['metadata']['labels']['observability.hopcharlie.dev/kind'],'job')
    def test_boundary_rbac_and_ingress(self):
        docs=render('observability-boundary',load('clusters/aws-dev-workload/values/observability-boundary.yaml'),'observability')
        self.assertFalse(of(docs,'Secret'))
        for ref in images(docs):
            name,digest=ref.rsplit('@',1);self.assertEqual(LOCK[name],digest)
        for d in of(docs,'ClusterRole'):
            for rule in d['rules']:
                self.assertNotIn('secrets',rule.get('resources',[]));self.assertNotIn('pods/log',rule.get('resources',[]))
        role=of(docs,'Role')[0];self.assertEqual(role['metadata']['namespace'],'observability')
        service=of(docs,'Service')[0];self.assertEqual(service['spec']['loadBalancerSourceRanges'],['10.40.0.0/19'])
        config=of(docs,'ConfigMap')[0]['data']['nginx.conf']
        deploy=of(docs,'Deployment')[0]['spec']['template']
        self.assertEqual(deploy['metadata']['annotations']['checksum/config'],hashlib.sha256(config.rstrip('\n').encode()).hexdigest())
        self.assertTrue(deploy['spec']['containers'][0]['securityContext']['readOnlyRootFilesystem'])
        for module in ('fastcgi','uwsgi','scgi'):
            self.assertIn(f'{module}_temp_path /tmp/{module};',config)
        policy=of(docs,'NetworkPolicy')[0];app=policy['spec']['ingress'][1]
        self.assertEqual(set(app['from'][0]),{'namespaceSelector','podSelector'})
        self.assertEqual(app['ports'][0]['port'],4319)
    def test_root_gate_and_upstream_pins(self):
        v=load('helm/gitops/ci/targets-values.yaml');v['observability']={'enabled':True,'automatic':{'enabled':True},'agentTargets':['management','workload'],'gatewayTargets':['management','workload']}
        docs=render(ROOT/'helm/gitops',v,'argocd');apps=of(docs,'Application')
        agent=next(a for a in apps if a['metadata']['name']=='charlie-workload-observability-agent')
        self.assertEqual(agent['spec']['sources'][0]['chart'],'alloy');self.assertEqual(agent['spec']['sources'][0]['targetRevision'],'1.15.0')
        for a in apps:
            if 'observability' in a['metadata']['name']:self.assertNotIn('automated',a['spec'].get('syncPolicy',{}))
    def test_real_upstream_charts(self):
        for purpose in ('management','workload'):
            docs=render(Path(os.environ['ALLOY_CHART']),load(f'clusters/aws-dev-{purpose}/values/alloy.yaml'),'observability','observability-agent')
            for image in images(docs):
                name,digest=image.rsplit('@',1)
                if name.endswith('grafana/alloy'):name+=':v1.21.3'
                self.assertEqual(LOCK[name],digest)
            self.assertFalse(of(docs,'ClusterRole'));self.assertFalse(of(docs,'Secret'))
            ds=of(docs,'DaemonSet')[0];self.assertEqual(ds['spec']['template']['spec']['serviceAccountName'],'observability-agent')
            self.assertIn('--stability.level=public-preview',ds['spec']['template']['spec']['containers'][0]['args'])
            gateways=render(Path(os.environ['OTEL_CHART']),load(f'clusters/aws-dev-{purpose}/values/otel-gateway-automatic.yaml'),'observability','observability-gateway')
            config=yaml.safe_load(of(gateways,'ConfigMap')[0]['data']['relay'])
            self.assertEqual(config['processors']['k8sattributes/app']['pod_association'],[{'sources':[{'from':'connection'}]}])
        prom=render(Path(os.environ['KPS_CHART']),load('clusters/aws-dev-workload/values/cnpg-prometheus.yaml'),'observability','monitoring')
        self.assertEqual([d['metadata']['name'] for d in of(prom,'Secret')],['monitoring-prometheus-scrape-confg']);self.assertFalse(of(prom,'Job'));self.assertFalse(of(prom,'Alertmanager'))
        self.assertFalse(of(prom,'ClusterRole'))
        self.assertEqual({d['metadata']['name'] for d in of(prom,'Deployment')},{'monitoring-operator','monitoring-kube-state-metrics'})
        cr=of(prom,'Prometheus')[0];self.assertEqual(cr['spec']['podMonitorNamespaceSelector']['matchLabels']['observability.hopcharlie.dev/enabled'],'true')
        operator=next(d for d in of(prom,'Deployment') if d['metadata']['name']=='monitoring-operator')['spec']['template']['spec']
        args=operator['containers'][0]['args']
        self.assertIn('--watch-referenced-objects-in-all-namespaces=false',args)
        self.assertFalse(any(a.startswith('--kubelet-service=') for a in args))
        self.assertIn('--prometheus-instance-namespaces=observability',args)
        self.assertIn('--alertmanager-config-namespaces=observability',args)
        self.assertFalse([v for v in (operator.get('volumes') or []) if v.get('secret',{}).get('secretName')=='monitoring-admission'])
        container=operator['containers'][0]
        self.assertEqual(container['ports'][0],{'containerPort':8080,'name':'http'})
        self.assertEqual(container['readinessProbe']['httpGet']['port'],'http')
        service=next(d for d in of(prom,'Service') if d['metadata']['name']=='monitoring-operator')
        self.assertEqual(service['spec']['ports'][0]['targetPort'],'http')
        monitor=next(d for d in of(prom,'ServiceMonitor') if d['metadata']['name']=='monitoring-operator')
        self.assertEqual(monitor['spec']['endpoints'][0].get('scheme','http'),'http')
        self.assertEqual(service['spec']['ports'][0]['port'],8080)
        self.assertNotIn('tlsConfig',monitor['spec']['endpoints'][0])
    def test_aws_platform_metrics_permissions_and_scraping(self):
        values=load('clusters/aws-dev-workload/values/observability-boundary.yaml')
        boundary=render('observability-boundary',values,'observability')
        roles={d['metadata']['name']:d for d in of(boundary,'ClusterRole')}
        self.assertEqual(roles['monitoring-aws-platform-metrics']['rules'],[
            {'apiGroups':[''],'resources':['nodes'],'verbs':['get','list','watch']},
            {'apiGroups':[''],'resources':['nodes/proxy'],'verbs':['get']}])
        ksm=roles['monitoring-aws-kube-state-metrics']
        self.assertEqual({r for rule in ksm['rules'] for r in rule['resources']},
            {'pods','nodes','namespaces','persistentvolumes','persistentvolumeclaims','services','deployments','statefulsets','daemonsets','replicasets','jobs','cronjobs'})
        for rule in ksm['rules']:self.assertEqual(rule['verbs'],['list','watch'])
        for binding in of(boundary,'ClusterRoleBinding'):
            if binding['metadata']['name'].startswith('monitoring-aws-'):
                self.assertEqual(binding['subjects'][0]['namespace'],'observability')
        values['awsPlatformMetrics']=False
        disabled=render('observability-boundary',values,'observability')
        self.assertFalse([d for d in disabled if d['metadata']['name'].startswith('monitoring-aws-')])
        for purpose in ('management',):
            docs=render('observability-boundary',load(f'clusters/aws-dev-{purpose}/values/observability-boundary.yaml'),'observability')
            self.assertFalse([d for d in docs if d['metadata']['name'].startswith('monitoring-aws-')])
        prom=render(Path(os.environ['KPS_CHART']),load('clusters/aws-dev-workload/values/cnpg-prometheus.yaml'),'observability','monitoring')
        spec=of(prom,'Prometheus')[0]['spec']
        self.assertEqual(spec['serviceMonitorNamespaceSelector'],{'matchLabels':{'kubernetes.io/metadata.name':'observability'}})
        selected={d['metadata']['name'] for d in of(prom,'ServiceMonitor') if all(d['metadata']['labels'].get(k)==v for k,v in spec['serviceMonitorSelector']['matchLabels'].items())}
        self.assertEqual(selected,{'monitoring-operator','monitoring-prometheus','monitoring-node-exporter','monitoring-kube-state-metrics'})
        import base64
        scrape=yaml.safe_load(base64.b64decode(of(prom,'Secret')[0]['data']['additional-scrape-configs.yaml']))
        self.assertEqual({j['job_name'] for j in scrape},{'aws-kubelet','aws-cadvisor'})
        for job in scrape:
            self.assertFalse(job['tls_config']['insecure_skip_verify'])
            self.assertEqual(job['authorization']['credentials_file'],'/var/run/secrets/kubernetes.io/serviceaccount/token')
            self.assertEqual(job['kubernetes_sd_configs'],[{'role':'node'}])
        pod=of(prom,'DaemonSet')[0]['spec']['template']['spec'];c=pod['containers'][0]
        self.assertTrue(pod['hostNetwork']);self.assertFalse(pod['hostPID']);self.assertFalse(pod['automountServiceAccountToken'])
        self.assertEqual(pod['securityContext']['runAsUser'],65534)
        self.assertEqual(c['securityContext'],{'allowPrivilegeEscalation':False,'capabilities':{'drop':['ALL']},'readOnlyRootFilesystem':True})
        self.assertEqual({v['hostPath']['path'] for v in pod['volumes']},{'/proc','/sys','/'})
        self.assertTrue(all(v['readOnly'] for v in c['volumeMounts']))
        state=next(d for d in of(prom,'Deployment') if d['metadata']['name']=='monitoring-kube-state-metrics')
        args=state['spec']['template']['spec']['containers'][0]['args']
        self.assertIn('--resources=pods,nodes,namespaces,deployments,statefulsets,daemonsets,replicasets,jobs,cronjobs,persistentvolumes,persistentvolumeclaims,services',args)
        for ref in images(prom):
            name,digest=ref.rsplit('@',1);self.assertEqual(LOCK[name],digest)

    def test_activation_overrides_are_manual_and_narrow(self):
        import importlib.util
        spec=importlib.util.spec_from_file_location('activation',ROOT/'scripts/render-observability-activation.py')
        module=importlib.util.module_from_spec(spec);spec.loader.exec_module(module)
        targets=load('helm/gitops/ci/targets-values.yaml')['targets']
        for target in targets.values(): target['vpc_id']='vpc-04b7c6a79ad46fcdc'
        sha='a'*40
        selected,values=module.render(sha,targets)
        self.assertEqual(len(selected),9)
        click=next(d for d in selected if d['metadata']['name']=='charlie-management-clickstack')
        self.assertEqual(click['spec']['source']['helm']['valueFiles'],[
            '../../../clusters/aws-dev-management/values/clickstack.yaml',
            '../../../clusters/aws-dev-management/values/clickstack-workload.yaml'])
        self.assertFalse(of(render('observability-boundary'), 'Deployment'))
        base=load('clusters/aws-dev-workload/values/baseline.yaml')
        before=render('cluster-baseline',base,'charlie-apps')
        base['networkPolicy'].update(load('clusters/aws-dev-workload/values/baseline-observability.yaml')['networkPolicy'])
        after=render('cluster-baseline',base,'charlie-apps')
        changed=[(b,a) for b,a in zip(before,after) if b!=a]
        self.assertEqual(len(changed),1)
        policy=changed[0][1]
        self.assertEqual((policy['kind'],policy['metadata']['namespace'],policy['metadata']['name']),('NetworkPolicy','observability','system-compatibility'))
        self.assertEqual(policy['spec']['podSelector']['matchExpressions'][0]['operator'],'NotIn')
        self.assertEqual(policy['spec']['egress'],[{}])
        central=load('clusters/aws-dev-management/values/clickstack.yaml')
        central.update(load('clusters/aws-dev-management/values/clickstack-workload.yaml'))
        docs=render('clickstack',central,'observability','clickstack')
        service=next(d for d in of(docs,'Service') if d['metadata']['name']=='clickstack-otlp-private')
        self.assertEqual(service['spec']['loadBalancerSourceRanges'],['10.40.32.0/19'])
        self.assertFalse(of(docs,'Secret'))
        with self.assertRaises(ValueError): module.render('main',targets)
        other=copy.deepcopy(targets);other['workload']['endpoint']='http://unsafe.invalid'
        with self.assertRaises(ValueError): module.render(sha,other)

    def test_hyperdx_query_trust_opt_in(self):
        docs=render('clickstack',{'hyperdx':{'queryTrust':{'enabled':True}}},'observability','clickstack')
        app=next(d for d in of(docs,'Deployment') if d['metadata']['name']!='clickstack-otel-collector')
        env={e['name']:e for e in app['spec']['template']['spec']['containers'][0]['env']}
        self.assertEqual(env['NODE_EXTRA_CA_CERTS']['value'],'/etc/hyperdx/query-ca/ca.crt')
        self.assertNotIn('NODE_TLS_REJECT_UNAUTHORIZED',env)
if __name__=='__main__':unittest.main()
