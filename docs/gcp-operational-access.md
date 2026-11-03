# Argo CD의 기존 GKE 운영 접근 준비

**2026-11-03 사용자 승인으로 실제 WIF/IAM·GKE RBAC·Argo 등록을 적용하고 인증 조회를 검증했다.** Headlamp의 개인 Google 로그인과 Argo CD의 자동 갱신용 워크로드 인증은 별개다. 결정은 [ADR0021](adr/0021-keyless-argocd-gke-access.md)을 따른다. 아래 절차는 승인 당시의 적용·검토 기록이며, 기존 리소스에 그대로 재실행하지 않는다.

## 인증·권한 범위

관리 EKS의 argocd-application-controller와 argocd-server가 특정 audience의 projected ServiceAccount JWT를 받는다. 별도 Google WIF provider가 두 subject만 검증하고 전용 Google 서비스 계정을 impersonate한다. argocd-k8s-auth gcp가 ADC 설정으로 단기 토큰을 갱신한다. GitHub WIF와 기존 AWS Pod Identity는 유지한다.

- Google IAM은 roles/container.clusterViewer와 해당 서비스 계정 impersonation만 추가한다. JSON 서비스 계정 key·AWS 장기 key·수동 bearer token을 생성하지 않는다.
- GKE RBAC는 전용 Google SA에 기본 view ClusterRole을 부여하고 승인된 앱 namespace에만 edit RoleBinding을 부여한다. edit는 그 namespace의 Secret·워크로드 변경을 포함한다. Headlamp의 조회 전용 권한과 혼동하지 않는다.
- Argo cluster 등록의 namespaces는 승인된 앱 namespace 목록, clusterResources는 false다. 이 단계에서는 CNPG CRD·StorageClass·cluster-wide RBAC를 Argo가 생성하지 못한다. 해당 운영자 설치는 서비스2에서 별도 승인·적용한다.
- charlie-apps는 기존 gcp-baseline release가 단독 소유해야 한다. 없으면 charlie-gcp-baseline/charlie-system 설치를 별도 승인받아 먼저 수행한다. access 차트는 createNamespaces=false를 유지하며 namespace 생성 우회로 사용하지 않는다.
- GKE API의 기존 AWS NAT /32 허용은 API 경로만 제공한다. Pod·DB·앱 간 데이터 경로는 서비스4의 별도 작업이다.

서비스2의 2026-11-03 적용 기록에서 charlie-gcp-baseline/charlie-system과 cnpg/cnpg-system 설치·검증을 완료했다. 이번 Argo 연동은 기존 baseline·CNPG의 수동 Helm 소유권을 보존한다. 두 release를 재설치하거나 자동으로 Argo Application에 인수하지 않는다. 실제 적용 전 존재·소유권을 다시 확인하고 예상과 다르면 중단한다.

## 오프라인 준비

별도 Terraform root는 [terraform/integrations/gcp/argocd](../terraform/integrations/gcp/argocd/main.tf)다. 기존 bootstrap/runtime state는 건드리지 않고 같은 승인된 GCS 버킷의 gcp/dev/argocd prefix를 사용한다. example 입력과 backend 파일은 실제 프로젝트·관리 EKS issuer로 준비한다. 입력/실제 backend 파일은 Git 밖에 둔다.

승인된 Terraform plan 검토와 apply 이후 이 root의 public output만 JSON으로 준비한다. 다른 root의 전체 output이나 state는 사용하지 않는다. **현재 live Helm values 전체는 디스크에 저장하거나 터미널에 출력하지 않는다.** controller/server의 실제 env·volumes·volumeMounts·KSA 이름을 승인된 조회로 메모리에서 읽어 prepare()에 전달한다. 저장소 기본 values는 live 구성의 대체물이 아니다.

