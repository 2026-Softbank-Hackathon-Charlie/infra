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

    def test_dynamic_registration_changes_cache_without_changing_auth(self):
        legacy = self.prepare.prepare(inputs(), metadata(), {})
        stage1 = self.prepare.prepare(inputs(), metadata(), {}, dynamic_apps=True)
        self.assertEqual(stage1['management-manifests.json'], legacy['management-manifests.json'])
        self.assertEqual(stage1['bootstrap-overlay.json'], legacy['bootstrap-overlay.json'])
        self.assertEqual(stage1['gke-rbac-values.json']['dynamicApps'], {'enabled': True, 'grantAccess': False})
        dynamic = self.prepare.prepare(inputs(), metadata(), {}, dynamic_apps=True, grant_access=True)
        old_cluster = next(d for d in legacy['management-manifests.json']['items'] if d['kind'] == 'Secret')
        cluster = next(d for d in dynamic['management-manifests.json']['items'] if d['kind'] == 'Secret')
        self.assertEqual(cluster['stringData'], {**old_cluster['stringData'], 'namespaces': '', 'clusterResources': 'true'})
        self.assertEqual(dynamic['management-manifests.json']['items'][0], legacy['management-manifests.json']['items'][0])
        for component in ('controller', 'server'):
            self.assertEqual(dynamic['bootstrap-overlay.json']['argo-cd'][component], legacy['bootstrap-overlay.json']['argo-cd'][component])
        self.assertEqual(dynamic['bootstrap-overlay.json']['argo-cd']['configs'], {'cm': {'resource.respectRBAC': 'normal'}})
        self.assertEqual(dynamic['gke-rbac-values.json'], {**legacy['gke-rbac-values.json'], 'dynamicApps': {'enabled': True, 'grantAccess': True}})
        self.assertEqual(self.prepare.prepare(inputs(), metadata(), dynamic['bootstrap-overlay.json'], dynamic_apps=True, grant_access=True), dynamic)

    def test_dynamic_rbac_is_bounded_and_admission_fails_closed(self):
        with tempfile.TemporaryDirectory() as directory:
            values = Path(directory) / 'values.json'
            values.write_text(json.dumps(self.prepare.prepare(inputs(), metadata(), {}, dynamic_apps=True, grant_access=True)['gke-rbac-values.json']))
            result = subprocess.run([HELM, 'template', 'gcp-argocd-access', str(ROOT/'helm/charts/gcp-argocd-access'), '--namespace', 'charlie-apps', '--kube-version', '1.35.0', '-f', str(values)], capture_output=True, text=True, encoding='utf-8')
            self.assertEqual(result.returncode, 0, result.stderr)
            docs = [d for d in yaml.safe_load_all(result.stdout) if d]
        self.assertEqual(len(docs), 6)
        role = next(d for d in docs if d['kind'] == 'ClusterRole')
        expected = {('', 'namespaces'), ('', 'services'), ('', 'resourcequotas'), ('', 'limitranges'), ('apps', 'deployments'), ('postgresql.cnpg.io', 'clusters'), ('networking.k8s.io', 'networkpolicies'), ('bitnami.com', 'sealedsecrets')}
        actual = {(group, resource) for rule in role['rules'] for group in rule['apiGroups'] for resource in rule['resources']}
        self.assertEqual(actual, expected)
        self.assertTrue(all(set(rule['verbs']) == {'get', 'list', 'watch', 'create', 'update', 'patch', 'delete'} for rule in role['rules']))
        bindings = [d for d in docs if d['kind'] == 'ClusterRoleBinding']
        self.assertEqual({b['roleRef']['name'] for b in bindings}, {'view', role['metadata']['name']})
        self.assertTrue(all(b['subjects'] == [{'kind': 'User', 'apiGroup': 'rbac.authorization.k8s.io', 'name': EMAIL}] for b in bindings))
        policy = next(d for d in docs if d['kind'] == 'ValidatingAdmissionPolicy')
        binding = next(d for d in docs if d['kind'] == 'ValidatingAdmissionPolicyBinding')
        self.assertEqual(policy['spec']['failurePolicy'], 'Fail')
        self.assertEqual(policy['spec']['matchConditions'], [{'name': 'argocd-google-identity', 'expression': "request.userInfo.username == '" + EMAIL + "'"}])
        self.assertNotIn('namespaceSelector', policy['spec']['matchConstraints'])
        self.assertEqual(binding['spec'], {'policyName': policy['metadata']['name'], 'validationActions': ['Deny']})
        self.assertTrue(all(set(rule['operations']) == {'CREATE', 'UPDATE', 'DELETE'} for rule in policy['spec']['matchConstraints']['resourceRules']))
        policy_resources = {(group, resource) for rule in policy['spec']['matchConstraints']['resourceRules'] for group in rule['apiGroups'] for resource in rule['resources']}
        self.assertEqual(policy_resources, expected, 'Every resource with write access must be covered by the ownership policy')
        variables = {v['name']: v['expression'] for v in policy['spec']['variables']}
        denied = yaml.safe_load((ROOT/'helm/charts/hopcharlie-apps/values.yaml').read_text())['deniedNamespaces']
        self.assertEqual(set(json.loads(variables['protectedNamespaces'])), set(denied))
        self.assertFalse(any('namespaceObject' in expression for expression in variables.values()))
        expressions = '\n'.join(v['expression'] for v in policy['spec']['validations'])
        for guard in ('oldObject.metadata.labels', "'hopcharlie.dev/app'", "'hopcharlie.dev/instance'", 'namespaceObject != null', "'pod-security.kubernetes.io/enforce'", "== 'restricted'", "'pod-security.kubernetes.io/enforce-version'", "== 'latest'"):
            self.assertIn(guard, expressions)
        self.assertIn('<= 53', variables['ownedNamespace'])
        self.assertIn("+ '--'", variables['ownedNamespace'])

    def test_dynamic_mode_cannot_create_baseline_namespaces(self):
        with tempfile.TemporaryDirectory() as directory:
            config = self.prepare.prepare(inputs(), metadata(), {}, dynamic_apps=True)['gke-rbac-values.json']
            config['createNamespaces'] = True
            values = Path(directory) / 'values.json'; values.write_text(json.dumps(config))
            result = subprocess.run([HELM, 'template', 'gcp-argocd-access', str(ROOT/'helm/charts/gcp-argocd-access'), '-f', str(values)], capture_output=True, text=True, encoding='utf-8')
            self.assertNotEqual(result.returncode, 0)

    def test_dynamic_policy_stage_does_not_grant_write_access(self):
        with tempfile.TemporaryDirectory() as directory:
            config = self.prepare.prepare(inputs(), metadata(), {}, dynamic_apps=True)['gke-rbac-values.json']
            values = Path(directory) / 'values.json'; values.write_text(json.dumps(config))
            result = subprocess.run([HELM, 'template', 'gcp-argocd-access', str(ROOT/'helm/charts/gcp-argocd-access'), '-f', str(values)], capture_output=True, text=True, encoding='utf-8')
            self.assertEqual(result.returncode, 0, result.stderr)
            docs = [d for d in yaml.safe_load_all(result.stdout) if d]
        self.assertEqual(len(docs), 5)
        self.assertEqual([d['roleRef']['name'] for d in docs if d['kind'] == 'ClusterRoleBinding'], ['view'])
        self.assertTrue(any(d['kind'] == 'ValidatingAdmissionPolicyBinding' for d in docs))

    def test_dynamic_grant_requires_policy_mode(self):
        with self.assertRaises(ValueError):
            self.prepare.prepare(inputs(), metadata(), {}, grant_access=True)
        with tempfile.TemporaryDirectory() as directory:
            config = self.prepare.prepare(inputs(), metadata(), {})['gke-rbac-values.json']
            config['dynamicApps'] = {'enabled': False, 'grantAccess': True}
            values = Path(directory) / 'values.json'; values.write_text(json.dumps(config))
            result = subprocess.run([HELM, 'template', 'gcp-argocd-access', str(ROOT/'helm/charts/gcp-argocd-access'), '-f', str(values)], capture_output=True, text=True, encoding='utf-8')
            self.assertNotEqual(result.returncode, 0)

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
