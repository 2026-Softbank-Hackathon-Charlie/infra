#!/usr/bin/env python3
"""Verify offline GCP federation manifests and the pinned Argo CD chart."""
import base64, copy, importlib.util, json, os, shutil, subprocess, tempfile, unittest
from pathlib import Path
import yaml
ROOT = Path(__file__).resolve().parents[2]
HELM = os.environ.get('HELM', 'helm')
PROVIDER = 'projects/123456789/locations/global/workloadIdentityPools/argocd/providers/eks'
EMAIL = 'argocd-gke@example-project.iam.gserviceaccount.com'

def inputs():
    return {'workload_identity_provider': PROVIDER, 'projected_token_audience': 'https://iam.googleapis.com/' + PROVIDER,
        'deploy_service_account_email': EMAIL, 'kubernetes_service_account_subjects': ['system:serviceaccount:argocd:' + sa for sa in ['argocd-application-controller', 'argocd-server']],
        'federation_config': {'type': 'external_account', 'audience': '//iam.googleapis.com/' + PROVIDER,
            'subject_token_type': 'urn:ietf:params:oauth:token-type:jwt', 'token_url': 'https://sts.googleapis.com/v1/token',
            'service_account_impersonation_url': 'https://iamcredentials.googleapis.com/v1/projects/-/serviceAccounts/' + EMAIL + ':generateAccessToken',
            'credential_source': {'file': '/var/run/secrets/gcp/token'}}}

def metadata():
    return {'name': 'gke-test', 'endpoint': '203.0.113.5',
        'masterAuth': {'clusterCaCertificate': base64.b64encode(b'-----BEGIN CERTIFICATE-----\ndGVzdA==\n-----END CERTIFICATE-----\n').decode()}}