이미 아래의 현재 목록이 안전한 public JSON으로 따로 준비된 경우에만 CLI의 --bootstrap-values를 사용할 수 있다. 일반적인 live 인수에서는 다음 Python API 경로를 사용한다. 이 예시는 향후 승인된 조회·적용 준비 절차이며 현재 실행하지 않았다.

```python
import importlib.util
import json
import os
from pathlib import Path
import subprocess

root = Path.cwd()
common = ["--kubeconfig", os.environ["MANAGEMENT_KUBECONFIG"],
          "--kube-context", "charlie-dev-management", "--namespace", "argocd"]
release = json.loads(subprocess.run(
    ["helm", "list", *common, "--filter", "^argocd$", "--output", "json"],
    check=True, capture_output=True, text=True).stdout)
if len(release) != 1 or release[0]["chart"] != "charlie-bootstrap-0.1.0":
    raise SystemExit("Live bootstrap release differs; stop and review its chart.")
# stdout remains in memory; never print or save the complete live values.
live = json.loads(subprocess.run(
    ["helm", "get", "values", "argocd", *common, "--all", "--output", "json"],
    check=True, capture_output=True, text=True).stdout)
selected = {"argo-cd": {}}
for component in ("controller", "server"):
    pod = live["argo-cd"].get(component, {})
    selected["argo-cd"][component] = {
        key: pod.get(key, []) for key in ("env", "volumes", "volumeMounts")}
    selected["argo-cd"][component]["serviceAccount"] = {
        "name": pod.get("serviceAccount", {}).get("name", "")}
spec = importlib.util.spec_from_file_location("prepare_gcp", root / "scripts/prepare-argocd-gcp.py")
module = importlib.util.module_from_spec(spec)
spec.loader.exec_module(module)
prepared = root / ".generated/gcp-access/prepared"
files = module.prepare(
    json.loads((root / ".generated/gcp-access/public-outputs.json").read_text(encoding="utf-8-sig")),
    json.loads((root / ".generated/gcp-access/gke-public.json").read_text(encoding="utf-8-sig")),
    selected)
# prepare() rejects conflicting identities/names and inline credential env values.
# Review the selected lists for other secret-bearing fields before writing files.
prepared.mkdir(parents=True, exist_ok=True)
for name, content in files.items():
    (prepared / name).write_text(json.dumps(content, indent=2) + "\n", encoding="utf-8")
```

준비 과정에서 inline credential 또는 비밀값 여부가 불분명한 기존 항목을 발견하면 출력 전에 중단한다. env 이름에 대한 자동 검사가 모든 비밀값을 찾아낸다고 가정하지 않는다. 기존 항목을 임의 삭제해서 검사를 통과시키지 말고, 별도로 승인된 Secret 참조 전환이나 인수 절차를 먼저 검토한다.

GKE 메타데이터는 다음 필드로 제한한다. 파일의 endpoint는 실제 Google endpoint이며 로컬 터널 주소가 아니다.

```powershell
gcloud container clusters describe charlie-dev-gcp-workload `
  --project=project-b47cdba3-db5b-42a2-9a6 --zone=asia-northeast3-a `
  --format="json(name,endpoint,masterAuth.clusterCaCertificate,status)"
```

기본 경로는 위 메모리 기반 Python API다. 아래 CLI는 현재 live에서 추출한 목록이 비밀값 없는 public pod 설정임을 확인해 별도로 준비한 경우에만 사용한다. 전체 Helm values 파일을 이 입력으로 저장하지 않는다.

```powershell
python scripts/prepare-argocd-gcp.py `
  --terraform-outputs .generated/gcp-access/public-outputs.json `
  --gke-metadata .generated/gcp-access/gke-public.json `
  --bootstrap-values .generated/gcp-access/selected-public-pod-config.json `
  --output-dir .generated/gcp-access/prepared
```

생성 결과는 세 파일이다.

