#!/usr/bin/env python3
"""Render the GCE storage boundary and optional platform credential inputs."""
import copy,json,subprocess,tempfile,unittest
from pathlib import Path
import yaml
ROOT=Path(__file__).resolve().parents[2]
class GCEStorage(unittest.TestCase):
 def render(self,chart,values,namespace='minio-gce',ok=True):
  with tempfile.NamedTemporaryFile(mode='w',suffix='.json') as f:
   json.dump(values,f);f.flush()
   p=subprocess.run(['helm','template','test',str(ROOT/'helm/charts'/chart),'-n',namespace,'-f',f.name],capture_output=True,text=True)
  if not ok:self.assertNotEqual(p.returncode,0);return []
  self.assertEqual(p.returncode,0,p.stderr);return list(filter(None,yaml.safe_load_all(p.stdout)))
 def test_minio_only_binds_tailnet_and_retains_nonroot_data(self):
  values={'owner':'gce-ready-aaaaaaaaaa','tailIP':'100.64.0.1','image':'525091405886.dkr.ecr.ap-northeast-2.amazonaws.com/hopcharlie/apps/gce-storage/minio@sha256:'+'a'*64}
  docs=self.render('gce-workload-deployment',values);pod=next(x for x in docs if x['kind']=='Deployment')['spec']['template']['spec'];container=pod['containers'][0]
  self.assertFalse(pod['automountServiceAccountToken']);self.assertTrue(pod['securityContext']['runAsNonRoot']);self.assertEqual(container['ports'],[{'name':'s3','containerPort':9000,'hostPort':9000,'hostIP':'100.64.0.1'}]);self.assertTrue(container['securityContext']['readOnlyRootFilesystem'])
  self.assertEqual(next(x for x in docs if x['kind']=='PersistentVolumeClaim')['spec']['storageClassName'],'onprem-local-retain')
  self.assertFalse(any(x['kind'] in ['Secret','Ingress','Role','ClusterRole'] for x in docs))
  for patch in [{'tailIP':'0.0.0.0'},{'image':values['image'].split('@')[0]+':latest'},{'owner':''}]:
   self.render('gce-workload-deployment',{**values,**patch},ok=False)
  self.render('gce-workload-deployment',values,namespace='minio',ok=False)
 def test_credentials_are_optional_and_reference_only_dedicated_ssm(self):
  base=json.loads((ROOT/'clusters/aws-dev-management/values/hopcharlie-platform.yaml').read_text())
  for enabled in [False,True]:
   values=copy.deepcopy(base);values['api']['gceStorage']={'enabled':enabled};docs=self.render('hopcharlie-platform',values,'charlie-platform');script=next(x for x in docs if x['kind']=='ConfigMap' and x['metadata']['name']=='hopcharlie-fetch-settings')['data']['fetch-settings.sh']
   self.assertEqual('GCE_MINIO_ADMIN_ACCESS_KEY' in script,enabled)
   if enabled:
    self.assertIn('get onprem-gce/minio-admin-access-key',script);self.assertIn('get onprem-gce/minio-admin-secret-key',script);self.assertIn('^[a-f0-9]{32}$',script);self.assertIn('^[a-f0-9]{64}$',script)
   self.assertFalse(any(x['kind']=='Secret' for x in docs))
 def test_gce_profile_uses_same_minio_contract_with_separate_namespace(self):
  values=json.loads((ROOT/'clusters/onprem-dev-workload/values/apps.yaml').read_text());self.assertEqual(set(values['environments']),{'onprem-gce'});p=values['environments']['onprem-gce']['profile'];self.assertEqual(p['namespaceLabels'],{'charlie.dev/workload-target':'onprem-gce'});self.assertEqual(p['s3'],{'endpoint':'http://minio.minio-gce.svc.cluster.local:9000','region':'us-east-1','pathStyle':True,'bucketPrefix':'hop-gce-','credentialsSecret':'app-s3'})
  original=json.loads((ROOT/'clusters/aws-dev-management/values/hopcharlie-apps.yaml').read_text())['environments']['onprem']['profile'];self.assertEqual(p['s3']['region'],original['s3']['region']);self.assertEqual(p['s3']['pathStyle'],original['s3']['pathStyle']);self.assertNotEqual(p['s3']['endpoint'],original['s3']['endpoint'])
if __name__=='__main__':unittest.main()
