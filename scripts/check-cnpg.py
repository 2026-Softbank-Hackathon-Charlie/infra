#!/usr/bin/env python3
"""CNPG PoC의 고정 CRD·이미지와 AWS/GCP 배포 계약을 클라우드 접속 없이 검사한다."""
import importlib.util
import json
import os
from pathlib import Path
import re
import subprocess
import tempfile

import yaml

ROOT = Path(__file__).resolve().parents[1]
spec = importlib.util.spec_from_file_location("helm_checks", ROOT / "scripts/check-helm.py")
checks = importlib.util.module_from_spec(spec)
spec.loader.exec_module(checks)
DEPENDENCIES = {
    "cert-manager": "cert-manager",
    "cloudnative-pg": "cnpg-system",
    "plugin-barman-cloud": "cnpg-system",
}
REPO = "https://github.com/2026-Softbank-Hackathon-Charlie/infra.git"
VALUES = ROOT / "clusters/aws-dev-workload/values"
GCP_VALUES = ROOT / "clusters/gcp-dev-workload/values"


def pinned_image(ref):
    assert re.fullmatch(r".+@sha256:[a-f0-9]{64}", ref), f"Unpinned CNPG image: {ref}"
    name, digest = ref.rsplit("@", 1)
    assert checks.LOCK.get(name) == digest, f"CNPG image differs from lock: {name}"
    return ref


def operand_images(value):
    """Includes images used later by the operator, plugin and ACME solver."""
    if isinstance(value, dict):
        for key, child in value.items():
            if key in {"image", "imageName", "SIDECAR_IMAGE", "POSTGRES_IMAGE_NAME"} and isinstance(child, str):
                yield child
            yield from operand_images(child)
    elif isinstance(value, list):
        for child in value:
            yield from operand_images(child)
    elif isinstance(value, str) and value.startswith("--acme-http01-solver-image="):
        yield value.split("=", 1)[1]


def check_project(docs, project, namespace):
    checks.check_project(docs, project, namespace)
    allowed = {(item["group"], item["kind"]) for item in project["spec"]["namespaceResourceWhitelist"]}
    for doc in docs:
        if doc["kind"] not in checks.CLUSTER_KINDS:
            assert (doc["apiVersion"].rpartition("/")[0], doc["kind"]) in allowed, f"CNPG AppProject denies namespaced {doc['kind']}"


def structural(value, schema, path):
    """Reject unknown CR fields and wrong primitive shapes against fixed upstream CRDs.

    API admission/CEL, controller behavior and reconciliation still require the
    separately approved live dry-run/deployment. This is not an API-server emulator.
    """
    if value is None and schema.get("nullable"):
        return
    kind = schema.get("type")
    if kind == "object":
        assert isinstance(value, dict), f"Expected object: {path}"
        assert set(schema.get("required", [])) <= value.keys(), f"Missing required CR fields: {path}"
        if schema.get("x-kubernetes-preserve-unknown-fields"):
            return
        properties = schema.get("properties", {})
        additional = schema.get("additionalProperties", False)
        for key, child in value.items():
            if key in properties:
                structural(child, properties[key], f"{path}.{key}")
            elif isinstance(additional, dict):
                structural(child, additional, f"{path}.{key}")
            else:
                assert additional is True, f"Unknown CR field: {path}.{key}"
    elif kind == "array":
        assert isinstance(value, list), f"Expected list: {path}"
        for index, child in enumerate(value):
            structural(child, schema.get("items", {}), f"{path}[{index}]")
    elif kind == "string":
        assert isinstance(value, str), f"Expected string: {path}"
        assert "enum" not in schema or value in schema["enum"], f"Invalid CR enum: {path}"
    elif kind == "integer":
        assert isinstance(value, int) and not isinstance(value, bool), f"Expected integer: {path}"
    elif kind == "boolean":
        assert isinstance(value, bool), f"Expected boolean: {path}"
    elif kind == "number":
        assert isinstance(value, (int, float)) and not isinstance(value, bool), f"Expected number: {path}"


def check_cluster_update_policy(doc):
    """Match the pinned operator's single-instance admission constraint."""
    if doc.get("kind") == "Cluster" and doc.get("apiVersion", "").startswith("postgresql.cnpg.io/"):
        spec = doc["spec"]
        assert not (spec.get("instances", 1) == 1 and spec.get("primaryUpdateStrategy", "unsupervised") == "supervised"), "CNPG rejects supervised updates for single-instance clusters"