| 파일 | 용도 |
|---|---|
| bootstrap-overlay.json | controller/server의 기존 env·volumes·mounts를 보존하고 ADC/JWT 설정 추가 |
| management-manifests.json | 비밀키 없는 ADC ConfigMap과 endpoint/CA/exec 인증 설정만 담은 Argo cluster Secret |
| gke-rbac-values.json | 전용 Google SA와 승인된 namespace RBAC 입력 |

이 도구는 subprocess·kubectl·클라우드 API를 호출하지 않는다. 임의 token endpoint/credential source, sensitive Terraform outputs, 다른 KSA, 기존 이름 충돌과 복사 대상의 inline credential env를 거부한다. 원본 values의 다른 설정은 overlay에 넣지 않으며 적용할 때 현재 values와 함께 유지한다. 출력 내용이 달라지면 새 출력 디렉터리에서 검토한다.

## 승인 후 적용 순서

승인 대상은 전용 WIF pool/provider/Google SA/IAM, GKE 지정 RBAC, 관리 EKS의 임시 접근 정책과 argocd ConfigMap/cluster Secret, controller/server의 projected volume/env와 재배포였다. **baseline·CNPG는 서비스2에서 설치했고, Argo 전용 변경도 이후 별도 승인으로 적용했다.** 아래는 적용 전에 사용한 검토 절차다. 재적용이나 유지보수에서는 현재 live release·서비스 계정·이미지를 다시 확인하고 새 변경 범위의 승인을 받는다.

고정 bootstrap 차트는 클러스터 범위 RBAC도 포함하므로 argocd namespace 한정 편집 정책만으로 Helm upgrade 권한을 보장할 수 없다. 임시 접근 계획은 AmazonEKSAdminViewPolicy의 클러스터 조회 권한과 argocd namespace 한정 AmazonEKSEditPolicy를 함께 사용하는 안이다. 전자는 Secret 조회 능력도 포함하지만 실제 조회는 기존 bootstrap 리소스와 argocd Helm 설정으로 제한하고 비밀값은 메모리에서만 처리한다. 클러스터 범위 쓰기·cluster-admin 권한은 부여하지 않는다.

Google IAM 생성 전에 현재 pinned chart와 live 설정으로 만든 dry-run 결과를 기존 Helm manifest 및 현재 live의 Helm 관리 필드와 메모리에서 비교한다. dry-run은 실제 patch 권한을 검사하지 않는다. 모든 클러스터 범위 리소스와 기존 RBAC가 같고 변경이 controller/server 인증 설정에 한정될 때만 진행한다. 클러스터 RBAC 변경이나 추가 권한이 필요하면 정책을 넓히지 않고 중단한다. 이번에 만든 임시 정책 연결과 AccessEntry만 작업 종료 시 회수한다.

기존 Argo와 Redis가 정상이며 Redis 인증 Secret 참조가 존재하는지 값 출력 없이 먼저 확인한다. 같은 버전의 인증 설정만 보완하는 이번 upgrade는 --no-hooks로 기존 redis-secret-init hook을 재실행하지 않는다. 기존 인증 Secret이 없거나 초기화 작업이 필요하면 이 범위로 진행하지 않는다.

1. 전용 Terraform root를 init/plan하고 생성·권한 범위를 검토한 뒤 승인된 apply를 실행한다. 기존 GitHub pool이나 runtime GKE를 이 root로 이동하지 않는다.
2. charlie-apps가 없으면 먼저 기존 gcp-baseline 차트를 charlie-gcp-baseline release로 승인받아 적용한다. Namespace 소유자는 baseline 하나로 유지한다. access 차트로 namespace를 대신 생성하지 않는다. baseline은 CNPG를 설치하지 않는다.
3. 위 메모리 기반 절차로 현재 live 배열을 보존한 결과를 생성하고 GKE에 gcp-argocd-access를 적용한다. createNamespaces=false를 유지한다.
4. 관리 EKS의 명시적 kubeconfig/context를 지정해 management-manifests.json을 적용한다. cluster Secret에 bearer/private key가 없더라도 자격 설정 변경이므로 승인 대상이다.
5. 현재 release가 저장소의 charlie-bootstrap 0.1.0/고정 argo-cd 10.9.6과 일치하는지 먼저 확인한다. controller/server 이미지의 v3.5.3 digest도 현재 pin과 비교한다. 하나라도 다르면 중단하고 해당 live chart 기준으로 다시 검토한다. 임의 chart/이미지 업그레이드를 이 작업에 섞지 않는다.

