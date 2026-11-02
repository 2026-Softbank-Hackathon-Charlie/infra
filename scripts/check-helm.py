#!/usr/bin/env python3
"""AWS 기본 chart를 임시 디렉터리에서 lint/render하고 GitOps·이미지 계약을 검사합니다."""
import copy
import json
import os
from pathlib import Path
import re
import shutil
import subprocess
import tempfile

import yaml

ROOT = Path(__file__).resolve().parents[1]
HELM = os.environ.get("HELM", "helm")
VERSIONS = json.loads((ROOT / "helm/versions.json").read_text())
LOCK = json.loads((ROOT / "helm/images.lock.json").read_text())["images"]
yaml.SafeLoader.add_constructor("tag:yaml.org,2002:value", lambda loader, node: loader.construct_scalar(node))
CLUSTER_KINDS = {"Namespace", "StorageClass", "ClusterRole", "ClusterRoleBinding", "CustomResourceDefinition", "MutatingWebhookConfiguration", "ValidatingWebhookConfiguration", "APIService", "IngressClass", "IngressClassParams", "PriorityClass", "PersistentVolume", "CSIDriver"}


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


def main():
    assert helm("version", "--short").startswith("v" + VERSIONS["helm"]), "Use the pinned Helm version"
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
        for purpose in ("management", "workload"):
            values_root = ROOT / f"clusters/aws-dev-{purpose}/values"
            baseline_ns = "charlie-platform" if purpose == "management" else "charlie-apps"
            docs = render(ROOT / "helm/charts/cluster-baseline", values_root / "baseline.yaml", "baseline", baseline_ns)
            assert {"Namespace", "StorageClass", "ResourceQuota", "LimitRange", "NetworkPolicy"} <= {d["kind"] for d in docs}
            assert not any(d["kind"] in {"Ingress", "ClusterRole", "ClusterRoleBinding"} for d in docs)
            check_project(docs, projects[purpose], baseline_ns)
            for name in fixture["addons"][purpose] + ["opentelemetry-collector"]:
                pin = VERSIONS["charts"][name]
                chart_dir = work / "charts" / name
                if not chart_dir.exists():
                    chart_dir.mkdir(parents=True)
                    helm("pull", name, "--repo", pin["repo"], "--version", pin["version"], "--untar", "--untardir", chart_dir)
                chart = chart_dir / name
                namespace = "observability" if name in {"kube-prometheus-stack", "loki", "opentelemetry-collector"} else "kube-system"
                release = "monitoring" if name == "kube-prometheus-stack" else name
                extra = ("--set", f"clusterName=charlie-dev-{purpose}", "--set", "region=ap-northeast-2", "--set", "vpcId=vpc-0123456789abcdef0") if name == "aws-load-balancer-controller" else ()
                docs = render(chart, values_root / f"{name}.yaml", release, namespace, extra)
                check_project(docs, projects[purpose], namespace)
                seen.update(check_images(docs))
        # HopCharlie platform: before and after the shared ALB is turned on.
        platform_values = ROOT / "clusters/aws-dev-management/values/hopcharlie-platform.yaml"
        for ingress in (False, True):
            extra = ("--set", "ingress.enabled=true") if ingress else ()
            docs = render(ROOT / "helm/charts/hopcharlie-platform", platform_values, "hopcharlie-platform", "charlie-platform", extra)
            kinds = {d["kind"] for d in docs}
            assert {"StatefulSet", "Deployment", "Service", "NetworkPolicy", "ConfigMap", "ServiceAccount"} <= kinds
            assert ("Ingress" in kinds) == ingress
            assert not any(d["kind"] == "Secret" for d in docs), "Platform secrets stay in SSM"
            check_project(docs, projects["management"], "charlie-platform")
            seen.update(check_images(docs))
        print(f"Helm checks passed: bootstrap, GitOps staged/OTel, two baselines, all AWS addon values, HopCharlie platform; {len(seen)} pinned images.")


if __name__ == "__main__":
    main()