class ArgoGCPTests(unittest.TestCase):
    def setUp(self):
        spec = importlib.util.spec_from_file_location('prepare_argocd_gcp', ROOT / 'scripts/prepare-argocd-gcp.py')
        self.prepare = importlib.util.module_from_spec(spec)
        spec.loader.exec_module(self.prepare)

    def test_output_preserves_aws_and_has_no_static_token(self):
        original = {'argo-cd': {'controller': {'env': [{'name': 'AWS_REGION', 'value': 'ap-northeast-2'}],
            'volumes': [{'name': 'aws-token', 'emptyDir': {}}], 'volumeMounts': [{'name': 'aws-token', 'mountPath': '/var/run/aws'}]}}}
        before = copy.deepcopy(original)
        out = self.prepare.prepare(inputs(), metadata(), original)
        self.assertEqual(before, original)
        controller = out['bootstrap-overlay.json']['argo-cd']['controller']
        self.assertIn(original['argo-cd']['controller']['volumes'][0], controller['volumes'])
        self.assertIn(original['argo-cd']['controller']['env'][0], controller['env'])
        for part, name in [('controller', 'argocd-application-controller'), ('server', 'argocd-server')]:
            component = out['bootstrap-overlay.json']['argo-cd'][part]
            self.assertEqual(component['serviceAccount']['name'], name)
            jwt = next(v for v in component['volumes'] if v['name'] == 'gcp-identity-token')['projected']['sources'][0]['serviceAccountToken']
            self.assertEqual(jwt['audience'], inputs()['projected_token_audience'])
            self.assertEqual(jwt['expirationSeconds'], 3600)
        secret = next(x for x in out['management-manifests.json']['items'] if x['kind'] == 'Secret')
        auth = json.loads(secret['stringData']['config'])
        self.assertEqual(auth['execProviderConfig']['command'], 'argocd-k8s-auth')
        self.assertNotIn('bearerToken', auth)
        self.assertEqual(secret['stringData']['namespaces'], 'charlie-apps')
        self.assertEqual(secret['stringData']['clusterResources'], 'false')
        self.assertFalse(auth['tlsClientConfig']['insecure'])
        self.assertEqual(self.prepare.prepare(inputs(), metadata(), out['bootstrap-overlay.json']), out)

    def test_identity_mismatch(self):
        for key, value in [('projected_token_audience', 'https://other.example'), ('kubernetes_service_account_subjects', ['system:serviceaccount:default:any'])]:
            cfg = inputs(); cfg[key] = value
            with self.assertRaises(ValueError): self.prepare.prepare(cfg, metadata(), {})
        cfg = inputs(); cfg['federation_config']['credential_source']['file'] = '/tmp/untrusted'
        with self.assertRaises(ValueError): self.prepare.prepare(cfg, metadata(), {})

    def test_unsafe_endpoint_and_ca(self):
        for endpoint in ['http://bad.test', 'user@bad.test', 'bad.test/path', 'bad.test:443', '']:
            cluster = metadata(); cluster['endpoint'] = endpoint
            with self.assertRaises(ValueError): self.prepare.prepare(inputs(), cluster, {})
        cluster = metadata(); cluster['masterAuth']['clusterCaCertificate'] = 'invalid!'
        with self.assertRaises(ValueError): self.prepare.prepare(inputs(), cluster, {})

    def test_credentials_rejected(self):
        cfg = {k: {'value': v, 'sensitive': False} for k, v in inputs().items()}
        cfg['federation_config']['sensitive'] = True
        with self.assertRaises(ValueError): self.prepare.prepare(cfg, metadata(), {})
        with self.assertRaises(ValueError):
            self.prepare.prepare(inputs(), metadata(), {'argo-cd': {'controller': {'env': [{'name': 'AWS_SESSION_TOKEN', 'value': 'do-not-copy'}]}}})
        cfg = inputs(); cfg['federation_config']['private_key'] = 'do-not-copy'
        with self.assertRaises(ValueError): self.prepare.prepare(cfg, metadata(), {})

    def test_existing_name_conflicts(self):
        for current in [{'volumes': [{'name': 'gcp-identity-token', 'emptyDir': {}}]}, {'serviceAccount': {'name': 'other'}}]:
            with self.assertRaises(ValueError): self.prepare.prepare(inputs(), metadata(), {'argo-cd': {'controller': current}})

    def test_offline_cli(self):
        with tempfile.TemporaryDirectory() as directory:
            root = Path(directory)
            for name, data in [('outputs', inputs()), ('gke', metadata()), ('values', {})]: (root / (name + '.json')).write_text(json.dumps(data))
            result = subprocess.run([os.environ.get('PYTHON', 'python'), str(ROOT / 'scripts/prepare-argocd-gcp.py'), '--terraform-outputs', str(root/'outputs.json'), '--gke-metadata', str(root/'gke.json'), '--bootstrap-values', str(root/'values.json'), '--output-dir', str(root/'out')], capture_output=True, text=True)
            self.assertEqual(result.returncode, 0, result.stderr)
            self.assertEqual({p.name for p in (root/'out').iterdir()}, {'bootstrap-overlay.json', 'management-manifests.json', 'gke-rbac-values.json'})
            self.assertIn('Offline', result.stdout)

    def test_rbac_namespace_edit_not_admin(self):
        with tempfile.TemporaryDirectory() as directory:
            values = Path(directory)/'values.json'; values.write_text(json.dumps(self.prepare.prepare(inputs(), metadata(), {})['gke-rbac-values.json']))
            result = subprocess.run([HELM, 'template', 'gcp-argocd-access', str(ROOT/'helm/charts/gcp-argocd-access'), '--namespace', 'charlie-apps', '--kube-version', '1.36.0', '-f', str(values)], capture_output=True, text=True)
            self.assertEqual(result.returncode, 0, result.stderr)
            docs = [d for d in yaml.safe_load_all(result.stdout) if d]
        self.assertEqual({d['kind'] for d in docs}, {'ClusterRoleBinding', 'RoleBinding'})
        self.assertEqual(next(d for d in docs if d['kind']=='ClusterRoleBinding')['roleRef']['name'], 'view')
        binding = next(d for d in docs if d['kind']=='RoleBinding')
        self.assertEqual(binding['metadata']['namespace'], 'charlie-apps')
        self.assertEqual(binding['roleRef']['name'], 'edit')
        self.assertEqual(binding['subjects'][0]['name'], EMAIL)

    def test_actual_pinned_bootstrap_overlay(self):
        chart = os.environ.get('ARGO_BOOTSTRAP_CHART')
        package = ROOT/'.generated/argocd-test/argo-cd-10.9.6.tgz'
        if not chart and not package.is_file(): self.skipTest('Pinned dependency rendered in Helm CI')
        with tempfile.TemporaryDirectory() as directory:
            temp = Path(directory)
            if not chart:
                chart = temp/'bootstrap'; shutil.copytree(ROOT/'helm/bootstrap', chart)
                (chart/'charts').mkdir(exist_ok=True); shutil.copyfile(package, chart/'charts/argo-cd-10.9.6.tgz')
            current = json.loads((ROOT/'helm/bootstrap/values.yaml').read_text())
            current['argo-cd']['controller']['volumes'] = [{'name':'aws-token','emptyDir':{}}]
            current['argo-cd']['controller']['volumeMounts'] = [{'name':'aws-token','mountPath':'/var/run/aws'}]
            for name, data in [('current.json',current), ('overlay.json',self.prepare.prepare(inputs(), metadata(), current)['bootstrap-overlay.json'])]: (temp/name).write_text(json.dumps(data))
            result = subprocess.run([HELM,'template','argocd',str(chart),'--namespace','argocd','--kube-version','1.35.0','-f',str(temp/'current.json'),'-f',str(temp/'overlay.json')],capture_output=True,text=True)
            self.assertEqual(result.returncode,0,result.stderr)
            docs=[d for d in yaml.safe_load_all(result.stdout) if d]
        for name in ['argocd-application-controller','argocd-server']:
            pod=next(d for d in docs if d['kind'] in ['Deployment','StatefulSet'] and d['metadata']['name']==name)['spec']['template']['spec']
            self.assertEqual(pod['serviceAccountName'],name)
            self.assertTrue(any(v['name']=='gcp-identity-token' for v in pod['volumes']))
            self.assertIn({'name':'GOOGLE_APPLICATION_CREDENTIALS','value':'/etc/argocd-gcp/credential-configuration.json'},pod['containers'][0]['env'])
            if name=='argocd-application-controller': self.assertTrue(any(v['name']=='aws-token' for v in pod['volumes']))

if __name__ == '__main__': unittest.main()