향후 적용할 명령의 형태는 다음과 같다. 경로는 절대 경로로 준비하고 두 context를 혼동하지 않는다.

```powershell
$managementKube = $env:MANAGEMENT_KUBECONFIG
$gkeKube = $env:GKE_KUBECONFIG
$prepared = Join-Path $PWD '.generated/gcp-access/prepared'
if (-not $managementKube -or -not $gkeKube) { throw 'Explicit kubeconfig paths are required.' }

# Service2 already installed the baseline. Check ownership without upgrading it.
$baseline = @(helm list `
  --kubeconfig $gkeKube --kube-context charlie-dev-gcp-workload `
  --namespace charlie-system --filter '^charlie-gcp-baseline$' --output json | ConvertFrom-Json)
if ($LASTEXITCODE -ne 0 -or $baseline.Count -ne 1 -or `
    $baseline[0].chart -ne 'gcp-baseline-0.1.0' -or `
    $baseline[0].status -ne 'deployed') { throw 'Baseline differs; stop without changes.' }
helm upgrade --install gcp-argocd-access helm/charts/gcp-argocd-access `
  --kubeconfig $gkeKube --kube-context charlie-dev-gcp-workload `
  --namespace charlie-system -f "$prepared/gke-rbac-values.json" `
  --set createNamespaces=false --wait --timeout 5m

# Recheck current Helm release immediately before generating/applying the overlay.
$release = @(helm list --kubeconfig $managementKube `
  --kube-context charlie-dev-management --namespace argocd `
  --filter '^argocd$' --output json | ConvertFrom-Json)