def check_comparison_policy(app):
    """Keep admission/zero-value normalization limited to the AWS DB app."""
    comparison = app["metadata"].get("annotations", {}).get("argocd.argoproj.io/compare-options")
    rules = app["spec"].get("ignoreDifferences", [])
    if app["metadata"]["name"] != "charlie-workload-cnpg-poc":
        assert comparison is None and not rules, "CNPG comparison policy must not affect dependency apps"
        return
    assert comparison == "ServerSideDiff=true,IncludeMutationWebhook=true"
    fields = ("superuser", "createdb", "createrole", "replication", "bypassrls")
    expressions = [f".spec.{field} | select(. == false)" for field in fields]
    expressions.append(".spec.inRoles | select(. == [])")
    expected = [
        {"group": "postgresql.cnpg.io", "kind": "DatabaseRole", "namespace": "charlie-db",
         "name": f"postgres-poc-svc-{service}-{role}", "jsonPointers": ["/metadata/generation"], "jqPathExpressions": expressions}
        for service in ("a", "b") for role in ("owner", "runtime")
    ]
    assert sorted(rules, key=lambda rule: rule.get("name", "")) == sorted(expected, key=lambda rule: rule["name"]), "Only four named Roles' generation and false/empty values may be normalized"
    assert "RespectIgnoreDifferences=true" not in app["spec"]["syncPolicy"]["syncOptions"]


def manual_applications():
    raw = (ROOT / "helm/cnpg-poc-applications.yaml.example").read_text()
    assert "REPLACE_WITH_WORKLOAD_EKS_API_ENDPOINT" in raw
    assert "REPLACE_WITH_REVIEWED_COMMIT_SHA" in raw
    fixture = raw.replace("REPLACE_WITH_WORKLOAD_EKS_API_ENDPOINT", "eks-workload.example.invalid")
    fixture = fixture.replace("REPLACE_WITH_REVIEWED_COMMIT_SHA", "1" * 40)
    fixture = fixture.replace("REPLACE_WITH_CNPG_BACKUP_BUCKET", "charlie-ci-cnpg-111111111111-ap-northeast-2")
    docs = [d for d in yaml.safe_load_all(fixture) if d]
    project = next(d for d in docs if d["kind"] == "AppProject")
    apps = [d for d in docs if d["kind"] == "Application"]
    assert len(apps) == 4
    assert {d["namespace"] for d in project["spec"]["destinations"]} == {"charlie-db", "cnpg-system", "cert-manager"}
    assert not any(d["kind"] == "Namespace" for d in docs)
    assert project["spec"]["destinations"] and all(d["server"] == "https://eks-workload.example.invalid" for d in project["spec"]["destinations"])
    assert not any(x["kind"] == "Namespace" for x in project["spec"]["clusterResourceWhitelist"])
    for app in apps:
        check_comparison_policy(app)
        assert app["metadata"]["namespace"] == "argocd"
        assert app["spec"]["project"] == project["metadata"]["name"]
        assert not app["metadata"].get("finalizers"), "Deleting an Application must not cascade-delete DB data"
        assert "operation" not in app and "automated" not in app["spec"]["syncPolicy"]
        assert set(app["spec"]["syncPolicy"]["syncOptions"]) == {"CreateNamespace=false", "ServerSideApply=true"}
        assert app["spec"]["destination"]["server"] == "https://eks-workload.example.invalid"
        sources = app["spec"].get("sources", [app["spec"].get("source")])
        for source in sources:
            assert source["repoURL"] in project["spec"]["sourceRepos"]
            if "chart" in source:
                pin = checks.VERSIONS["charts"][source["chart"]]
                assert source["repoURL"] == pin["repo"] and source["targetRevision"] == pin["version"]
            else:
                assert source["repoURL"] == REPO and re.fullmatch(r"[a-f0-9]{40}", source["targetRevision"])
    return project


def chart_at(name, work):
    pin = checks.VERSIONS["charts"][name]
    # Optional public-chart cache for local iteration; verify its Chart.yaml pin.
    cache = os.environ.get("CNPG_CHART_DIR")
    chart = Path(cache) / name if cache else work / name
    if not chart.exists():
        checks.helm("pull", name, "--repo", pin["repo"], "--version", pin["version"], "--untar", "--untardir", work)
    metadata = yaml.safe_load((chart / "Chart.yaml").read_text())
    assert metadata["version"] == pin["version"] and metadata["appVersion"] == pin["appVersion"]
    return chart


