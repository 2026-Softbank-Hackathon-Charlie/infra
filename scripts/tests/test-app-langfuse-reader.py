"""Render the reader boundary without credentials, APIs or cluster access."""
import json
import os
from pathlib import Path
import subprocess
import tempfile
import unittest

import yaml

ROOT = Path(__file__).resolve().parents[2]
HELM = os.environ.get('HELM', 'helm')
APP = 'moimroom-mig'
PROJECT = 'cmw76sgdx000bzk08z3ez9xsq'
URL = 'http://langfuse-web.langfuse.svc.cluster.local:3000'


def settings(**extra):
    return dict(enabled=True, baseUrl=URL, apps={APP: dict(projectId=PROJECT, metadataKey='hopcharlie.app', secretName=APP+'-langfuse-reader')}, **extra)


def render(config):
    with tempfile.TemporaryDirectory(prefix='langfuse-reader-render-') as directory:
        values = Path(directory)/'values.json'
        values.write_text(json.dumps({'api': {'langfuse': config}}), encoding='utf8')
        result = subprocess.run([HELM, 'template', 'hopcharlie-platform', str(ROOT/'helm/charts/hopcharlie-platform'),
                                 '-f', str(ROOT/'clusters/aws-dev-management/values/hopcharlie-platform.yaml'),
                                 '-f', str(values)], capture_output=True, text=True, encoding='utf8')
        if result.returncode:
            raise ValueError('Helm rejected settings: '+result.stderr)
        return [d for d in yaml.safe_load_all(result.stdout) if d]


def named(docs, kind, name):
    found=next((d for d in docs if d['kind']==kind and d['metadata']['name']==name),None)
    if found is None: raise AssertionError('Missing rendered '+kind+'/'+name)
    return found


class ReaderBoundary(unittest.TestCase):
    def test_public_config_and_api_only_readonly_secret_files(self):
        docs=render(settings())
        config=json.loads(named(docs,'ConfigMap','hopcharlie-langfuse-reader')['data']['config.json'])
        self.assertEqual(config, {'baseUrl': URL, 'apps': {APP: {'projectId': PROJECT, 'metadataKey': 'hopcharlie.app',
            'publicKeyFile': '/var/run/langfuse/'+APP+'/public-key', 'secretKeyFile': '/var/run/langfuse/'+APP+'/secret-key'}}})
        api=named(docs,'Deployment','hopcharlie-api')['spec']['template']['spec']
        reader=next(v for v in api['volumes'] if v.get('secret',{}).get('secretName')==APP+'-langfuse-reader')
        self.assertEqual(reader['secret']['defaultMode'],0o440)
        self.assertEqual(reader['secret']['items'],[{'key':'public-key','path':'public-key'},{'key':'secret-key','path':'secret-key'}])
        container=api['containers'][0]
        self.assertIn({'name':'PLATFORM_LANGFUSE_CONFIG','value':'/etc/langfuse/config.json'},container['env'])
        self.assertIn({'name':reader['name'],'mountPath':'/var/run/langfuse/'+APP,'readOnly':True},container['volumeMounts'])
        for c in api.get('initContainers',[]):
            self.assertNotIn(reader['name'],[v['name'] for v in c.get('volumeMounts',[])])
        for d in docs:
            if d['kind']=='Deployment' and d['metadata']['name']!='hopcharlie-api':
                self.assertNotIn(APP+'-langfuse-reader',json.dumps(d))
        self.assertFalse(any(d['kind']=='Secret' and 'langfuse' in d['metadata']['name'] for d in docs))

    def test_default_off_removes_all_reader_capabilities(self):
        default=json.loads((ROOT/'helm/charts/hopcharlie-platform/values.yaml').read_text(encoding='utf8'))
        self.assertIn('langfuse',default['api'])
        self.assertFalse(default['api']['langfuse']['enabled'])
        docs=render({'enabled':False})
        text=json.dumps(docs)
        for marker in ('PLATFORM_LANGFUSE_CONFIG','hopcharlie-langfuse-reader','hopcharlie-app-langfuse-egress',APP+'-langfuse-reader','checksum/langfuse'):
            self.assertNotIn(marker,text)

    def test_config_changes_restart_only_api_and_preserve_other_checksums(self):
        before=render(settings()); altered=settings(); altered['apps'][APP]['projectId']='reviewed-new-project'
        after=render(altered)
        a=named(before,'Deployment','hopcharlie-api'); b=named(after,'Deployment','hopcharlie-api')
        aa=a['spec']['template']['metadata']['annotations']; bb=b['spec']['template']['metadata']['annotations']
        self.assertRegex(aa['checksum/langfuse'],r'^[a-f0-9]{64}$')
        self.assertNotEqual(aa.pop('checksum/langfuse'),bb.pop('checksum/langfuse'))
        self.assertEqual(aa,bb)
        self.assertEqual(a['spec']['strategy'],{'type':'Recreate'})
        self.assertEqual(a['spec']['replicas'],1)
        self.assertEqual(a['spec']['template']['spec'],b['spec']['template']['spec'])
        for name in ('hopcharlie-gateway','hopcharlie-console'):
            self.assertEqual(named(before,'Deployment',name),named(after,'Deployment',name))
        self.assertEqual(named(before,'StatefulSet','hopcharlie-postgres'),named(after,'StatefulSet','hopcharlie-postgres'))

    def test_egress_selects_only_api_and_exact_web_port(self):
        policy=named(render(settings()),'NetworkPolicy','hopcharlie-app-langfuse-egress')['spec']
        self.assertEqual(policy,{'podSelector':{'matchLabels':{'app.kubernetes.io/part-of':'hopcharlie','app.kubernetes.io/component':'api'}},
            'policyTypes':['Egress'],'egress':[{'to':[{'namespaceSelector':{'matchLabels':{'kubernetes.io/metadata.name':'langfuse'}},
            'podSelector':{'matchLabels':{'app.kubernetes.io/name':'langfuse','app.kubernetes.io/instance':'langfuse','app.kubernetes.io/component':'web'}}}],
            'ports':[{'protocol':'TCP','port':3000}]}]})

    def test_invalid_binding_urls_and_raw_keys_rejected(self):
        cases=[]
        for field,value in [('projectId','https://invalid'),('metadataKey','invalid/key'),('secretName','hopcharlie-hopai-langfuse'),('publicKey','fixture-not-for-values')]:
            config=settings(); config['apps'][APP][field]=value; cases.append(config)
        # Helm's map merge preserves a management app for apps={}; null removes it.
        cases += [dict(enabled=True,baseUrl=URL,apps=None),dict(enabled=True,baseUrl=URL,apps={'../other':settings()['apps'][APP]}),
                  dict(enabled=True,baseUrl='https://external.example',apps=settings()['apps'])]
        for config in cases:
            with self.subTest(config=config),self.assertRaises(ValueError): render(config)


if __name__=='__main__': unittest.main()
