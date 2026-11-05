#!/usr/bin/env python3
"""AWS 기본 chart를 임시 디렉터리에서 lint/render하고 GitOps·이미지 계약을 검사합니다."""
import copy
import json
import os
from pathlib import Path
import re
import shutil
import subprocess
import sys
import tempfile

import yaml

ROOT = Path(__file__).resolve().parents[1]
HELM = os.environ.get("HELM", "helm")
VERSIONS = json.loads((ROOT / "helm/versions.json").read_text())
LOCK = json.loads((ROOT / "helm/images.lock.json").read_text())["images"]
yaml.SafeLoader.add_constructor("tag:yaml.org,2002:value", lambda loader, node: loader.construct_scalar(node))
CLUSTER_KINDS = {"Namespace", "StorageClass", "ClusterRole", "ClusterRoleBinding", "CustomResourceDefinition", "MutatingWebhookConfiguration", "ValidatingWebhookConfiguration", "ValidatingAdmissionPolicy", "ValidatingAdmissionPolicyBinding", "APIService", "IngressClass", "IngressClassParams", "PriorityClass", "PersistentVolume", "CSIDriver"}


def helm(*args):
    return subprocess.check_output([HELM, *map(str, args)], text=True)


def render(chart, values, release="check", namespace="kube-system", extra=()):
    args = ["--kube-version", VERSIONS["kubernetes"] + ".0", "--namespace", namespace, "-f", values, *extra]
    helm("lint", "--strict", chart, *args)
    docs = [d for d in yaml.safe_load_all(helm("template", release, chart, "--include-crds", *args)) if d]
    docs = [item for d in docs for item in (d.get("items", []) if d["kind"] == "List" else [d])]
    assert docs, f"Empty chart: {chart}"
    return docs


def image_refs(value):
    if isinstance(value, dict):
        for key, child in value.items():
            if key == "image" and isinstance(child, str):
                yield child
            yield from image_refs(child)
    elif isinstance(value, list):
        for child in value:
            yield from image_refs(child)


def check_images(docs):
    refs = set(image_refs(docs))
    assert refs, "No container images rendered"
    for ref in refs:
        assert re.fullmatch(r".+@sha256:[a-f0-9]{64}", ref), f"Unpinned image: {ref}"
        name, digest = ref.rsplit("@", 1)
        assert LOCK.get(name) == digest, f"Image differs from lock: {name}"
    return refs


def check_project(docs, project, namespace):
    allowed = {(d["group"], d["kind"]) for d in project["spec"]["clusterResourceWhitelist"]}
    allowed_namespaces = {d["namespace"] for d in project["spec"]["destinations"]}
    for doc in docs:
        if doc["kind"] in CLUSTER_KINDS:
            pair = (doc["apiVersion"].rpartition("/")[0], doc["kind"])
            assert pair in allowed, f"AppProject denies {pair}"
        else:
            assert doc["metadata"].get("namespace", namespace) in allowed_namespaces, doc["metadata"]


def check_gke_image_settings(platform_values, work):
    chart = ROOT / "helm/charts/hopcharlie-platform"
    values = json.loads(platform_values.read_text())
    registry = values["deploy"]["gkeImageRegistry"]
    adc = values["deploy"]["gkeImageCredentialConfig"]
    disabled = copy.deepcopy(values)
    for key in ("gkeImageRegistry", "gkeImageCredentialConfig"):
        disabled["deploy"].pop(key)
    together = copy.deepcopy(values)
    together["gcp"] = {
        "credentialConfig": {"type": "external_account", "credential_source": {"file": "/var/run/secrets/gcp/token"}},
        "tokenAudience": "https://iam.googleapis.com/projects/1/locations/global/workloadIdentityPools/storage/providers/eks",
    }
    for name, case, enabled in (("enabled", values, True), ("disabled", disabled, False), ("with-storage", together, True)):
        path = work / ("platform-build-" + name + ".json")
        path.write_text(json.dumps(case))
        docs = render(chart, path, "hopcharlie-platform", "charlie-platform")
        assert not any(d["kind"] == "Secret" for d in docs), "Build ADC has no key or token"
        for doc in docs:
            if doc["kind"] not in ("Deployment", "StatefulSet"):
                continue
            env = {e["name"]: e.get("value") for c in doc["spec"]["template"]["spec"]["containers"] for e in c.get("env", [])}
            expected = enabled and doc["metadata"]["name"] == "hopcharlie-api"
            assert ("PLATFORM_GKE_IMAGE_REGISTRY" in env) == expected, doc["metadata"]["name"]
            assert ("PLATFORM_GKE_IMAGE_CREDENTIAL_CONFIG" in env) == expected, doc["metadata"]["name"]
            if expected:
                assert env["PLATFORM_GKE_IMAGE_REGISTRY"] == registry
                assert json.loads(env["PLATFORM_GKE_IMAGE_CREDENTIAL_CONFIG"]) == adc
                if name == "with-storage":
                    assert env["GOOGLE_APPLICATION_CREDENTIALS"] == "/etc/gcp/adc.json"
        if name == "with-storage":
            cm = next(d for d in docs if d["kind"] == "ConfigMap" and d["metadata"]["name"] == "hopcharlie-gcp-adc")
            assert json.loads(cm["data"]["adc.json"]) == together["gcp"]["credentialConfig"]
    for i, (patch, message) in enumerate((
        ({"gkeImageRegistry": None}, "go together"),
        ({"gkeImageCredentialConfig": None}, "go together"),
        ({"builder": "local"}, "deploy.builder codebuild"),
        ({"gkeImageCredentialConfig": {"type": "service_account"}}, "aws1"),
        ({"gkeImageCredentialConfig": {"type": "external_account", "credential_source": {"file": "/var/run/secrets/gcp/token"}}}, "aws1"),
    )):
        case = copy.deepcopy(values)
        case["deploy"].update(patch)
        path = work / f"platform-build-invalid-{i}.json"
        path.write_text(json.dumps(case))
        result = subprocess.run([HELM, "template", "hopcharlie-platform", str(chart), "-f", str(path)], capture_output=True, text=True, encoding="utf-8")
        assert result.returncode != 0 and message in result.stderr, result.stderr