def main():
    assert checks.helm("version", "--short").startswith("v" + checks.VERSIONS["helm"])
    compatibility = checks.VERSIONS["cnpgCompatibility"]
    assert compatibility["kubernetes"] == checks.VERSIONS["kubernetes"]
    sdk = compatibility["barmanSidecarSdk"]
    for package in ("boto3", "botocore"):
        assert tuple(map(int, sdk[package].split("."))) >= tuple(map(int, sdk["minimumPodIdentitySdk"].split(".")))
    assert "/" + checks.VERSIONS["charts"]["plugin-barman-cloud"]["appVersion"] + "/" in sdk["source"]
    project = manual_applications()
    seen = set()
    crds = {}
    with tempfile.TemporaryDirectory(prefix="charlie-cnpg-check-") as directory:
        work = Path(directory)
        operand_default = None
        for name, namespace in DEPENDENCIES.items():
            chart = chart_at(name, work)
            docs = checks.render(chart, VALUES / f"{name}.yaml", name, namespace)
            assert not any(d["kind"] == "Namespace" for d in docs), "Namespaces belong only to workload baseline"
            assert not any(d["kind"] in {"PodMonitor", "ServiceMonitor", "PrometheusRule"} for d in docs)
            check_project(docs, project, namespace)
            seen.update(pinned_image(ref) for ref in operand_images(docs))
            if name != "cloudnative-pg":
                gcp_values = GCP_VALUES / f"{name}.yaml"
                assert yaml.safe_load(gcp_values.read_text()) == yaml.safe_load((VALUES / f"{name}.yaml").read_text()), "GCP must reuse the fixed AWS backup dependencies"
                gcp_dependencies = checks.render(chart, gcp_values, name, namespace, ("--kube-version", "1.36.0"))
                assert not any(d["kind"] == "Namespace" for d in gcp_dependencies)
                seen.update(pinned_image(ref) for ref in operand_images(gcp_dependencies))
            if name == "cloudnative-pg":
                configuration = next(d for d in docs if d["kind"] == "ConfigMap" and "POSTGRES_IMAGE_NAME" in d.get("data", {}))
                assert not configuration["data"].get("WATCH_NAMESPACE"), "CNPG must watch every application namespace"
                operand_default = configuration["data"]["POSTGRES_IMAGE_NAME"]
            for doc in docs:
                if doc["kind"] == "CustomResourceDefinition":
                    for version in doc["spec"]["versions"]:
                        crds[(doc["spec"]["group"] + "/" + version["name"], doc["spec"]["names"]["kind"])] = version["schema"]["openAPIV3Schema"]
        assert ("postgresql.cnpg.io/v1", "DatabaseRole") in crds
        assert ("barmancloud.cnpg.io/v1", "ObjectStore") in crds
        chart = ROOT / "helm/charts/cnpg-poc"
        values = VALUES / "cnpg-poc.yaml"
        # Missing S3 destination must fail closed rather than silently disable backups.
        result = subprocess.run([checks.HELM, "template", "cnpg-poc", str(chart), "-f", str(values), "--namespace", "charlie-db"], stdout=subprocess.PIPE, stderr=subprocess.PIPE, text=True)
        assert result.returncode != 0, "Default CNPG values must require an explicit backup bucket"
        docs = checks.render(chart, values, "cnpg-poc", "charlie-db", ("--set", "backup.bucketName=charlie-ci-cnpg-111111111111-ap-northeast-2"))
        assert not any(d["kind"] in {"Namespace", "Secret", "Ingress", "PodMonitor", "ServiceMonitor", "PrometheusRule"} for d in docs)
        check_project(docs, project, "charlie-db")
        seen.update(pinned_image(ref) for ref in operand_images(docs))
        for doc in docs:
            check_cluster_update_policy(doc)
            schema = crds.get((doc["apiVersion"], doc["kind"]))
            if schema:
                structural(doc["spec"], schema["properties"]["spec"], doc["kind"] + ".spec")
        cluster = next(d for d in docs if d["kind"] == "Cluster")
        assert operand_default == cluster["spec"]["imageName"], "Operator fallback must match the explicitly pinned operand"
        assert cluster["metadata"]["name"] == "postgres-poc" and cluster["spec"]["instances"] == 1
        assert cluster["spec"]["storage"]["storageClass"] == "cnpg-gp3-retain"
        assert cluster["spec"]["plugins"][0]["isWALArchiver"] is True
        assert "barmanObjectStore" not in cluster["spec"].get("backup", {})
        roles = [d for d in docs if d["kind"] == "DatabaseRole"]
        assert len(roles) == 4
        for role in roles:
            assert role["spec"]["databaseRoleReclaimPolicy"] == "retain"
            assert role["spec"]["login"] is True and role["spec"].get("passwordSecret", {}).get("name")
            assert all(role["spec"].get(key) is False for key in ("superuser", "createdb", "createrole", "replication", "bypassrls"))
        assert {d["spec"]["name"] for d in docs if d["kind"] == "Database"} == {"db_a", "db_b"}
        objects = [d for d in docs if d["kind"] == "ObjectStore"]
        assert len(objects) == 1
        config = objects[0]["spec"]["configuration"]
        assert config["s3Credentials"] == {"inheritFromIAMRole": True} and config["destinationPath"].endswith("/source/")
        assert "serverName" not in config, "Barman0.15 serverName belongs in Cluster plugin parameters"
        assert objects[0]["spec"]["retentionPolicy"] == "7d"
        gcp_docs = checks.render(chart, GCP_VALUES / "cnpg-poc.yaml", "cnpg-poc", "charlie-db", ("--kube-version", "1.36.0"))
        assert len(gcp_docs) == 16, "GCP parity must preserve the reviewed resource scope"
        assert not any(d["kind"] in {"Namespace", "Secret", "Service", "Ingress", "ClusterRole", "ClusterRoleBinding"} for d in gcp_docs)
        for doc in gcp_docs:
            check_cluster_update_policy(doc)
            schema = crds.get((doc["apiVersion"], doc["kind"]))
            if schema:
                structural(doc["spec"], schema["properties"]["spec"], "gcp." + doc["kind"] + ".spec")
        gcp_cluster = next(d for d in gcp_docs if d["kind"] == "Cluster")
        for key, value in cluster["spec"].items():
            if key != "storage":
                assert gcp_cluster["spec"][key] == value, f"GCP database contract changed: {key}"
        assert gcp_cluster["spec"]["storage"] == {"storageClass": "cnpg-gcp-pd-retain", "size": "20Gi"}
        assert gcp_cluster["spec"]["serviceAccountTemplate"]["metadata"]["annotations"] == {
            "iam.gke.io/gcp-service-account": "charlie-cnpg-poc-backup@project-b47cdba3-db5b-42a2-9a6.iam.gserviceaccount.com"
        }
        assert [d for d in gcp_docs if d["kind"] in {"Database", "DatabaseRole"}] == [d for d in docs if d["kind"] in {"Database", "DatabaseRole"}]
        storage = next(d for d in gcp_docs if d["kind"] == "StorageClass")
        assert storage["provisioner"] == "pd.csi.storage.gke.io" and storage["parameters"] == {"type": "pd-balanced"}
        assert storage["reclaimPolicy"] == "Retain" and storage["volumeBindingMode"] == "WaitForFirstConsumer" and storage["allowVolumeExpansion"] is True
        gcp_store = next(d for d in gcp_docs if d["kind"] == "ObjectStore")["spec"]
        assert gcp_store["configuration"] == {
            "destinationPath": "gs://charlie-414068132732-cnpg-poc-backup/source/",
            "googleCredentials": {"gkeEnvironment": True},
            "wal": {"compression": "gzip"}, "data": {"compression": "gzip"},
        }
        assert gcp_store["retentionPolicy"] == "7d" and "env" not in gcp_store["instanceSidecarConfiguration"]
        assert next(d for d in gcp_docs if d["kind"] == "ScheduledBackup")["spec"] == next(d for d in docs if d["kind"] == "ScheduledBackup")["spec"]
        network = next(d for d in gcp_docs if d["kind"] == "NetworkPolicy" and d["metadata"]["name"] == "cnpg-poc-database")["spec"]
        operator = network["ingress"][-1]["from"][0]
        assert operator["namespaceSelector"]["matchLabels"] == {"kubernetes.io/metadata.name": "cnpg-system"}
        assert operator["podSelector"]["matchLabels"] == {"app.kubernetes.io/name": "cloudnative-pg", "app.kubernetes.io/instance": "cnpg"}
        dns = next(d for d in gcp_docs if d["kind"] == "NetworkPolicy" and d["metadata"]["name"] == "cnpg-poc-dns")["spec"]["egress"]
        assert all(entry.get("to") for entry in dns), "DNS must not allow arbitrary destinations"
        extra_dns = {entry["to"][0]["ipBlock"]["cidr"]: {(port["protocol"], port["port"]) for port in entry["ports"]}
            for entry in dns if entry["to"][0].get("ipBlock")}
        assert extra_dns == {"169.254.20.10/32": {("TCP", 53), ("UDP", 53)}, "10.82.0.10/32": {("TCP", 53), ("UDP", 53)}}
        metadata = {entry["to"][0]["ipBlock"]["cidr"]: {(port["protocol"], port["port"]) for port in entry["ports"]}
            for entry in network["egress"] if entry.get("to", [{}])[0].get("ipBlock")}
        assert metadata == {"169.254.169.252/32": {("TCP", 987), ("TCP", 988)}}
        assert not any(marker in yaml.safe_dump(gcp_docs) for marker in ("s3Credentials", "AWS_REGION", "AWS_DEFAULT_REGION", "eks.amazonaws.com/role-arn", "s3://"))
        result = subprocess.run([checks.HELM, "template", "cnpg-poc", str(chart), "--namespace", "charlie-db",
            "--kube-version", "1.36.0", "-f", str(GCP_VALUES / "cnpg-poc.yaml"), "--set", "backup.gcpServiceAccount="], stdout=subprocess.PIPE, stderr=subprocess.PIPE, text=True)
        assert result.returncode != 0, "GCS backups must fail closed without their Workload Identity service account"
        seen.update(pinned_image(ref) for ref in operand_images(gcp_docs))
        example = ROOT / "examples/cnpg-poc"
        assert example.is_dir(), "Manual migration/verification Jobs are required"
        cases = [(service, mode) for service in ("a", "b") for mode in ("migrate", "verify", "network-denied", "marker", "fingerprint")]
        cases.append(("a", "recover-a"))
        for service, mode in cases:
            jobs = checks.render(example, example / "values.yaml", "cnpg-check", "charlie-apps", ("--set", f"service={service}", "--set", f"mode={mode}"))
            assert not any(d["kind"] in {"Secret", "Namespace"} for d in jobs)
            seen.update(pinned_image(ref) for ref in operand_images(jobs))
            assert not any(d["metadata"].get("annotations", {}).get("argocd.argoproj.io/hook") for d in jobs)
            job = next(d for d in jobs if d["kind"] == "Job")
            owner = mode in {"migrate", "recover-a"}
            assert job["metadata"]["namespace"] == ("charlie-db" if owner else "charlie-apps")
            pod = job["spec"]["template"]
            assert pod["spec"]["automountServiceAccountToken"] is False
            assert (pod["metadata"]["labels"].get("charlie.dev/cnpg-client") == "true") == (mode != "network-denied")
            credentials = next(v for v in pod["spec"]["volumes"] if v["name"] == "credentials")
            assert credentials["secret"]["secretName"] == f"svc-{service}-{'owner' if owner else 'runtime'}"
            environment = {v["name"]: v.get("value") for v in pod["spec"]["containers"][0]["env"]}
            assert environment["PGSSLMODE"] == "verify-full" and "PGPASSWORD" not in environment
            if mode == "recover-a":
                assert environment["POC_RESTORE_HOST"] == "postgres-poc-restore-rw.charlie-db.svc.cluster.local"
                assert any(v["name"] == "restore-ca" and v["secret"]["secretName"] == "postgres-poc-restore-ca" for v in pod["spec"]["volumes"])
        for invalid in (("b", "postgres-poc"), ("a", "postgres-poc-restore")):
            result = subprocess.run([checks.HELM, "template", "cnpg-check", str(example),
                "--set", "mode=recover-a", "--set", f"service={invalid[0]}", "--set", f"clusterName={invalid[1]}"],
                stdout=subprocess.PIPE, stderr=subprocess.PIPE, text=True)
            assert result.returncode != 0, "Selective recovery must only target source service A"
        restore = ROOT / "examples/cnpg-restore"
        assert restore.is_dir(), "Manual full restore/PITR chart is required"
        bucket = "charlie-ci-cnpg-111111111111-ap-northeast-2"
        for target_time in ("", "2026-11-03T05:30:00Z"):
            extra = ("--set", f"backup.bucketName={bucket}", "--set", "runId=trial-001")
            if target_time:
                extra += ("--set-string", f"targetTime={target_time}")
            restored = checks.render(restore, restore / "values.yaml", "cnpg-restore", "charlie-db", extra)
            assert len(restored) == 7 and {d["kind"] for d in restored} == {"Cluster", "DatabaseRole", "ObjectStore"}
            check_project(restored, project, "charlie-db")
            seen.update(pinned_image(ref) for ref in operand_images(restored))
            for doc in restored:
                check_cluster_update_policy(doc)
                schema = crds[(doc["apiVersion"], doc["kind"])]
                structural(doc["spec"], schema["properties"]["spec"], "restore." + doc["kind"] + ".spec")
            recovered = next(d for d in restored if d["kind"] == "Cluster")
            assert recovered["metadata"]["name"] == "postgres-poc-restore"
            assert recovered["spec"]["instances"] == 1 and recovered["spec"]["enableSuperuserAccess"] is False
            assert recovered["spec"]["imageName"] == operand_default
            assert recovered["spec"]["storage"]["storageClass"] == "cnpg-gp3-retain"
            recovery = recovered["spec"]["bootstrap"]["recovery"]
            assert recovery.get("recoveryTarget", {}).get("targetTime", "") == target_time
            external = next(d for d in recovered["spec"]["externalClusters"] if d["name"] == recovery["source"])
            output = recovered["spec"]["plugins"][0]
            assert output["isWALArchiver"] is True
            assert external["plugin"]["parameters"]["serverName"] == "postgres-poc"
            assert output["parameters"]["serverName"] == "postgres-poc-restore"
            input_name = external["plugin"]["parameters"]["barmanObjectName"]
            output_name = output["parameters"]["barmanObjectName"]
            stores = {d["metadata"]["name"]: d["spec"] for d in restored if d["kind"] == "ObjectStore"}
            assert input_name != output_name and len(stores) == 2
            assert stores[input_name]["configuration"]["destinationPath"] == f"s3://{bucket}/restore/trial-001/input/"
            assert stores[output_name]["configuration"]["destinationPath"] == f"s3://{bucket}/restore/trial-001/output/"
            for store in stores.values():
                assert store["configuration"]["s3Credentials"] == {"inheritFromIAMRole": True}
                assert "serverName" not in store["configuration"]
            for role in (d for d in restored if d["kind"] == "DatabaseRole"):
                assert role["spec"]["cluster"]["name"] == "postgres-poc-restore"
                assert role["spec"]["databaseRoleReclaimPolicy"] == "retain"
                assert all(role["spec"].get(key) is False for key in ("superuser", "createdb", "createrole", "replication", "bypassrls"))
        for invalid_run in ("", "../source", "TRIAL"):
            result = subprocess.run([checks.HELM, "template", "cnpg-restore", str(restore),
                "--namespace", "charlie-db", "--set", f"backup.bucketName={bucket}",
                "--set-string", f"runId={invalid_run}"], stdout=subprocess.PIPE, stderr=subprocess.PIPE, text=True)
            assert result.returncode != 0, "Restore archive paths must reject missing/unsafe run IDs"
        initial = yaml.safe_load((ROOT / "examples/cnpg-initial-backup.yaml.example").read_text())
        assert initial["kind"] == "Backup" and initial["spec"]["cluster"]["name"] == "postgres-poc"
        assert initial["spec"]["method"] == "plugin"
        structural(initial["spec"], crds[(initial["apiVersion"], "Backup")]["properties"]["spec"], "initial.Backup.spec")
        # Configuration strings select images that do not appear as image: fields.
        assert any("plugin-barman-cloud-sidecar:" in ref for ref in seen)
        assert any("postgresql:18.6-minimal-trixie@" in ref for ref in seen)
        assert any("cert-manager-acmesolver:" in ref for ref in seen)
    print(f"CNPG checks passed: fixed CRDs, AWS/GCP source profiles, GCS Workload Identity and metadata isolation, 4 manual apps, least-privilege roles, eleven manual Job variants, initial backup/full restore/PITR; {len(seen)} pinned images.")


if __name__ == "__main__":
    main()
