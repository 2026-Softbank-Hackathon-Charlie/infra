#!/usr/bin/env python3
"""AWS 기본 chart를 임시 디렉터리에서 lint/render하고 GitOps·이미지 계약을 검사합니다."""
import copy
import hashlib
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


PG_ENV = {"DATABASE_URL": "uri", "PGHOST": "host", "PGPORT": "port", "PGDATABASE": "dbname", "PGUSER": "username", "PGPASSWORD": "password", "JDBC_DATABASE_URL": "jdbc-uri"}


def check_service_contract(gitops_file, docs, profile):
    """The per-service contract (platform ADR 0024): a service without a port
    gets no Service, ports, probes or PORT; memory is its request and limit,
    cpu its request; the database variables come from CNPG's app secret,
    before the service's own env; the Postgres pod gets profile.postgres.resources."""
    values = gitops_file["values"]
    defaults = {"requests": {"cpu": "50m", "memory": "64Mi"}, "limits": {"memory": "256Mi"}}
    pg = next((r["name"] for r in values.get("resources") or [] if r["type"] == "postgres"), None)
    services = {d["metadata"]["name"] for d in docs if d["kind"] == "Service"}
    for svc in values["services"]:
        name = f"{values['app']}-{svc['name']}"
        deployment = next(d for d in docs if d["kind"] == "Deployment" and d["metadata"]["name"] == name)
        container = deployment["spec"]["template"]["spec"]["containers"][0]
        env = [e["name"] for e in container.get("env") or []]
        port = svc.get("port")
        assert (name in services) == bool(port), name
        assert deployment["spec"].get("minReadySeconds") == (None if port else 10), deployment["spec"]
        assert ("ports" in container) == bool(port) and ("PORT" in env) == bool(port), container
        assert ("readinessProbe" in container) == bool(port) and ("livenessProbe" in container) == bool(port and svc.get("healthCheck")), container
        resources = copy.deepcopy(defaults)
        if svc.get("memory"):
            resources["requests"]["memory"] = resources["limits"]["memory"] = svc["memory"]
        if svc.get("cpu"):
            resources["requests"]["cpu"] = svc["cpu"]
        assert container["resources"] == resources, (name, container["resources"])
        own = sorted((svc.get("env") or {}).keys())
        assert env[len(env) - len(own):] == own, env
        refs = {e["name"]: e["valueFrom"]["secretKeyRef"] for e in container.get("env") or [] if e["name"] in PG_ENV}
        assert refs == ({k: {"name": f"{values['app']}-{pg}-app", "key": v} for k, v in PG_ENV.items()} if pg else {}), refs
    for cluster in (d for d in docs if d["kind"] == "Cluster"):
        assert cluster["spec"].get("resources") == profile.get("postgres", {}).get("resources"), cluster["spec"].get("resources")


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
    spread = next(d for d in docs if d["kind"] == "Deployment")["spec"]["template"]["spec"]["topologySpreadConstraints"]
    assert [(s["topologyKey"], s["maxSkew"], s["whenUnsatisfiable"], s.get("nodeTaintsPolicy"), s.get("matchLabelKeys")) for s in spread] == [
        ("kubernetes.io/hostname", 1, "DoNotSchedule", "Honor", ["pod-template-hash"])], spread
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
        # The eks backend template (ADR 0058): one env=template list, checked here
        # because the API refuses a bad one at startup.
        docs = render(ROOT / "helm/charts/hopcharlie-platform", platform_values, "hopcharlie-platform", "charlie-platform")
        api = next(d for d in docs if d["kind"] == "Deployment" and d["metadata"]["name"] == "hopcharlie-api")
        env = {e["name"]: e.get("value") for e in api["spec"]["template"]["spec"]["containers"][0]["env"]}
        assert env["PLATFORM_APP_BACKEND_TEMPLATES"] == "eks=http://{instance}.eks.hopcharlie.internal,onprem=http://app-onprem-{instance}.hopcharlie-db-network.svc.cluster.local", env.get("PLATFORM_APP_BACKEND_TEMPLATES")
        assert "PLATFORM_APP_BACKEND_TEMPLATE" not in env
        # Born-paired app databases (ADR 0063) are on in the management values after
        # rollout steps 1-4; the chart renders the two settings only when set.
        hosts = "eks=db-eks-{instance}.hopcharlie-db-network.svc.cluster.local,gke=db-gke-{instance}.hopcharlie-db-network.svc.cluster.local,onprem=db-onprem-{instance}.hopcharlie-db-network.svc.cluster.local"
        assert env.get("PLATFORM_APP_DB_TOPOLOGY") == "1" and env.get("PLATFORM_APP_DB_HOST_TEMPLATES") == hosts, env
        off_values = work / "platform-app-db-topology-off.json"
        off_values.write_text(json.dumps({"api": {"appDbTopology": {"enabled": False, "hostTemplates": "", "route": {"tailnet": "", "proxyClass": "", "tag": ""}}}}))
        docs = render(ROOT / "helm/charts/hopcharlie-platform", platform_values, "hopcharlie-platform", "charlie-platform", ("-f", str(off_values)))
        api = next(d for d in docs if d["kind"] == "Deployment" and d["metadata"]["name"] == "hopcharlie-api")
        off_env = {e["name"]: e.get("value") for e in api["spec"]["template"]["spec"]["containers"][0]["env"]}
        assert "PLATFORM_APP_DB_TOPOLOGY" not in off_env and "PLATFORM_APP_DB_HOST_TEMPLATES" not in off_env, off_env
        topo_values = work / "platform-app-db-topology.json"
        topo_values.write_text(json.dumps({"api": {"appDbTopology": {"enabled": True, "hostTemplates": hosts}}}))
        docs = render(ROOT / "helm/charts/hopcharlie-platform", platform_values, "hopcharlie-platform", "charlie-platform", ("-f", str(topo_values)))
        api = next(d for d in docs if d["kind"] == "Deployment" and d["metadata"]["name"] == "hopcharlie-api")
        topo_env = {e["name"]: e.get("value") for e in api["spec"]["template"]["spec"]["containers"][0]["env"]}
        assert topo_env["PLATFORM_APP_DB_TOPOLOGY"] == "1" and topo_env["PLATFORM_APP_DB_HOST_TEMPLATES"] == hosts, topo_env
        assert topo_env["PLATFORM_DSN_HOST_SUFFIXES"] == env["PLATFORM_DSN_HOST_SUFFIXES"]
        # App DB routes and registered-app files (ADR 0064) are on in the management
        # values after their one-time steps (IAM, app-db-routes release).
        route = {"tailnet": "tail567fe8.ts.net", "proxyClass": "hopcharlie-app-db-egress-management", "tag": "tag:hop-db-client-management"}
        assert {k: env.get("PLATFORM_APP_DB_ROUTE_" + k) for k in ("TAILNET", "PROXY_CLASS", "TAG")} == {
            "TAILNET": route["tailnet"], "PROXY_CLASS": route["proxyClass"], "TAG": route["tag"]}, env
        assert env.get("PLATFORM_APP_FILES") == "1", env
        off_values = work / "platform-app-db-routes-off.json"
        off_values.write_text(json.dumps({"api": {"appDbTopology": {"route": {"tailnet": "", "proxyClass": "", "tag": ""}}, "appFiles": {"enabled": False}}}))
        docs = render(ROOT / "helm/charts/hopcharlie-platform", platform_values, "hopcharlie-platform", "charlie-platform", ("-f", str(off_values)))
        api = next(d for d in docs if d["kind"] == "Deployment" and d["metadata"]["name"] == "hopcharlie-api")
        off_env = {e["name"]: e.get("value") for e in api["spec"]["template"]["spec"]["containers"][0]["env"]}
        assert not any(k.startswith("PLATFORM_APP_DB_ROUTE_") or k == "PLATFORM_APP_FILES" for k in off_env), off_env
        on_values = work / "platform-app-db-routes.json"
        on_values.write_text(json.dumps({"api": {"appDbTopology": {"route": route}, "appFiles": {"enabled": True}}}))
        docs = render(ROOT / "helm/charts/hopcharlie-platform", platform_values, "hopcharlie-platform", "charlie-platform", ("-f", str(on_values)))
        api = next(d for d in docs if d["kind"] == "Deployment" and d["metadata"]["name"] == "hopcharlie-api")
        on_env = {e["name"]: e.get("value") for e in api["spec"]["template"]["spec"]["containers"][0]["env"]}
        assert {k: on_env.get("PLATFORM_APP_DB_ROUTE_" + k.upper()) for k in ("tailnet", "tag")} == {"tailnet": route["tailnet"], "tag": route["tag"]}, on_env
        assert on_env["PLATFORM_APP_DB_ROUTE_PROXY_CLASS"] == route["proxyClass"] and on_env["PLATFORM_APP_FILES"] == "1", on_env
        assert api["spec"]["template"]["spec"]["automountServiceAccountToken"] is True
        # The gke: remote uses the API pod's Google ADC (ADR 0041).
        assert on_env["GOOGLE_APPLICATION_CREDENTIALS"] == "/etc/gcp/adc.json", on_env
        for i, bad in enumerate(({"api": {"appDbTopology": {"route": {"tailnet": "tail567fe8.ts.net", "proxyClass": "", "tag": ""}}}},
                                 {"api": {"appDbTopology": {"hostTemplates": "", "route": route}}})):
            path = work / f"platform-app-db-routes-invalid-{i}.json"
            path.write_text(json.dumps(bad))
            result = subprocess.run([HELM, "template", "hopcharlie-platform", str(ROOT / "helm/charts/hopcharlie-platform"), "-f", str(platform_values), "-f", str(path)], capture_output=True, text=True, encoding="utf-8")
            assert result.returncode != 0 and "route needs tailnet" in result.stderr, result.stderr
        two = work / "platform-two-templates.json"
        two.write_text(json.dumps({"deploy": {"backendTemplates": "eks=http://{instance}.eks.hopcharlie.internal,gke=https://{app}-{service}.{instance}.gke.test:{port}/?env={env}"}}))
        docs = render(ROOT / "helm/charts/hopcharlie-platform", platform_values, "hopcharlie-platform", "charlie-platform", ("-f", str(two)))
        api = next(d for d in docs if d["kind"] == "Deployment" and d["metadata"]["name"] == "hopcharlie-api")
        assert any(e["name"] == "PLATFORM_APP_BACKEND_TEMPLATES" and e["value"].startswith("eks=") and ",gke=" in e["value"] for e in api["spec"]["template"]["spec"]["containers"][0]["env"])
        for bad in ("http://{instance}.eks.hopcharlie.internal", "eks=http://{instance}.eks.test,", "eks={instance}.eks.test",
                    "eks=http://{instnace}.eks.test", "eks=http://{instance}.eks.test,gke=http://a b.test", "EKS=http://a.test"):
            # A values file, as --set splits on commas.
            bad_values = work / "platform-bad-templates.json"
            bad_values.write_text(json.dumps({"deploy": {"backendTemplates": bad}}))
            result = subprocess.run([HELM, "template", "hopcharlie-platform", str(ROOT / "helm/charts/hopcharlie-platform"), "--namespace", "charlie-platform",
                                     "-f", str(platform_values), "-f", str(bad_values)], capture_output=True, text=True, encoding="utf-8")
            assert result.returncode != 0 and "deploy.backendTemplates" in result.stderr, f"accepted {bad!r}"
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
        assert sorted(d["kind"] for d in docs) == ["AppProject", "ApplicationSet", "Role", "RoleBinding",
                                                   "ValidatingAdmissionPolicy", "ValidatingAdmissionPolicy", "ValidatingAdmissionPolicy",
                                                   "ValidatingAdmissionPolicyBinding", "ValidatingAdmissionPolicyBinding", "ValidatingAdmissionPolicyBinding"]
        # The platform's AI runner Jobs (hopcharlie-platform hopai.yaml) are kept to the
        # runner by a policy on the platform ServiceAccount's Job requests.
        runner = next(d for d in docs if d["kind"] == "ValidatingAdmissionPolicy" and d["metadata"]["name"] == "hopcharlie-platform-ai-runner")
        assert runner["spec"]["matchConstraints"]["resourceRules"] == [{"apiGroups": ["batch"], "apiVersions": ["*"], "operations": ["CREATE", "UPDATE"], "resources": ["jobs"]}]
        assert "system:serviceaccount:charlie-platform:hopcharlie-platform" in runner["spec"]["matchConditions"][0]["expression"]
        rules = " ".join(v["expression"] for v in runner["spec"]["validations"])
        for want in ("'hopai'", "serviceAccountName == 'hopcharlie-platform'", "/hopcharlie/hopai:", "aws-cli/aws-cli:", "has(v.secret)", "has(v.hostPath)", "envFrom",
                     "['hopcharlie-hopai-fetch', 'hopai-onboarding']", "'^hopai-onb-[0-9a-f]{12}$'"):
            assert want in rules, want
        # An onboarding's hopai input ConfigMap and stopping its Job (infra ADR 0082).
        cleanup = next(d for d in docs if d["kind"] == "ValidatingAdmissionPolicy" and d["metadata"]["name"] == "hopcharlie-platform-ai-runner-cleanup")["spec"]
        assert cleanup["matchConstraints"]["resourceRules"] == [
            {"apiGroups": [""], "apiVersions": ["*"], "operations": ["CREATE", "DELETE"], "resources": ["configmaps"]},
            {"apiGroups": ["batch"], "apiVersions": ["*"], "operations": ["DELETE"], "resources": ["jobs"]}]
        cleanup_rules = " ".join(v["expression"] for v in cleanup["validations"])
        for want in ("request.namespace == 'charlie-platform'", "'^hopai-onb-[0-9a-f]{12}$'", "oldObject.metadata.labels['hopcharlie.dev/ai-run'] == 'true'"):
            assert want in cleanup_rules, want
        binding = next(d for d in docs if d["kind"] == "ValidatingAdmissionPolicyBinding" and d["spec"]["policyName"] == "hopcharlie-platform-ai-runner")
        assert binding["spec"]["validationActions"] == ["Deny"]
        # Deleting the parent Application must leave the ApplicationSet (ADR 0036): its
        # Applications carry the resources finalizer, which deletes the apps' namespaces.
        appset = next(d for d in docs if d["kind"] == "ApplicationSet")
        assert appset["metadata"]["annotations"]["argocd.argoproj.io/sync-options"] == "Delete=false"
        assert appset["spec"]["template"]["metadata"]["finalizers"] == ["resources-finalizer.argocd.argoproj.io"]
        # Migrations change the Cluster's topology fields (ADR 0050); the Tailscale operator
        # rewrites only a database egress Service's externalName (ADR 0063).
        assert appset["spec"]["template"]["spec"]["ignoreDifferences"] == [
            {"group": "postgresql.cnpg.io", "kind": "Cluster", "jqPathExpressions": [".spec.replica", 'select(.metadata.labels["hopcharlie.dev/db-topology-members"] != "3") | .spec.externalClusters', ".spec.certificates", ".spec.bootstrap"]},
            {"group": "apps", "kind": "Deployment", "jqPathExpressions": ['select(.metadata.labels["hopcharlie.dev/onboarding-cold"] == "true") | .spec.replicas']},
            {"group": "", "kind": "Service", "jqPathExpressions": [
                'select(.spec.type == "ExternalName" and .metadata.annotations["tailscale.com/tailnet-fqdn"] != null) | .spec.externalName']}]
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
                profile = json.loads(eks_profile.read_text())["profile"] if extra else gitops_file.get("profile", {})
                check_service_contract(gitops_file, docs, profile)
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
                    if profile.get("observability", {}).get("enabled"):
                        expected = {**(expected or {}), "checksum/observability-ca": hashlib.sha256(profile["observability"]["caBundle"].encode()).hexdigest()}
                    assert pod["metadata"].get("annotations") == expected, pod["metadata"]
                    for container in pod["spec"]["containers"]:
                        assert container.get("envFrom") == ([{"secretRef": {"name": "app-secrets"}}] if "app" in sealed_values else None), container.get("envFrom")
                        buckets = [e["value"] for e in container["env"] if e["name"] == "S3_BUCKET"]
                        assert buckets == (["hopcharlie-eks-" + gitops_file["namespace"]] if any(r["type"] == "s3" for r in gitops_file["values"].get("resources") or []) else []), buckets
                if extra:
                    # The gateway reaches every app's internal load balancer on port 80.
                    ports = [d["spec"]["ports"][0]["port"] for d in docs if d["kind"] == "Service" and d["spec"]["type"] == "LoadBalancer"]
                    assert ports == ([80] if public else []), ports
                    # The shared entry (ADR 0058): the public service's route on Traefik at
                    # <instance>.eks.hopcharlie.internal, the platform's eks backend host.
                    svc_name = next((f"{gitops_file['values']['app']}-{s['name']}" for s in gitops_file["values"]["services"] if s.get("public")), None)
                    routes = [(d["metadata"]["name"], d["spec"]) for d in docs if d["kind"] == "Ingress"]
                    assert routes == ([(svc_name, {"ingressClassName": "traefik", "rules": [{"host": f"{gitops_file['namespace']}.eks.hopcharlie.internal", "http": {"paths": [
                        {"path": "/", "pathType": "Prefix", "backend": {"service": {"name": svc_name, "port": {"name": "http"}}}}]}}]})] if public else []), routes
                else:
                    assert not any(d["kind"] == "Ingress" for d in docs), "the chart's default profile has no Ingress"
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
            check_service_contract(gitops_file, docs, gke["profile"])
            has_s3 = any(r["type"] == "s3" for r in gitops_file["values"].get("resources") or [])
            for pod in (d["spec"]["template"] for d in docs if d["kind"] == "Deployment"):
                for container in pod["spec"]["containers"]:
                    env = {e["name"]: e.get("value") for e in container["env"] if e["name"].startswith("S3_")}
                    expected = {"S3_ENDPOINT": "https://storage.googleapis.com", "S3_BUCKET": "hopcharlie-gke-" + namespace, "S3_REGION": "auto", "S3_PATH_STYLE": "false", "S3_ACCESS_KEY": None, "S3_SECRET_KEY": None}
                    assert env == (expected if has_s3 else {}), env
            for doc in docs:
                assert (doc["apiVersion"].rpartition("/")[0], doc["kind"]) in app_allowed, f"AppProject hopcharlie-apps denies {doc['kind']}"
            # GKE has no shared entry yet (ADR 0058): no Ingress there.
            assert not any(d["kind"] == "Ingress" for d in docs), "gke apps get no Ingress yet"
            for svc in (d for d in docs if d["kind"] == "Service" and d["spec"]["type"] == "LoadBalancer"):
                assert svc["spec"]["externalTrafficPolicy"] == "Local", svc["spec"]
                assert svc["metadata"]["annotations"] == {"networking.gke.io/load-balancer-type": "Internal"}, svc["metadata"]["annotations"]
            ns = next(d for d in docs if d["kind"] == "Namespace")
            assert ns["metadata"]["labels"]["pod-security.kubernetes.io/enforce"] == "restricted"
            policies = [d for d in docs if d["kind"] == "NetworkPolicy"]
            assert any({"ipBlock": {"cidr": "10.40.0.0/19"}} in (rule.get("from") or []) for d in policies for rule in d["spec"].get("ingress") or []), "GKE apps admit only the gateway subnets"
        # onprem apps (ADR 0081): buckets on the laptop's in-cluster MinIO, path
        # style, with the same prefix as the platform's minio storage block, and
        # an egress rule to MinIO's port 9000 next to the default 443.
        onprem_profile = work / "onprem-profile.json"
        onprem = json.loads(apps_values.read_text())["environments"]["onprem"]
        onprem_profile.write_text(json.dumps({"profile": onprem["profile"]}))
        envs_text = (ROOT / "terraform/platform/aws/host/envs.yaml").read_text()
        onprem_env = next(e for e in yaml.safe_load(envs_text)["environments"] if e["name"] == "onprem")
        assert onprem_env["storage"]["driver"] == "minio", onprem_env.get("storage")
        assert onprem_env["storage"]["bucketPrefix"] == onprem["profile"]["s3"]["bucketPrefix"], "envs.yaml and the onprem profile name the same buckets"
        minio_egress = {"to": [{"namespaceSelector": {"matchLabels": {"kubernetes.io/metadata.name": "minio"}}, "podSelector": {"matchLabels": {"app": "minio"}}}], "ports": [{"protocol": "TCP", "port": 9000}]}
        for ci in sorted((ROOT / "helm/charts/hopcharlie-app/ci").glob("*.yaml")):
            gitops_file = yaml.safe_load(ci.read_text())
            gitops_file.pop("profile", None)
            plain = work / ("onprem-" + ci.name)
            plain.write_text(json.dumps(gitops_file))
            namespace = gitops_file["namespace"]
            docs = render(ROOT / "helm/charts/hopcharlie-app", plain, namespace, namespace, ("-f", onprem_profile))
            has_s3 = any(r["type"] == "s3" for r in gitops_file["values"].get("resources") or [])
            for pod in (d["spec"]["template"] for d in docs if d["kind"] == "Deployment"):
                for container in pod["spec"]["containers"]:
                    env = {e["name"]: e.get("value") for e in container["env"] if e["name"].startswith("S3_")}
                    expected = {"S3_ENDPOINT": "http://minio.minio.svc.cluster.local:9000", "S3_BUCKET": "hopcharlie-op-" + namespace, "S3_REGION": "us-east-1", "S3_PATH_STYLE": "true", "S3_ACCESS_KEY": None, "S3_SECRET_KEY": None}
                    assert env == (expected if has_s3 else {}), env
            policy = next(d for d in docs if d["kind"] == "NetworkPolicy" and d["metadata"]["name"] == "hopcharlie-app")
            assert minio_egress in policy["spec"]["egress"], policy["spec"]["egress"]
        # The schema and the chart refuse sealed values the platform must not write.
        stateful = yaml.safe_load((ROOT / "helm/charts/hopcharlie-app/ci/stateful-values.yaml").read_text())
        # Postgres backups (platform ADR 0025): with the platform's backup bucket and
        # key, the eks profile gives the Cluster the Barman Cloud plugin, an
        # ObjectStore at the registration's path and a daily ScheduledBackup. The gke
        # profile, which does not turn backups on, draws none of them.
        backed = copy.deepcopy(stateful)
        backed.pop("profile")
        backed["values"]["backup"] = {"bucket": "hopcharlie-eks-moimroom---pg", "path": "20261104T082201Z"}
        backed["values"]["sealedSecrets"]["pgBackup"] = {"accessKey": "AgBiYWNrdXAtYWNjZXNzCg==", "secretKey": "AgBiYWNrdXAtc2VjcmV0Cg=="}
        backed_file = work / "backed-values.json"
        backed_file.write_text(json.dumps(backed))
        docs = render(ROOT / "helm/charts/hopcharlie-app", backed_file, "moimroom", "moimroom", ("-f", eks_profile))
        for doc in docs:
            assert (doc["apiVersion"].rpartition("/")[0], doc["kind"]) in app_allowed, f"AppProject hopcharlie-apps denies {doc['kind']}"
        cluster = next(d for d in docs if d["kind"] == "Cluster")
        assert cluster["spec"]["plugins"] == [{"name": "barman-cloud.cloudnative-pg.io", "enabled": True, "isWALArchiver": True,
                                               "parameters": {"barmanObjectName": "moimroom-db", "serverName": "moimroom-db"}}], cluster["spec"]
        store = next(d for d in docs if d["kind"] == "ObjectStore")
        assert store["metadata"]["name"] == "moimroom-db" and store["metadata"]["annotations"] == {"argocd.argoproj.io/sync-wave": "-1"}
        assert store["spec"]["configuration"]["destinationPath"] == "s3://hopcharlie-eks-moimroom---pg/20261104T082201Z/"
        assert "endpointURL" not in store["spec"]["configuration"], store["spec"]["configuration"]
        assert store["spec"]["configuration"]["s3Credentials"] == {"accessKeyId": {"name": "app-pgbackup", "key": "accessKey"}, "secretAccessKey": {"name": "app-pgbackup", "key": "secretKey"}}
        assert store["spec"]["instanceSidecarConfiguration"]["env"] == [{"name": "AWS_REGION", "value": "ap-northeast-2"}, {"name": "AWS_DEFAULT_REGION", "value": "ap-northeast-2"}]
        assert store["spec"]["retentionPolicy"] == "7d"
        schedule = next(d for d in docs if d["kind"] == "ScheduledBackup")
        assert schedule["metadata"]["annotations"] == {"argocd.argoproj.io/sync-wave": "1"}
        assert schedule["spec"] == {"cluster": {"name": "moimroom-db"}, "schedule": "0 0 18 * * *", "method": "plugin",
                                    "pluginConfiguration": {"name": "barman-cloud.cloudnative-pg.io"}, "immediate": True, "suspend": False, "backupOwnerReference": "self"}, schedule["spec"]
        key = next(d for d in docs if d["kind"] == "SealedSecret" and d["metadata"]["name"] == "app-pgbackup")
        assert key["spec"]["encryptedData"] == backed["values"]["sealedSecrets"]["pgBackup"] and key["metadata"]["annotations"] == {"argocd.argoproj.io/sync-wave": "-1"}
        docs = render(ROOT / "helm/charts/hopcharlie-app", backed_file, "moimroom", "moimroom", ("-f", gke_profile))
        assert not [d for d in docs if d["kind"] in ("ObjectStore", "ScheduledBackup")] and "plugins" not in next(d for d in docs if d["kind"] == "Cluster")["spec"]
        # Apps registered in two environments (ADR 0063): with values.dbTopology the
        # Cluster is one half of a CNPG distributed topology. The gke file is born
        # primary (initdb), the eks file a replica (pg_basebackup) of it; both use the
        # sealed app account and CA, and reach each other through the Tailscale
        # Services when the profile's dbNetwork is on.
        pg_app = {k: "AgBwZy1hcHAK" for k in ("username", "password", "dbname", "host", "port", "uri", "jdbc-uri")}
        pg_ca = {"ca.crt": "AgBjYS1jcnQK", "ca.key": "AgBjYS1rZXkK"}
        topo_files = {}
        for env, peer in (("gke", "eks"), ("eks", "eks")):  # peer: the env born a replica
            topo = copy.deepcopy(stateful)
            topo.pop("profile")
            topo["env"] = env
            topo["values"]["dbTopology"] = {"primary": "gke", "peer": peer}
            topo["values"]["sealedSecrets"].update(pgApp=pg_app, pgCA=pg_ca)
            topo_files[env] = work / f"topology-{env}.json"
            topo_files[env].write_text(json.dumps(topo))
        apps_envs = json.loads(apps_values.read_text())["environments"]
        sizes = set()
        for env, other in (("gke", "eks"), ("eks", "gke")):
            profile = copy.deepcopy(apps_envs[env]["profile"])
            profile["dbNetwork"]["enabled"] = True
            on = work / f"topology-profile-{env}.json"
            on.write_text(json.dumps({"profile": profile}))
            docs = render(ROOT / "helm/charts/hopcharlie-app", topo_files[env], "moimroom", "moimroom", ("-f", on))
            for doc in docs:
                assert (doc["apiVersion"].rpartition("/")[0], doc["kind"]) in app_allowed, f"AppProject hopcharlie-apps denies {doc['kind']}"
            ns = next(d for d in docs if d["kind"] == "Namespace")
            assert ns["metadata"]["labels"]["hopcharlie.dev/db-topology"] == "true"
            spec = next(d for d in docs if d["kind"] == "Cluster")["spec"]
            sizes.add(spec["storage"]["size"])
            secret = {"name": "moimroom-db-app"}
            if env == "gke":
                assert spec["bootstrap"] == {"initdb": {"database": "app", "owner": "app", "secret": secret}}, spec["bootstrap"]
            else:
                assert spec["bootstrap"] == {"pg_basebackup": {"source": "moimroom-db-gke", "database": "app", "owner": "app", "secret": secret}}, spec["bootstrap"]
            assert spec["replica"] == {"self": f"moimroom-db-{env}", "primary": "moimroom-db-gke", "source": f"moimroom-db-{other}"}, spec["replica"]
            assert spec["certificates"] == {"serverCASecret": "app-pg-ca", "clientCASecret": "app-pg-ca", "serverAltDNSNames": [
                f"moimroom-db-to-{env}.moimroom.svc", f"moimroom-db-to-{env}.moimroom.svc.cluster.local"]}, spec["certificates"]
            tls = {"sslCert": {"name": "moimroom-db-replication", "key": "tls.crt"}, "sslKey": {"name": "moimroom-db-replication", "key": "tls.key"},
                   "sslRootCert": {"name": "app-pg-ca", "key": "ca.crt"}}
            params = {"user": "streaming_replica", "dbname": "postgres", "sslmode": "verify-full"}
            assert spec["externalClusters"] == [
                {"name": f"moimroom-db-{env}", "connectionParameters": {"host": "moimroom-db-rw.moimroom.svc", **params}, **tls},
                {"name": f"moimroom-db-{other}", "connectionParameters": {"host": f"moimroom-db-to-{other}.moimroom.svc.cluster.local", **params}, **tls}], spec["externalClusters"]
            sealed = {d["metadata"]["name"]: d for d in docs if d["kind"] == "SealedSecret"}
            assert sealed["moimroom-db-app"]["spec"]["template"]["type"] == "kubernetes.io/basic-auth"
            assert sealed["moimroom-db-app"]["spec"]["encryptedData"] == pg_app
            assert sealed["app-pg-ca"]["spec"]["template"]["type"] == "Opaque" and sealed["app-pg-ca"]["spec"]["encryptedData"] == pg_ca
            services = {d["metadata"]["name"]: d for d in docs if d["kind"] == "Service"}
            net = profile["dbNetwork"]
            assert services["moimroom-db-ts"]["metadata"]["annotations"] == {
                "tailscale.com/expose": "true", "tailscale.com/hostname": f"hop-db-{env}-moimroom",
                "tailscale.com/tags": f"tag:hop-db-{env}", "tailscale.com/proxy-class": f"hopcharlie-app-db-ingress-{env}"}
            assert services["moimroom-db-ts"]["spec"]["selector"] == {"cnpg.io/cluster": "moimroom-db", "cnpg.io/instanceRole": "primary"}
            egress = services[f"moimroom-db-to-{other}"]
            assert egress["spec"]["type"] == "ExternalName" and egress["metadata"]["annotations"] == {
                "tailscale.com/tailnet-fqdn": f"hop-db-{other}-moimroom.{net['tailnetDomain']}", "tailscale.com/hostname": f"hop-db-{env}-moimroom-to-{other}",
                "tailscale.com/tags": f"tag:hop-db-client-{env}", "tailscale.com/proxy-class": f"hopcharlie-app-db-egress-{env}"}
            policy = next(d for d in docs if d["kind"] == "NetworkPolicy" and d["metadata"]["name"] == "moimroom-db-replication")["spec"]

            def proxy(svc):
                return {"namespaceSelector": {"matchLabels": {"kubernetes.io/metadata.name": "hopcharlie-db-network"}}, "podSelector": {"matchLabels": {
                    "tailscale.com/parent-resource": svc, "tailscale.com/parent-resource-ns": "moimroom"}}}
            assert policy["ingress"] == [{"from": [proxy("moimroom-db-ts")], "ports": [{"protocol": "TCP", "port": 5432}]}], policy
            assert policy["egress"] == [{"to": [proxy(f"moimroom-db-to-{other}")], "ports": [{"protocol": "TCP", "port": 5432}]}], policy
            # dbNetwork off: the Cluster and Secrets, no tailnet objects.
            profile["dbNetwork"]["enabled"] = False
            off = work / f"topology-profile-off-{env}.json"
            off.write_text(json.dumps({"profile": profile}))
            docs = render(ROOT / "helm/charts/hopcharlie-app", topo_files[env], "moimroom", "moimroom", ("-f", off))
            assert not [d for d in docs if d["metadata"]["name"].startswith("moimroom-db-")
                        and d["kind"] in ("Service", "NetworkPolicy")], "dbNetwork off draws no tailnet objects"
        assert len(sizes) == 1, f"both environments' databases must be the same size: {sizes}"
        # ADR 0078: a third member gets both remote paths. New replicas always
        # bootstrap from primary; primary keeps the legacy peer as its source.
        members = ["gke", "eks", "onprem"]
        for env in members:
            values = json.loads(topo_files["gke"].read_text())
            values["env"] = env
            values["values"]["dbTopology"]["members"] = members
            three = work / f"topology-three-{env}.json"
            three.write_text(json.dumps(values))
            docs = render(ROOT / "helm/charts/hopcharlie-app", three, "moimroom", "moimroom", ("-f", work / "topology-profile-gke.json"))
            cluster = next(d for d in docs if d["kind"] == "Cluster")
            namespace = next(d for d in docs if d["kind"] == "Namespace")
            assert cluster["metadata"]["labels"]["hopcharlie.dev/db-topology-members"] == "3"
            assert namespace["metadata"]["labels"]["hopcharlie.dev/db-topology-members"] == "3"
            spec = cluster["spec"]
            source = "eks" if env == "gke" else "gke"
            assert spec["replica"] == {"self": f"moimroom-db-{env}", "primary": "moimroom-db-gke", "source": f"moimroom-db-{source}"}
            if env == "gke":
                assert "initdb" in spec["bootstrap"]
            else:
                assert spec["bootstrap"]["pg_basebackup"]["source"] == "moimroom-db-gke"
            assert {c["name"] for c in spec["externalClusters"]} == {f"moimroom-db-{m}" for m in members}
            services = {d["metadata"]["name"]: d for d in docs if d["kind"] == "Service"}
            policy = next(d for d in docs if d["kind"] == "NetworkPolicy" and d["metadata"]["name"] == "moimroom-db-replication")["spec"]
            assert policy["egress"] == [{"to": [proxy(f"moimroom-db-to-{m}")], "ports": [{"protocol": "TCP", "port": 5432}]} for m in members if m != env]
            for m in members:
                if m == env:
                    continue
                egress = services[f"moimroom-db-to-{m}"]
                assert egress["metadata"]["annotations"]["tailscale.com/tailnet-fqdn"] == f"hop-db-{m}-moimroom.{net['tailnetDomain']}"
                external = next(c for c in spec["externalClusters"] if c["name"] == f"moimroom-db-{m}")
                assert external["connectionParameters"]["host"] == f"moimroom-db-to-{m}.moimroom.svc.cluster.local"
        # One env so far (ADR 0074): the primary's file with peerPresent false is the
        # same output without the tailnet objects; peerPresent true is the same output.
        on = work / "topology-profile-gke.json"
        paired = render(ROOT / "helm/charts/hopcharlie-app", topo_files["gke"], "moimroom", "moimroom", ("-f", on))
        tailnet = {("Service", "moimroom-db-ts"), ("Service", "moimroom-db-to-eks"), ("NetworkPolicy", "moimroom-db-replication")}
        assert tailnet <= {(d["kind"], d["metadata"]["name"]) for d in paired}
        for present in (True, False):
            values = json.loads(topo_files["gke"].read_text())
            values["values"]["dbTopology"]["peerPresent"] = present
            alone = work / f"topology-gke-peer-{str(present).lower()}.json"
            alone.write_text(json.dumps(values))
            docs = render(ROOT / "helm/charts/hopcharlie-app", alone, "moimroom", "moimroom", ("-f", on))
            want = paired if present else [d for d in paired if (d["kind"], d["metadata"]["name"]) not in tailnet]
            assert docs == want, f"peerPresent {present} must draw the paired output{'' if present else ' without the tailnet objects'}"
        # Onboarded apps (platform ADR 0042, infra ADR 0082): the primary's Cluster
        # is born a replica of the source outside the platform; the app stays cold.
        onb = json.loads(topo_files["gke"].read_text())
        onb.update(env="eks")
        for svc in onb["values"]["services"]:
            svc["replicas"] = 0
        onb["values"]["postgres"] = {"image": "ghcr.io/cloudnative-pg/postgresql:17.11-standard-bookworm@sha256:" + "a" * 64,
                                     "database": "moimroom", "owner": "moimroom", "storageSize": "3Gi",
                                     "parameters": {"max_connections": "200", "max_wal_senders": "10", "wal_retrieve_retry_interval": "2s"}}
        onb["values"]["dbTopology"] = {"primary": "eks", "peer": "gke", "peerPresent": False, "external": {
            "name": "legacy", "host": "hop-db-legacy-moimroom", "user": "hopcharlie_replicator", "passwordSecret": "moimroom-db-legacy", "sslmode": "prefer"}}
        onb["values"]["onboardingCold"] = True
        pg_legacy = {"username": "AgB1c2VyCg==", "password": "AgBwYXNzCg=="}
        onb["values"]["sealedSecrets"]["pgLegacy"] = pg_legacy
        onb_file = work / "onboarding-eks.json"
        onb_file.write_text(json.dumps(onb))
        eks_on = work / "topology-profile-eks.json"
        net = json.loads(eks_on.read_text())["profile"]["dbNetwork"]
        docs = render(ROOT / "helm/charts/hopcharlie-app", onb_file, "moimroom", "moimroom", ("-f", eks_on))
        for doc in docs:
            assert (doc["apiVersion"].rpartition("/")[0], doc["kind"]) in app_allowed, f"AppProject hopcharlie-apps denies {doc['kind']}"
        spec = next(d for d in docs if d["kind"] == "Cluster")["spec"]
        assert spec["imageName"] == onb["values"]["postgres"]["image"] and spec["storage"]["size"] == "3Gi"
        # values.postgres.parameters merge into the chart's; a given key wins.
        assert spec["postgresql"]["parameters"] == {"wal_retrieve_retry_interval": "2s", "max_connections": "200", "max_wal_senders": "10"}, spec["postgresql"]
        assert spec["bootstrap"] == {"pg_basebackup": {"source": "legacy", "database": "moimroom", "owner": "moimroom", "secret": {"name": "moimroom-db-app"}}}, spec["bootstrap"]
        assert spec["replica"] == {"self": "moimroom-db-eks", "primary": "legacy", "source": "legacy"}, spec["replica"]
        assert spec["certificates"]["serverCASecret"] == "app-pg-ca" and spec["certificates"]["clientCASecret"] == "app-pg-ca"
        assert spec["externalClusters"] == [
            {"name": "legacy", "connectionParameters": {"host": "moimroom-db-to-legacy.moimroom.svc.cluster.local", "user": "hopcharlie_replicator",
                                                        "dbname": "postgres", "sslmode": "prefer"},
             "password": {"name": "moimroom-db-legacy", "key": "password"}},
            {"name": "moimroom-db-eks", "connectionParameters": {"host": "moimroom-db-rw.moimroom.svc", **params}, **tls},
            {"name": "moimroom-db-gke", "connectionParameters": {"host": "moimroom-db-to-gke.moimroom.svc.cluster.local", **params}, **tls}], spec["externalClusters"]
        objects = {(d["kind"], d["metadata"]["name"]): d for d in docs}
        assert ("Service", "moimroom-db-ts") not in objects and ("Service", "moimroom-db-to-gke") not in objects, "no peer tailnet objects before the peer exists"
        egress = objects[("Service", "moimroom-db-to-legacy")]
        assert egress["spec"]["type"] == "ExternalName" and egress["metadata"]["annotations"] == {
            "tailscale.com/tailnet-fqdn": f"hop-db-legacy-moimroom.{net['tailnetDomain']}", "tailscale.com/hostname": "hop-db-eks-moimroom-to-legacy",
            "tailscale.com/tags": "tag:hop-db-client-eks", "tailscale.com/proxy-class": "hopcharlie-app-db-egress-eks"}
        assert objects[("NetworkPolicy", "moimroom-db-to-legacy")]["spec"]["egress"] == [{"to": [proxy("moimroom-db-to-legacy")], "ports": [{"protocol": "TCP", "port": 5432}]}]
        legacy = objects[("SealedSecret", "moimroom-db-legacy")]["spec"]
        assert legacy["encryptedData"] == pg_legacy and legacy["template"]["type"] == "kubernetes.io/basic-auth"
        deployments = [d for d in docs if d["kind"] == "Deployment"]
        assert deployments and all(d["spec"]["replicas"] == 0 and d["metadata"]["labels"]["hopcharlie.dev/onboarding-cold"] == "true"
                                   and "hopcharlie.dev/onboarding-cold" not in d["spec"]["template"]["metadata"]["labels"] for d in deployments)
        # After the cutover: initdb with the same database (Argo CD ignores it), no
        # source objects, the legacy Secret kept, replicas as written (0 stays 0).
        after = copy.deepcopy(onb)
        after["values"]["dbTopology"].pop("external")
        after["values"].pop("onboardingCold")
        after["values"]["services"][0]["replicas"] = 2
        after_file = work / "onboarding-after.json"
        after_file.write_text(json.dumps(after))
        docs = render(ROOT / "helm/charts/hopcharlie-app", after_file, "moimroom", "moimroom", ("-f", eks_on))
        objects = {(d["kind"], d["metadata"]["name"]): d for d in docs}
        spec = objects[("Cluster", "moimroom-db")]["spec"]
        assert spec["bootstrap"] == {"initdb": {"database": "moimroom", "owner": "moimroom", "secret": {"name": "moimroom-db-app"}}}
        assert [c["name"] for c in spec["externalClusters"]] == ["moimroom-db-eks", "moimroom-db-gke"]
        # Without dbTopology the parameters are the Cluster's only ones.
        alone = copy.deepcopy(after)
        alone["values"].pop("dbTopology")
        for key in ("pgApp", "pgCA", "pgLegacy"):
            alone["values"]["sealedSecrets"].pop(key)
        alone_file = work / "onboarding-alone.json"
        alone_file.write_text(json.dumps(alone))
        spec = next(d for d in render(ROOT / "helm/charts/hopcharlie-app", alone_file, "moimroom", "moimroom", ("-f", eks_on)) if d["kind"] == "Cluster")["spec"]
        assert spec["postgresql"] == {"parameters": {"max_connections": "200", "max_wal_senders": "10", "wal_retrieve_retry_interval": "2s"}}, spec.get("postgresql")
        assert spec["bootstrap"] == {"initdb": {"database": "moimroom", "owner": "moimroom"}}, spec["bootstrap"]
        assert ("Service", "moimroom-db-to-legacy") not in objects and ("NetworkPolicy", "moimroom-db-to-legacy") not in objects
        assert ("SealedSecret", "moimroom-db-legacy") in objects
        assert [d["spec"]["replicas"] for d in docs if d["kind"] == "Deployment"] == [2, 0]
        assert not any("hopcharlie.dev/onboarding-cold" in d["metadata"]["labels"] for d in docs if d["kind"] == "Deployment")
        for name, change in (
            ("external in the peer's file", lambda v: v.update(env="gke")),
            ("external without pgLegacy", lambda v: v["values"]["sealedSecrets"].pop("pgLegacy")),
            ("external with another password Secret", lambda v: v["values"]["dbTopology"]["external"].update(passwordSecret="other")),
            ("external with members", lambda v: (v["values"]["dbTopology"].pop("peerPresent"), v["values"]["dbTopology"].update(members=["eks", "gke"]))),
            ("external that is not legacy", lambda v: v["values"]["dbTopology"]["external"].update(name="vm")),
            ("external host outside hop-db-legacy-", lambda v: v["values"]["dbTopology"]["external"].update(host="hop-db-eks-moimroom")),
            ("postgres image without a digest", lambda v: v["values"]["postgres"].update(image="postgres:17")),
            ("pgLegacy without a postgres resource", lambda v: (v["values"].pop("dbTopology"), v["values"]["sealedSecrets"].pop("pgApp"),
                                                                v["values"]["sealedSecrets"].pop("pgCA"), v["values"].update(resources=[{"name": "photos", "type": "s3"}]))),
            ("negative replicas", lambda v: v["values"]["services"][0].update(replicas=-1)),
        ):
            values = copy.deepcopy(onb)
            change(values)
            broken = work / "broken-values.json"
            broken.write_text(json.dumps(values))
            result = subprocess.run([HELM, "template", "moimroom", str(ROOT / "helm/charts/hopcharlie-app"), "-n", "moimroom", "-f", str(eks_on), "-f", str(broken)], capture_output=True, text=True)
            assert result.returncode != 0, f"hopcharlie-app accepts {name}"
        # Without profile.dbNetwork the source cannot be reached.
        result = subprocess.run([HELM, "template", "moimroom", str(ROOT / "helm/charts/hopcharlie-app"), "-n", "moimroom",
                                 "-f", str(work / "topology-profile-off-eks.json"), "-f", str(onb_file)], capture_output=True, text=True)
        assert result.returncode != 0, "hopcharlie-app accepts external without dbNetwork"
        for name, change in (
            ("dbTopology without pgCA", lambda v: v["values"]["sealedSecrets"].pop("pgCA")),
            ("dbTopology without pgApp and pgCA", lambda v: (v["values"]["sealedSecrets"].pop("pgCA"), v["values"]["sealedSecrets"].pop("pgApp"))),
            ("dbTopology without a postgres resource", lambda v: v["values"].update(resources=[{"name": "photos", "type": "s3"}])),
            ("dbTopology of other environments", lambda v: v["values"]["dbTopology"].update(primary="aws", peer="gcp")),
            ("dbTopology whose only environment is the file's", lambda v: v["values"]["dbTopology"].update(primary="eks", peer="eks")),
            ("unknown dbTopology key", lambda v: v["values"]["dbTopology"].update(role="primary")),
            ("peerPresent false in the replica's file", lambda v: v["values"]["dbTopology"].update(peerPresent=False)),
            ("peerPresent that is not a boolean", lambda v: v["values"]["dbTopology"].update(peerPresent="false")),
            ("empty members", lambda v: v["values"]["dbTopology"].update(members=[])),
            ("one member", lambda v: v["values"]["dbTopology"].update(members=["gke"])),
            ("four members", lambda v: v["values"]["dbTopology"].update(members=["gke", "eks", "onprem", "fourth"])),
            ("duplicate members", lambda v: v["values"]["dbTopology"].update(members=["gke", "eks", "eks"])),
            ("members missing primary", lambda v: v["values"]["dbTopology"].update(members=["eks", "onprem"])),
            ("members missing peer", lambda v: v["values"]["dbTopology"].update(members=["gke", "onprem"])),
            ("members missing env", lambda v: v.update(env="onprem", values={**v["values"], "dbTopology": {"primary": "gke", "peer": "eks", "members": ["gke", "eks"]}})),
            ("members with absent peer", lambda v: v["values"]["dbTopology"].update(members=["gke", "eks", "onprem"], peerPresent=False)),
            ("service collides with second remote", lambda v: (v["values"]["dbTopology"].update(members=["gke", "eks", "onprem"]), v["values"]["services"][0].update(name="db-to-onprem"))),
            ("pgApp without uri", lambda v: v["values"]["sealedSecrets"]["pgApp"].pop("uri")),
            ("service whose Service is the tailnet ingress", lambda v: v["values"]["services"][0].update(name="db-ts")),
        ):
            values = json.loads(topo_files["eks"].read_text())
            change(values)
            broken = work / "broken-values.json"
            broken.write_text(json.dumps(values))
            result = subprocess.run([HELM, "template", "moimroom", str(ROOT / "helm/charts/hopcharlie-app"), "-n", "moimroom", "-f", str(eks_profile), "-f", str(broken)], capture_output=True, text=True)
            assert result.returncode != 0, f"hopcharlie-app accepts {name}"
        for name, values_of, profile in (
            ("a backup bucket without its key", lambda v: v["values"]["sealedSecrets"].pop("pgBackup"), eks_profile),
            ("a backup key without its bucket", lambda v: v["values"].pop("backup"), eks_profile),
            ("a backup path that is not one path segment", lambda v: v["values"]["backup"].update(path="a/b"), eks_profile),
            ("a backup bucket without a path", lambda v: v["values"]["backup"].pop("path"), eks_profile),
            ("backups without the bucket's region", lambda v: v.update(profile={"s3": {"region": ""}}), eks_profile),
        ):
            values = copy.deepcopy(backed)
            values_of(values)
            broken = work / "broken-values.json"
            broken.write_text(json.dumps(values))
            result = subprocess.run([HELM, "template", "moimroom", str(ROOT / "helm/charts/hopcharlie-app"), "-n", "moimroom", "-f", str(profile), "-f", str(broken)], capture_output=True, text=True)
            assert result.returncode != 0, f"hopcharlie-app accepts {name}"
        for name, change in (
            ("app without appChecksum", lambda v: v["values"]["sealedSecrets"].pop("appChecksum")),
            ("reserved secret name", lambda v: v["values"]["sealedSecrets"]["app"].update(S3_BUCKET="AgA=")),
            ("ciphertext that is not base64", lambda v: v["values"]["sealedSecrets"]["s3"].update(accessKey="not base64")),
            ("unknown sealed key", lambda v: v["values"]["sealedSecrets"].update(db={})),
            ("profile reading another Secret", lambda v: v["profile"]["s3"].update(credentialsSecret="other")),
            ("env name that is also a secret", lambda v: v["values"]["services"][0]["env"].update(HOST_CODE="plain")),
            # web stops being public first, so only the missing port is wrong.
            ("public service without a port", lambda v: (v["values"]["services"][0].update(public=False), v["values"]["services"][1].update(public=True))),
            ("health check without a port", lambda v: v["values"]["services"][1].update(healthCheck="/healthz")),
            ("database variable in env", lambda v: v["values"]["services"][1]["env"].update(PGHOST="db")),
            ("database variable as a secret", lambda v: v["values"]["sealedSecrets"]["app"].update(PGPASSWORD="AgA=")),
            ("memory that is not a quantity", lambda v: v["values"]["services"][1].update(memory="512MB")),
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