if ($LASTEXITCODE -ne 0 -or $release.Count -ne 1 -or `
    $release[0].chart -ne 'charlie-bootstrap-0.1.0') { throw 'Live chart differs; stop.' }
# Compare current live controller/server images to the pinned image in
# helm/bootstrap/values.yaml and helm/images.lock.json; stop on any difference.
kubectl --kubeconfig $managementKube --context charlie-dev-management `
  --namespace argocd get statefulset/argocd-application-controller deployment/argocd-server `
  -o jsonpath='{range .items[*]}{.metadata.name}{" "}{.spec.template.spec.containers[0].image}{"\n"}{end}'

# Resolve only the already pinned dependency version; do not update Chart.lock.
helm dependency build helm/bootstrap
# Keep dry-run output in memory; review redacted structural changes, not raw values.
$rendered = helm upgrade argocd helm/bootstrap --reuse-values --no-hooks `
  --kubeconfig $managementKube --kube-context charlie-dev-management `
  --namespace argocd -f "$prepared/bootstrap-overlay.json" `
  --dry-run=server --hide-secret
if ($LASTEXITCODE -ne 0) { throw 'Dry-run failed; do not apply.' }
# Compare against live manifests in memory. Only ADC/JWT additions to
# controller/server are intended; verify other images, KSA and resources unchanged.
# If live values changed since preparation, regenerate from live memory and review again.
kubectl --kubeconfig $managementKube --context charlie-dev-management `
  --namespace argocd apply -f "$prepared/management-manifests.json"
if ($LASTEXITCODE -ne 0) { throw 'Registration failed; stop before Helm upgrade.' }
helm upgrade argocd helm/bootstrap --reuse-values --no-hooks `
  --kubeconfig $managementKube --kube-context charlie-dev-management `
  --namespace argocd -f "$prepared/bootstrap-overlay.json" --wait --timeout 5m
```

--reuse-values는 기존 scalar/object 값을 보존하기 위한 것이며 Helm 배열을 자동 병합하지 않는다. 따라서 overlay의 env·volumes·volumeMounts는 **바로 전 live 배열을 읽어 보존한 결과**여야 한다. 저장소 values.yaml만으로 생성한 overlay나 오래된 live snapshot을 적용하지 않는다. 실제 dry-run/live manifest 비교에서 다른 변경이 보이면 적용을 중단한다.

## 실제 완료 확인과 복구

2026-11-03 실행 결과: WIF/IAM6개 생성, gcp-argocd-access/charlie-system revision1, argocd/argocd revision2를 확인했다. controller/server 모두 Ready1이며 두 Pod의 실제 WIF 토큰으로 GKE charlie-apps 조회 HTTP200과 권한 허용/거부12개를 검증했다. 기존 bootstrap의 변경은 두 workload 인증 설정뿐이고 기존 RBAC/CRD15개와 모든 기존 Secret은 동일했다. 기존 AWS 두 target은 Argo에서 Successful이다. 작업용 AccessEntry·두 정책·두 터널은 회수·부재 확인했다.

GKE는 등록됐지만 아직 Application이 없어 Argo 목록에 Unknown와 "Cluster has no applications and is not being monitored"가 표시된다. 실제 인증 실패와 구분한다. 앱 배포·cache/sync 동작은 서비스3 이후에 검증한다. 개인 Google 인증을 통한 Headlamp GKE 조회는 HTTP200이었고, 기존 AWS 두 Headlamp 화면 조회 정상은 사용자가 확인했다. 실제 1시간 만료 이후의 장시간 연속 시험은 하지 않았다. 코드는 아직 로컬 미커밋이며 Git 게시 승인은 별도다.

실제 적용에 사용한 로컬 Helm은3.18.3, CI 고정값은3.19.1이다. 두 버전의 빈 patch 생략 동작을 공식 소스에서 확인했고, 실제 upgrade에서도 기존 cluster 리소스·RBAC는 바뀌지 않았다.

- controller와 server가 정상 Ready이고 기존 EKS 두 target 연결이 유지되는지 확인한다.
- 양 Pod에서 projected token과 ADC 경로가 실제로 읽히는지 확인하되 token 내용을 출력하지 않는다.
- Argo GKE target에 namespace 조회/앱 범위 cache가 동작하고 Google token 교환·갱신이 성공하는지 확인한다. 토큰을 로그·채팅에 출력하지 않는다.
- charlie-apps 안의 승인된 변경과 namespace 밖 Secret/워크로드 변경 거부를 확인한다. cluster-scope 변경은 이 단계의 권한이 아니다.
- 이 등록은 앱·CNPG 복제·cross-cloud 네트워크·실제 이전 완료가 아니다.
- 롤백은 이번에 추가한 GKE cluster Secret/ADC 설정·overlay만 제거하고 이전 Helm revision으로 복구한다. GKE workload/PVC, 기존 EKS target·GitHub WIF는 삭제하지 않는다. WIF root 정리는 해당 신뢰를 쓰는 workload가 없는지 확인한 후 별도 승인한다.

## 검증

```powershell
python scripts/tests/test-argocd-gcp.py
python scripts/check-inventory.py
```

고정 argo-cd 10.9.6 chart의 실제 렌더 검증은 Helm CI에서 수행한다. [Google Kubernetes WIF](https://docs.cloud.google.com/iam/docs/workload-identity-federation-with-kubernetes)와 [고정 Argo gcp 인증 코드](https://github.com/argoproj/argo-cd/blob/v3.5.3/cmd/argocd-k8s-auth/commands/gcp.go)를 기준으로 준비했다. 실제 STS·IAM·RBAC 전달과 브라우저/Argo 연결은 적용 후 검증 대상이다.