def check_system_network_policy():
    chart = ROOT / "helm/charts/cluster-baseline"
    expected = {"kube-system", "observability", "cnpg-system", "cert-manager", "traefik"}
    with tempfile.TemporaryDirectory(prefix="charlie-system-policy-") as temp:
        work = Path(temp)
        projects = render(ROOT / "helm/gitops", ROOT / "helm/gitops/ci/targets-values.yaml", "charlie-addons", "argocd")
        for purpose in ("management", "workload"):
            values_path = ROOT / f"clusters/aws-dev-{purpose}/values/baseline.yaml"
            namespace = "charlie-platform" if purpose == "management" else "charlie-apps"
            docs = render(chart, values_path, "baseline", namespace)
            policies = [d for d in docs if d["kind"] == "NetworkPolicy" and d["metadata"]["name"] == "system-compatibility"]
            assert {d["metadata"]["namespace"] for d in policies} == (expected if purpose == "workload" else set()), purpose
            assert len(policies) == (len(expected) if purpose == "workload" else 0)
            for policy in policies:
                assert policy["spec"] == {"podSelector": {}, "policyTypes": ["Ingress", "Egress"], "ingress": [{}], "egress": [{}]}, policy
            project = next(d for d in projects if d["kind"] == "AppProject" and d["metadata"]["name"] == f"charlie-addons-{purpose}")
            destinations = {d["namespace"] for d in project["spec"]["destinations"]}
            assert "*" not in destinations and not any("sandbox" in d for d in destinations), destinations
            assert (expected <= destinations) if purpose == "workload" else not ({"cnpg-system", "cert-manager"} & destinations)
            check_project(docs, project, namespace)
            # Opting into system compatibility must leave application/DB rules identical.
            values = json.loads(values_path.read_text())
            values.setdefault("networkPolicy", {})["systemCompatibilityNamespaces"] = []
            disabled = work / f"{purpose}.json"
            disabled.write_text(json.dumps(values))
            assert [d for d in docs if d not in policies] == render(chart, disabled, "baseline", namespace)
        defaults = render(chart, chart / "values.yaml")
        assert not any(d["metadata"]["name"] == "system-compatibility" for d in defaults)
        for invalid in (["charlie-apps"], ["charlie-db"], ["sandbox-test"], ["*"], ["kube-system", "kube-system"]):
            values = work / "invalid.json"
            values.write_text(json.dumps({"networkPolicy": {"systemCompatibilityNamespaces": invalid}}))
            result = subprocess.run([HELM, "template", "baseline", str(chart), "-f", str(values)], capture_output=True, text=True)
            assert result.returncode != 0 and "systemCompatibilityNamespaces" in result.stderr, result.stderr
    print("System NetworkPolicy checks passed: exact namespaces, management opt-out, unchanged app policies, AppProject permissions and unsafe input rejection.")


