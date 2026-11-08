#!/usr/bin/env python3
import json,subprocess,tempfile,unittest
from pathlib import Path
import yaml
ROOT=Path(__file__).resolve().parents[2]
class GCEWebRoute(unittest.TestCase):
 def render(self,values,namespace='hopcharlie-db-network'):
  with tempfile.NamedTemporaryFile(mode='w',suffix='.json') as f:
   json.dump(values,f);f.flush();return subprocess.run(['helm','template','route',str(ROOT/'helm/charts/gce-app-web-route'),'-n',namespace,'-f',f.name],capture_output=True,text=True)
 def test_exact_management_route_and_name_boundary(self):
  for name in ['demo','a'*48]:
   p=self.render({'namespace':name,'targetFQDN':'example.tail1234.ts.net'});self.assertEqual(p.returncode,0,p.stderr);o=yaml.safe_load(p.stdout);self.assertEqual(o['metadata']['name'],'app-onprem-gce-'+name);self.assertLessEqual(len(o['metadata']['name']),63);self.assertEqual(o['spec'],{'type':'ExternalName','externalName':'placeholder','ports':[{'name':'http','protocol':'TCP','port':80}]});self.assertEqual(o['metadata']['annotations']['tailscale.com/proxy-class'],'hopcharlie-gce-web-egress')
 def test_invalid_target_namespace_and_oversize_name_rejected(self):
  for values,ns in [({'namespace':'a'*49,'targetFQDN':'example.tail1234.ts.net'},'hopcharlie-db-network'),({'namespace':'demo','targetFQDN':'example.com'},'hopcharlie-db-network'),({'namespace':'demo','targetFQDN':'example.tail1234.ts.net'},'kube-system')]:self.assertNotEqual(self.render(values,ns).returncode,0)
 def test_profile_uses_ingress_without_other_onprem_annotations(self):
  p=json.loads((ROOT/'clusters/onprem-dev-workload/values/apps.yaml').read_text())['environments']['onprem-gce']['profile'];self.assertEqual(p['publicService']['annotations'],{});self.assertEqual(p['ingress']['className'],'traefik');self.assertIn('{namespace}',p['ingress']['hostTemplate']);self.assertEqual(p['postgres']['storageClass'],'onprem-local-retain')
if __name__=='__main__':unittest.main()
