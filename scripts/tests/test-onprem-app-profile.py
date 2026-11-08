"""Render existing app profiles without calling a cluster, model or cloud API."""
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
PROFILES=json.loads((ROOT/'clusters/aws-dev-management/values/hopcharlie-apps.yaml').read_text(encoding='utf8'))['environments']


def fixture(stateful=True):
    f='stateful' if stateful else 'stateless'
    value=yaml.safe_load((ROOT/f'helm/charts/hopcharlie-app/ci/{f}-values.yaml').read_text(encoding='utf8'))
    value.update(app='moimroom-mig' if stateful else 'hello-onprem',env='onprem',namespace='moimroom-mig' if stateful else 'hello-onprem')
    value['values']['app']=value['app']
    value['profile']=copy.deepcopy(PROFILES['onprem']['profile'])
    if stateful:
        value['values']['dbTopology']={'primary':'onprem','peer':'gke','members':['gke','eks','onprem']}
        value['values']['sealedSecrets']['pgApp']={k:'U1lOVEhFVElD' for k in ('uri','username','password','dbname','host','port','jdbc-uri')}
        value['values']['sealedSecrets']['pgCA']={'ca.crt':'U1lOVEhFVElD','ca.key':'U1lOVEhFVElD'}
    return value


def render(values):
    with tempfile.TemporaryDirectory(prefix='onprem-app-profile-') as directory:
        path=Path(directory)/'values.json'
        path.write_text(json.dumps(values),encoding='utf8')
        result=subprocess.run([HELM,'template',values['namespace'],str(ROOT/'helm/charts/hopcharlie-app'),
            '--namespace',values['namespace'],'-f',str(path)],capture_output=True,text=True,encoding='utf8')
        if result.returncode: raise AssertionError('App chart rejected reviewed profile: '+result.stderr)
        return [d for d in yaml.safe_load_all(result.stdout) if d]


def index(docs): return {(d['kind'],d['metadata']['name']):d for d in docs}


class OnpremProfile(unittest.TestCase):
    def test_exact_onprem_tls_identity_without_keys_or_auth_headers(self):
        profile=PROFILES['onprem']['profile']['observability']
        self.assertEqual({k:v for k,v in profile.items() if k!='caBundle'}, {
            'enabled':True,'metricsEnabled':True,'endpoint':'https://observability-gateway.observability.svc:443',
            'clusterName':'charlie-dev-onprem-workload','cloudProvider':'onprem','region':'onprem'})
        self.assertTrue(profile['caBundle'].startswith('-----BEGIN CERTIFICATE-----\n'))
        self.assertNotIn('PRIVATE KEY',json.dumps(profile))

    def test_stateful_identity_data_topology_sealed_values_unchanged(self):
        values=fixture(); old=copy.deepcopy(values); old['profile']['observability']['enabled']=False
        before=index(render(old)); after=index(render(values))
        for (kind,name),original in before.items():
            current=copy.deepcopy(after[(kind,name)])
            if kind=='Deployment':
                pod=current['spec']['template']; oldpod=original['spec']['template']
                self.assertEqual(pod['spec']['containers'][0]['image'],oldpod['spec']['containers'][0]['image'])
                container=pod['spec']['containers'][0]
                env={v['name']:v for v in container['env']}
                self.assertEqual(env['OTEL_SERVICE_NAME']['value'],'moimroom-mig-'+name.removeprefix('moimroom-mig-'))
                self.assertEqual(env['OTEL_EXPORTER_OTLP_INSECURE']['value'],'false')
                self.assertNotIn('OTEL_EXPORTER_OTLP_HEADERS',env)
                self.assertIn('k8s.cluster.name=charlie-dev-onprem-workload',env['OTEL_RESOURCE_ATTRIBUTES']['value'])
                self.assertIn('hopcharlie.environment.name=onprem',env['OTEL_RESOURCE_ATTRIBUTES']['value'])
                self.assertEqual(pod['metadata']['annotations'].pop('checksum/observability-ca'),hashlib.sha256(values['profile']['observability']['caBundle'].encode()).hexdigest())
                pod['metadata']['labels'].pop('observability.hopcharlie.dev/enabled')
                pod['metadata']['labels'].pop('observability.hopcharlie.dev/kind')
                self.assertEqual(pod['metadata']['labels'].pop('app.kubernetes.io/version'),values['commit'])
                container['env']=[e for e in container['env'] if not e['name'].startswith('OTEL_')]
                container['volumeMounts']=[v for v in container.get('volumeMounts',[]) if v['name']!='observability-ca']
                if not container['volumeMounts'] and 'volumeMounts' not in oldpod['spec']['containers'][0]: container.pop('volumeMounts')
                pod['spec']['volumes']=[v for v in pod['spec']['volumes'] if v['name']!='observability-ca']
                if not pod['metadata']['annotations']: pod['metadata'].pop('annotations')
                if not pod['spec']['volumes']: pod['spec'].pop('volumes')
            elif kind=='Cluster':
                inherited=current['spec'].pop('inheritedMetadata')
                self.assertEqual(inherited['labels']['observability.hopcharlie.dev/kind'],'database')
            elif kind=='Namespace':
                current['metadata']['labels'].pop('observability.hopcharlie.dev/enabled')
            self.assertEqual(current,original,(kind,name))
        self.assertEqual(set(after)-set(before),{('ConfigMap','hopcharlie-observability-ca'),('NetworkPolicy','hopcharlie-otel-egress'),
            ('PodMonitor','moimroom-mig-db'),('NetworkPolicy','hopcharlie-cnpg-metrics')})
        monitor=after[('PodMonitor','moimroom-mig-db')]
        self.assertEqual(monitor['spec']['podMetricsEndpoints'][0]['relabelings'],[
            {'targetLabel':'environment','replacement':'onprem'},{'targetLabel':'cluster','replacement':'charlie-dev-onprem-workload'}])

    def test_stateless_app_has_no_database_or_monitor(self):
        docs=render(fixture(False))
        self.assertFalse(any(d['kind'] in ('Cluster','PodMonitor','ScheduledBackup','ObjectStore') for d in docs))
        self.assertEqual(sum(d['kind']=='ConfigMap' and d['metadata']['name']=='hopcharlie-observability-ca' for d in docs),1)


if __name__=='__main__': unittest.main()