def check_traefik(docs):
    """The shared app entry of the workload cluster (ADR 0058): one internal NLB with a
    fixed name that Argo CD never deletes, an IngressClass apps must ask for by name,
    and no CRD provider, dashboard or public TLS port."""
    services = {d["metadata"]["name"]: d for d in docs if d["kind"] == "Service"}
    assert sorted(services) == ["traefik", "traefik-no-route"], sorted(services)
    svc = services["traefik"]
    notes = svc["metadata"]["annotations"]
    assert notes["argocd.argoproj.io/sync-options"] == "Delete=false,Prune=false", notes
    assert notes["service.beta.kubernetes.io/aws-load-balancer-name"] == "hopcharlie-apps-eks", notes
    assert notes["service.beta.kubernetes.io/aws-load-balancer-scheme"] == "internal", notes
    assert notes["service.beta.kubernetes.io/aws-load-balancer-nlb-target-type"] == "ip", notes
    # Traefik keeps serving for 20 s and drains for 30 s after SIGTERM; the NLB holds a
    # draining target at least that long and stops new connections once /ping fails.
    assert notes["service.beta.kubernetes.io/aws-load-balancer-target-group-attributes"] == "deregistration_delay.timeout_seconds=60", notes
    assert {k.rpartition("/")[2]: v for k, v in notes.items() if "healthcheck" in k} == {
        "aws-load-balancer-healthcheck-protocol": "http", "aws-load-balancer-healthcheck-port": "8080", "aws-load-balancer-healthcheck-path": "/ping",
        "aws-load-balancer-healthcheck-interval": "5", "aws-load-balancer-healthcheck-timeout": "3",
        "aws-load-balancer-healthcheck-healthy-threshold": "2", "aws-load-balancer-healthcheck-unhealthy-threshold": "2"}, notes
    assert svc["spec"]["type"] == "LoadBalancer" and svc["spec"]["loadBalancerSourceRanges"] == ["10.40.0.0/19"], svc["spec"]
    assert [(p["port"], p["targetPort"]) for p in svc["spec"]["ports"]] == [(80, "web")], svc["spec"]["ports"]
    classes = [d for d in docs if d["kind"] == "IngressClass"]
    assert [(c["metadata"]["name"], c["spec"]["controller"]) for c in classes] == [("traefik", "traefik.io/ingress-controller")], classes
    assert (classes[0]["metadata"].get("annotations") or {}).get("ingressclass.kubernetes.io/is-default-class", "false") == "false", classes
    args = next(d for d in docs if d["kind"] == "Deployment")["spec"]["template"]["spec"]["containers"][0]["args"]
    for arg in ("--providers.kubernetesingress.ingressClass=traefik", "--providers.kubernetesingress.ingressendpoint.publishedservice=traefik/traefik",
                "--entryPoints.web.forwardedHeaders.trustedIPs=10.40.0.0/16", "--entryPoints.web.forwardedHeaders.notAppendXForwardedFor=true",
                "--entryPoints.web.transport.lifeCycle.requestAcceptGraceTimeout=20s", "--entryPoints.web.transport.lifeCycle.graceTimeOut=30s",
                "--api.dashboard=false"):
        assert arg in args, arg
    assert not any("websecure" in a for a in args), args
    assert not any(a.startswith("--providers.kubernetescrd") for a in args), args
    # A host with no app Ingress gets 503 from a Service without endpoints, not
    # Traefik's 404, so the platform's probe of a missing route fails.
    assert "--providers.kubernetesingress.allowEmptyServices=true" in args, args
    catch_all = [d for d in docs if d["kind"] == "Ingress"]
    assert [(d["metadata"]["name"], d["spec"]) for d in catch_all] == [("traefik-no-route", {
        "ingressClassName": "traefik", "defaultBackend": {"service": {"name": "traefik-no-route", "port": {"number": 80}}}})], catch_all
    assert services["traefik-no-route"]["spec"]["selector"] == {"hopcharlie.dev/no-route": "none"}


def main():
    check_system_network_policy()
    assert helm("version", "--short").startswith("v" + VERSIONS["helm"]), "Use the pinned Helm version"
    subprocess.run([os.environ.get("PYTHON", "python3"), str(ROOT / "scripts/check-clickstack.py")], check=True)
    fixture = json.loads((ROOT / "helm/gitops/ci/targets-values.yaml").read_text())
    # Render the staged configuration and the OTel-enabled variant.
    with tempfile.TemporaryDirectory(prefix="charlie-helm-") as temp:
        work = Path(temp)
        helm_home = work / "helm-home"
        for name in ("HELM_CACHE_HOME", "HELM_CONFIG_HOME", "HELM_DATA_HOME"):
            os.environ[name] = str(helm_home / name)
        bootstrap = work / "bootstrap"
        shutil.copytree(ROOT / "helm/bootstrap", bootstrap)
        helm("repo", "add", "argo", VERSIONS["charts"]["argo-cd"]["repo"])
        helm("dependency", "build", bootstrap)
        bootstrap_docs = render(bootstrap, bootstrap / "values.yaml", "argocd", "argocd")
        assert any(d["kind"] == "Deployment" for d in bootstrap_docs)
        subprocess.run(
            [os.environ.get("PYTHON", "python3"), str(ROOT / "scripts/tests/test-argocd-gcp.py"),
             "ArgoGCPTests.test_actual_pinned_bootstrap_overlay"],
            env={**os.environ, "ARGO_BOOTSTRAP_CHART": str(bootstrap)}, check=True)
        seen = check_images(bootstrap_docs)
        projects = {}
        for enable_otel in (False, True):
            config = copy.deepcopy(fixture)
            if enable_otel:
                for addons in config["addons"].values():
                    addons.append("opentelemetry-collector")
            inputs = work / "targets.json"
            inputs.write_text(json.dumps(config))
            root_docs = render(ROOT / "helm/gitops", inputs, "charlie-addons", "argocd")
            assert {d["kind"] for d in root_docs} == {"Application", "AppProject"}
            for purpose in ("management", "workload"):
                projects[purpose] = next(d for d in root_docs if d["kind"] == "AppProject" and d["metadata"]["name"] == f"charlie-addons-{purpose}")
                apps = [d for d in root_docs if d["kind"] == "Application" and d["spec"]["project"] == f"charlie-addons-{purpose}"]
                # baseline, plus the HopCharlie platform on the management cluster
                assert len(apps) == len(config["addons"][purpose]) + (2 if purpose == "management" else 1)
                for app in apps:
                    assert app["spec"]["destination"]["server"] == fixture["targets"][purpose]["endpoint"]
                    assert app["spec"]["syncPolicy"]["automated"]["prune"] is False
                    for source in app["spec"].get("sources", []):
                        if "chart" in source:
                            pin = VERSIONS["charts"][source["chart"]]
                            assert source["repoURL"] == pin["repo"] and source["targetRevision"] == pin["version"]
                            # Only Traefik skips its CRDs (ADR 0058: the CRD provider is off).
                            assert source["helm"].get("skipCrds", False) == (source["chart"] == "traefik"), app["metadata"]["name"]
                    labels = app["spec"]["syncPolicy"].get("managedNamespaceMetadata", {}).get("labels")
                    if app["metadata"]["name"] == "charlie-workload-traefik":
                        assert app["spec"]["destination"]["namespace"] == "traefik"
                        assert labels == {"elbv2.k8s.aws/pod-readiness-gate-inject": "enabled"}, labels
                    else:
                        assert labels is None, app["metadata"]["name"]
        for purpose in ("management", "workload"):
            values_root = ROOT / f"clusters/aws-dev-{purpose}/values"
            baseline_ns = "charlie-platform" if purpose == "management" else "charlie-apps"
            docs = render(ROOT / "helm/charts/cluster-baseline", values_root / "baseline.yaml", "baseline", baseline_ns)
            assert {"Namespace", "StorageClass", "ResourceQuota", "LimitRange", "NetworkPolicy"} <= {d["kind"] for d in docs}
            assert not any(d["kind"] in {"ClusterRole", "ClusterRoleBinding"} for d in docs)
            # The only Ingress is the shared ALB anchor, and only where externalAlb is on.
            alb = json.loads((values_root / "baseline.yaml").read_text())["externalAlb"]
            ingresses = [d for d in docs if d["kind"] == "Ingress"]
            assert [d["metadata"]["name"] for d in ingresses] == ([f"{alb['groupName']}-anchor"] if alb["enabled"] else [])
            check_project(docs, projects[purpose], baseline_ns)
            for name in fixture["addons"][purpose] + ["opentelemetry-collector"]:
                pin = VERSIONS["charts"][name]
                chart_dir = work / "charts" / name
                if not chart_dir.exists():
                    chart_dir.mkdir(parents=True)
                    helm("pull", name, "--repo", pin["repo"], "--version", pin["version"], "--untar", "--untardir", chart_dir)
                chart = chart_dir / name
                namespace = "observability" if name in {"kube-prometheus-stack", "loki", "opentelemetry-collector"} else "traefik" if name == "traefik" else "kube-system"
                release = "monitoring" if name == "kube-prometheus-stack" else name
                extra = ("--set", f"clusterName=charlie-dev-{purpose}", "--set", "region=ap-northeast-2", "--set", "vpcId=vpc-0123456789abcdef0") if name == "aws-load-balancer-controller" else ()
                docs = render(chart, values_root / f"{name}.yaml", release, namespace, extra)
                check_project(docs, projects[purpose], namespace)
                seen.update(check_images(docs))
                if name == "traefik":
                    check_traefik(docs)
        # GKE sealed-secrets (ADR 0043): applied by hand, not Argo. It must keep
        # its own key, never renew it, and unseal in every namespace.
        docs = render(work / "charts/sealed-secrets/sealed-secrets", ROOT / "clusters/gcp-dev-workload/values/sealed-secrets.yaml", "sealed-secrets-gcp", "charlie-apps")
        seen.update(check_images(docs))
        args = next(d for d in docs if d["kind"] == "Deployment")["spec"]["template"]["spec"]["containers"][0]["args"]
        assert {"--key-prefix=sealed-secrets-gcp-key", "--key-renew-period=0"} <= set(args) and not any(a.startswith("--all-namespaces") for a in args), args
        cluster_rbac = {(d["kind"], d["metadata"]["name"]) for d in docs if d["kind"] in {"ClusterRole", "ClusterRoleBinding"}}
        assert cluster_rbac == {("ClusterRole", "secrets-unsealer"), ("ClusterRoleBinding", "sealed-secrets-gcp")}, cluster_rbac
        assert not any(d["metadata"]["name"].endswith("service-proxier") for d in docs)
        assert all(d["metadata"].get("namespace", "charlie-apps") == "charlie-apps" for d in docs if d["kind"] not in CLUSTER_KINDS)
        # HopCharlie platform: before and after the shared ALB is turned on.
        platform_values = ROOT / "clusters/aws-dev-management/values/hopcharlie-platform.yaml"
        for ingress in (False, True):
            extra = ("--set", f"ingress.enabled={str(ingress).lower()}")
            docs = render(ROOT / "helm/charts/hopcharlie-platform", platform_values, "hopcharlie-platform", "charlie-platform", extra)
            kinds = {d["kind"] for d in docs}
            assert {"StatefulSet", "Deployment", "Service", "NetworkPolicy", "ConfigMap", "ServiceAccount"} <= kinds
            assert ("Ingress" in kinds) == ingress
            if ingress:
                # The apps' wildcard to the gateway (ADR 0036); its order is checked against
                # every explicit host of the shared ALB group after Headlamp is rendered.
                ingresses = {d["metadata"]["name"]: d for d in docs if d["kind"] == "Ingress"}
                assert sorted(ingresses) == ["hopcharlie-apps", "hopcharlie-platform"], sorted(ingresses)
                alb_ingresses = list(ingresses.values())
                hosts = {r["host"]: r["http"]["paths"][0]["backend"]["service"] for d in ingresses.values() for r in d["spec"]["rules"]}
                assert hosts == {
                    "api.hopcharlie.dev": {"name": "hopcharlie-api", "port": {"number": 8080}},
                    "app.hopcharlie.dev": {"name": "hopcharlie-gateway", "port": {"number": 8000}},
                    "console.hopcharlie.dev": {"name": "hopcharlie-console", "port": {"number": 80}},
                    "*.hopcharlie.dev": {"name": "hopcharlie-gateway", "port": {"number": 8000}},
                }, hosts
            assert not any(d["kind"] == "Secret" for d in docs), "Platform secrets stay in SSM"
            check_project(docs, projects["management"], "charlie-platform")
            seen.update(check_images(docs))
            if not ingress:
                configured_gcp = json.loads(platform_values.read_text()).get("gcp", {})
                adc_maps = [d for d in docs if d["metadata"]["name"] == "hopcharlie-gcp-adc"]
                assert bool(adc_maps) == bool(configured_gcp.get("tokenAudience")), "Google ADC follows the configured storage identity"
                if adc_maps:
                    assert json.loads(adc_maps[0]["data"]["adc.json"]) == configured_gcp["credentialConfig"]
        check_gke_image_settings(platform_values, work)
        # Google ADC for the gcs storage driver (ADR 0041): only the API pod gets
        # the projected token; the gateway and Postgres share the service account.
        gcp_values = work / "platform-gcp.json"
        adc = {"type": "external_account", "audience": "//iam.googleapis.com/projects/1/locations/global/workloadIdentityPools/charlie-platform/providers/management-eks",
               "subject_token_type": "urn:ietf:params:oauth:token-type:jwt", "token_url": "https://sts.googleapis.com/v1/token",
               "service_account_impersonation_url": "https://iamcredentials.googleapis.com/v1/projects/-/serviceAccounts/platform-storage@charlie-test-project.iam.gserviceaccount.com:generateAccessToken",
               "credential_source": {"file": "/var/run/secrets/gcp/token"}}
        audience = "https://iam.googleapis.com/projects/1/locations/global/workloadIdentityPools/charlie-platform/providers/management-eks"
        gcp_values.write_text(json.dumps({"gcp": {"credentialConfig": adc, "tokenAudience": audience}}))
        docs = render(ROOT / "helm/charts/hopcharlie-platform", platform_values, "hopcharlie-platform", "charlie-platform", ("-f", str(gcp_values)))
        cm = next(d for d in docs if d["kind"] == "ConfigMap" and d["metadata"]["name"] == "hopcharlie-gcp-adc")
        assert json.loads(cm["data"]["adc.json"]) == adc, cm["data"]
        for d in docs:
            if d["kind"] not in ("Deployment", "StatefulSet"):
                continue
            pod = d["spec"]["template"]["spec"]
            tokens = [s["serviceAccountToken"] for v in pod.get("volumes") or [] for s in (v.get("projected") or {}).get("sources") or [] if "serviceAccountToken" in s]
            env = {e["name"]: e.get("value") for c in pod["containers"] for e in c.get("env") or []}
            if d["metadata"]["name"] == "hopcharlie-api":
                assert tokens == [{"audience": audience, "expirationSeconds": 3600, "path": "token"}], tokens
                assert env["GOOGLE_APPLICATION_CREDENTIALS"] == "/etc/gcp/adc.json"
            else:
                assert not tokens and "GOOGLE_APPLICATION_CREDENTIALS" not in env, d["metadata"]["name"]
        half = work / "platform-gcp-half.json"
        half.write_text(json.dumps({"gcp": {"credentialConfig": adc, "tokenAudience": ""}}))
        result = subprocess.run([HELM, "template", "hopcharlie-platform", str(ROOT / "helm/charts/hopcharlie-platform"), "--namespace", "charlie-platform", "-f", str(platform_values), "-f", str(half)], capture_output=True, text=True, encoding="utf-8")
        assert result.returncode != 0 and "go together" in result.stderr, "gcp values come in pairs"
        google_values = work / "headlamp-gke-google.json"
        google_values.write_text(json.dumps({
            "proxy": {"trustedProxyCIDRs": ["10.40.0.0/16"]},
            "oauth": {"existingSecret": "fixture-oauth"},
            "clustersSecret": "fixture-clusters",
        }))
        google_docs = render(ROOT / "helm/charts/headlamp-gke-google", google_values,
                             "headlamp-gke-google", "headlamp")
        assert {"Deployment", "Service", "Ingress", "NetworkPolicy"} <= {d["kind"] for d in google_docs}
        seen.update(check_images(google_docs))
        # The ALB evaluates the shared group by group.order: every Ingress with an explicit
        # host (platform, both Headlamp charts) must come before the apps' wildcard.
        headlamp_values = work / "headlamp-team.json"
        headlamp_values.write_text(json.dumps({
            "oidc": {"issuerURL": "https://issuer.example.com", "clientID": "fixture"},
            "clusters": [{"name": "management", "endpoint": "https://management.eks.amazonaws.com", "ca": "dGVzdA=="}],
        }))
        headlamp_docs = render(ROOT / "helm/charts/headlamp-team", ROOT / "clusters/aws-dev-management/values/headlamp-team.yaml",
                               "headlamp", "headlamp", ("-f", headlamp_values))
        alb_group = json.loads((ROOT / "clusters/aws-dev-management/values/baseline.yaml").read_text())["externalAlb"]["groupName"]
        orders = {}
        for d in alb_ingresses + [d for d in headlamp_docs + google_docs if d["kind"] == "Ingress"]:
            annotations = d["metadata"]["annotations"]
            assert annotations["alb.ingress.kubernetes.io/group.name"] == alb_group, d["metadata"]["name"]
            orders[d["metadata"]["name"]] = int(annotations["alb.ingress.kubernetes.io/group.order"])
        wildcard = orders.pop("hopcharlie-apps")
        assert len(orders) == 3 and max(orders.values()) < wildcard < 1000, (orders, wildcard)
        # Apps the platform deploys from GitHub (ADR 0023): the root's Application
        # and AppProject, the ApplicationSet chart with the values the root passes,
        # and the app chart, whose every object its AppProject must allow.
        apps_project = next(d for d in root_docs if d["kind"] == "AppProject" and d["metadata"]["name"] == "charlie-hopcharlie-apps")
        apps_app = next(d for d in root_docs if d["kind"] == "Application" and d["metadata"]["name"] == "charlie-management-hopcharlie-apps")
        assert apps_app["spec"]["project"] == "charlie-hopcharlie-apps"
        assert apps_app["spec"]["destination"] == {"server": fixture["targets"]["management"]["endpoint"], "namespace": "argocd"}
        params = {p["name"]: p["value"] for p in apps_app["spec"]["source"]["helm"]["parameters"]}
        assert params["environments.eks.cluster.server"] == fixture["targets"]["workload"]["endpoint"]
        extra = [arg for name, value in params.items() for arg in ("--set-string", f"{name}={value}")]
        apps_values = ROOT / "clusters/aws-dev-management/values/hopcharlie-apps.yaml"
        docs = render(ROOT / "helm/charts/hopcharlie-apps", apps_values, "hopcharlie-apps", "argocd", extra)
        assert sorted(d["kind"] for d in docs) == ["AppProject", "ApplicationSet", "Role", "RoleBinding", "ValidatingAdmissionPolicy", "ValidatingAdmissionPolicyBinding"]
        # Deleting the parent Application must leave the ApplicationSet (ADR 0036): its
        # Applications carry the resources finalizer, which deletes the apps' namespaces.
        appset = next(d for d in docs if d["kind"] == "ApplicationSet")
        assert appset["metadata"]["annotations"]["argocd.argoproj.io/sync-options"] == "Delete=false"
        assert appset["spec"]["template"]["metadata"]["finalizers"] == ["resources-finalizer.argocd.argoproj.io"]
        check_project(docs, apps_project, "argocd")
        allowed = {(d["group"], d["kind"]) for d in apps_project["spec"]["namespaceResourceWhitelist"]}
        for doc in docs:
            if doc["kind"] not in CLUSTER_KINDS:
                assert (doc["apiVersion"].rpartition("/")[0], doc["kind"]) in allowed, f"AppProject charlie-hopcharlie-apps denies {doc['kind']}"
        app_project = next(d for d in docs if d["kind"] == "AppProject")
        app_allowed = {(d["group"], d["kind"]) for d in app_project["spec"]["clusterResourceWhitelist"] + app_project["spec"]["namespaceResourceWhitelist"]}
        eks_profile = work / "eks-profile.json"
        eks_profile.write_text(json.dumps({"profile": json.loads(apps_values.read_text())["environments"]["eks"]["profile"]}))
        for ci in sorted((ROOT / "helm/charts/hopcharlie-app/ci").glob("*.yaml")):
            gitops_file = yaml.safe_load(ci.read_text())
            public = any(s.get("public") for s in gitops_file["values"]["services"])
            for extra in ((), ("-f", eks_profile)):
                docs = render(ROOT / "helm/charts/hopcharlie-app", ci, gitops_file["namespace"], gitops_file["namespace"], extra)
                for doc in docs:
                    assert (doc["apiVersion"].rpartition("/")[0], doc["kind"]) in app_allowed, f"AppProject hopcharlie-apps denies {doc['kind']}"
                # Sealed values (platform ADR 0016, 0019): strict-scope SealedSecrets
                # before the Deployments, envFrom and the checksum only with app secrets,
                # and one bucket per instance.
                sealed_values = gitops_file["values"].get("sealedSecrets", {})
                sealed = {d["metadata"]["name"]: d for d in docs if d["kind"] == "SealedSecret"}
                assert set(sealed) == {name for key, name in (("s3", "app-s3"), ("app", "app-secrets")) if key in sealed_values}, sorted(sealed)
                for name, doc in sealed.items():
                    assert doc["spec"]["template"] == {"metadata": {"name": name, "namespace": gitops_file["namespace"]}, "type": "Opaque"}, doc["spec"]["template"]
                    assert doc["metadata"]["annotations"] == {"argocd.argoproj.io/sync-wave": "-1"}, doc["metadata"]["annotations"]
                if "app-s3" in sealed:
                    assert sealed["app-s3"]["spec"]["encryptedData"] == sealed_values["s3"]
                if "app-secrets" in sealed:
                    assert sealed["app-secrets"]["spec"]["encryptedData"] == sealed_values["app"]
                for doc in (d for d in docs if d["kind"] == "Deployment"):
                    pod = doc["spec"]["template"]
                    expected = {"hopcharlie.dev/secrets-checksum": sealed_values["appChecksum"]} if "app" in sealed_values else None
                    assert pod["metadata"].get("annotations") == expected, pod["metadata"]
                    for container in pod["spec"]["containers"]:
                        assert container.get("envFrom") == ([{"secretRef": {"name": "app-secrets"}}] if "app" in sealed_values else None), container.get("envFrom")
                        buckets = [e["value"] for e in container["env"] if e["name"] == "S3_BUCKET"]
                        assert buckets == (["hopcharlie-eks-" + gitops_file["namespace"]] if any(r["type"] == "s3" for r in gitops_file["values"].get("resources") or []) else []), buckets
                if extra:
                    # The gateway reaches every app's internal load balancer on port 80.
                    ports = [d["spec"]["ports"][0]["port"] for d in docs if d["kind"] == "Service" and d["spec"]["type"] == "LoadBalancer"]
                    assert ports == ([80] if public else []), ports
        # GKE apps (ADR 0039): an internal passthrough load balancer that keeps the
        # gateway's source address for the NetworkPolicy, reached over the Tailscale
        # routers (ADR 0037). Buckets are GCS through its S3-compatible API (ADR 0040).
        gke_profile = work / "gke-profile.json"
        gke = json.loads(apps_values.read_text())["environments"]["gke"]
        assert gke["cluster"] == {"name": "charlie-dev-gcp-workload"}, gke["cluster"]
        gke_profile.write_text(json.dumps({"profile": gke["profile"]}))
        for ci in sorted((ROOT / "helm/charts/hopcharlie-app/ci").glob("*.yaml")):
            gitops_file = yaml.safe_load(ci.read_text())
            gitops_file.pop("profile", None)
            plain = work / ("gke-" + ci.name)
            plain.write_text(json.dumps(gitops_file))
            namespace = gitops_file["namespace"]
            docs = render(ROOT / "helm/charts/hopcharlie-app", plain, namespace, namespace, ("-f", gke_profile))
            has_s3 = any(r["type"] == "s3" for r in gitops_file["values"].get("resources") or [])
            for pod in (d["spec"]["template"] for d in docs if d["kind"] == "Deployment"):
                for container in pod["spec"]["containers"]:
                    env = {e["name"]: e.get("value") for e in container["env"] if e["name"].startswith("S3_")}
                    expected = {"S3_ENDPOINT": "https://storage.googleapis.com", "S3_BUCKET": "hopcharlie-gke-" + namespace, "S3_REGION": "auto", "S3_PATH_STYLE": "false", "S3_ACCESS_KEY": None, "S3_SECRET_KEY": None}
                    assert env == (expected if has_s3 else {}), env
            for doc in docs:
                assert (doc["apiVersion"].rpartition("/")[0], doc["kind"]) in app_allowed, f"AppProject hopcharlie-apps denies {doc['kind']}"
            for svc in (d for d in docs if d["kind"] == "Service" and d["spec"]["type"] == "LoadBalancer"):
                assert svc["spec"]["externalTrafficPolicy"] == "Local", svc["spec"]
                assert svc["metadata"]["annotations"] == {"networking.gke.io/load-balancer-type": "Internal"}, svc["metadata"]["annotations"]
            ns = next(d for d in docs if d["kind"] == "Namespace")
            assert ns["metadata"]["labels"]["pod-security.kubernetes.io/enforce"] == "restricted"
            policies = [d for d in docs if d["kind"] == "NetworkPolicy"]
            assert any({"ipBlock": {"cidr": "10.40.0.0/19"}} in (rule.get("from") or []) for d in policies for rule in d["spec"].get("ingress") or []), "GKE apps admit only the gateway subnets"
        # The schema and the chart refuse sealed values the platform must not write.
        stateful = yaml.safe_load((ROOT / "helm/charts/hopcharlie-app/ci/stateful-values.yaml").read_text())
        for name, change in (
            ("app without appChecksum", lambda v: v["values"]["sealedSecrets"].pop("appChecksum")),
            ("reserved secret name", lambda v: v["values"]["sealedSecrets"]["app"].update(S3_BUCKET="AgA=")),
            ("ciphertext that is not base64", lambda v: v["values"]["sealedSecrets"]["s3"].update(accessKey="not base64")),
            ("unknown sealed key", lambda v: v["values"]["sealedSecrets"].update(db={})),
            ("profile reading another Secret", lambda v: v["profile"]["s3"].update(credentialsSecret="other")),
            ("env name that is also a secret", lambda v: v["values"]["services"][0]["env"].update(HOST_CODE="plain")),
        ):
            values = copy.deepcopy(stateful)
            change(values)
            broken = work / "broken-values.json"
            broken.write_text(json.dumps(values))
            result = subprocess.run([HELM, "template", "broken", str(ROOT / "helm/charts/hopcharlie-app"), "-f", str(broken)], capture_output=True, text=True)
            assert result.returncode != 0, f"hopcharlie-app accepts {name}"
        subprocess.check_call([sys.executable, str(ROOT / "scripts/check-cnpg.py")])
        print(f"Helm checks passed: bootstrap, GitOps staged/OTel, two baselines, all AWS addon values, HopCharlie platform, app ApplicationSet and app chart, and CNPG PoC; {len(seen)} baseline pinned images.")


if __name__ == "__main__":
    main()
