# Argo CD root 최초 연결 및 PR4 플랫폼 배포 계획

- 날짜: 2026-11-03
- 상태: 자동 동기화 없는 준비 단계 승인·실행 완료 — 실제 sync/관리 주체 전환은 미실행
- 분류: 영향이 크거나 불확실한 작업. 두 클러스터의 namespace·NetworkPolicy·RBAC·CRD·PVC 생성과 AWS 접근을 수반한다.

> 앞부분은 최초 계획과 검토 이력이다. 최종 승인·실행 범위는 아래 “자동 동기화 없는 연결 준비”와 “준비 단계 실행 완료”를 따른다. 최초 계획의 자동 배포·빈 DB 가정은 현재 실행 범위에 적용하지 않는다.

## 목표와 완료 기준

현재 management EKS의 Application/ApplicationSet은 0개, AppProject는 default 하나다. Argo Pod 5개는 정상이고 repository 및 workload 등록 Secret은 존재한다. PR4는 하위 Application 템플릿만 추가했으므로 root를 최초 연결해야 한다.

기존 README의 최초 연결 절차와 ADR 0014 플랫폼 운영 결정을 적용한다. root 1개와 하위 Application 13개(관리 baseline·플랫폼·애드온 5개, workload baseline·애드온 5개), 하위 AppProject 2개가 의도한 target/revision으로 생성되고, 모든 앱이 Synced/Healthy이며 플랫폼 API·gateway·console·PostgreSQL이 Ready인 것을 완료 기준으로 한다. 외부 ingress는 꺼져 있으므로 외부 도메인 접속 성공은 완료 기준이 아니다. 미완료 항목은 오류와 함께 구분해 보고한다.

## 범위, 파일 및 제외 사항

- 계정 525091405886, ap-northeast-2, charlie-dev-management/charlie-dev-workload, 기존 charlie-admin·SSM bridge 사용.
- 실제 EKS endpoint/CA/VPC/name/version 및 기존 Argo용 IAM·Pod Identity·EKS access 설정, 플랫폼 hopcharlie-platform SA의 Pod Identity, Loki 버킷 존재와 위치, 플랫폼 ECR digest 존재, SSM 파라미터 이름·유형 메타데이터를 조회한다. SSM은 `DescribeParameters` 등 값이 반환되지 않는 API만 사용하고 `GetParametersByPath`/`GetParameter(s)`는 실행하지 않는다. 파라미터 값과 Secret 값은 조회하지 않는다.
- management의 cluster/repository/repo-creds Secret 메타데이터, 두 클러스터의 namespace·StorageClass·리소스 소유권/Pod 상태·PVC/자원 여유 및 Argo Application/AppProject/ApplicationSet spec/status/event를 조회한다. 기존 외부 management target 등록 여부는 Secret 메타데이터만으로 확정하지 않는다. 필요 시 승인 범위의 EKS 실제 endpoint/CA와 IAM 역할로 충돌하지 않는 management cluster Secret `dev-management-cluster` 하나를 create한다. label은 `argocd.argoproj.io/secret-type: cluster`, name은 `charlie-dev-management`, server는 실제 EKS HTTPS endpoint, config는 기존 README 계약의 `awsAuthConfig.clusterName/roleARN`과 `tlsClientConfig.insecure=false/caData`다. workload 등록 Secret은 덮어쓰지 않는다.
- `clusters/aws-dev-management/values/loki.yaml`의 두 버킷 placeholder만 조회로 확인한 실제 foundation Loki 버킷으로 치환한다. 기존 README의 후속 절차이며 새로운 저장 방식은 선택하지 않는다.
- Loki 수정은 해당 파일만 포함한 전용 branch/commit/PR로 제출한다. 사용자 승인에 해당 PR merge까지 명시적으로 포함된 경우에만, 검증 성공 및 의도한 diff 확인 후 merge한다. 다른 작업자의 변경은 포함하거나 되돌리지 않는다.
- `helm/root-application.yaml.example`을 기반으로 보호된 `.generated/root-application.yaml`에 실제 target 값을 넣는다. repoURL, main, 자동 sync(prune false/selfHeal true), 기존 addon 목록·ingress false는 유지한다. 합성 fixture/localhost를 실제 target으로 사용하지 않는다.
- 계획/실행 결과는 이 파일에, 계획 링크는 README 파일 목록에 추가한다. 기존 README/AGENTS.md와 다른 미추적 파일은 사용자 변경으로 보존한다.
- 제외: 실제 backend에 연결하는 Terraform init/plan/apply/state 접근(필수 PR CI의 `init -backend=false`/mock 검증은 허용), 인프라 변경 workflow dispatch, IAM/SG/Access Entry/Pod Identity 수정, 신규 자격 증명·토큰 발급, 기존 Secret 값 조회·변경, SSM 값 조회·변경, Pod exec/로그 조회, Argo 재설치/업그레이드, DNS/ACM/ALB/외부 ingress 활성화, 기존 EC2 철거/DB 이관, CNPG, 다른 앱·애드온 추가, PVC 삭제.

## 순서와 의존성

1. 새 plan-reviewer의 독립 검토 → 지적 반영 → 사용자에게 최종 계획·조회 및 배포 권한 범위 승인 요청. 현재 사용자의 작업 요청은 이 계획 승인이나 구체적인 클라우드 변경 승인으로 대신하지 않는다.
2. 승인 후 임시 보호 kubeconfig와 SSM 터널로 두 클러스터를 조회한다. 계정·클러스터 신원/버전 1.35·실제 endpoint/CA 확인, 예상 밖 기존 Application/ApplicationSet·리소스 소유권 충돌 여부를 확인한다. 등록 Secret 값은 읽지 않는다. live `argocd-cm`에서 Application health customization이 bootstrap의 하위 앱 health 전파 규칙과 일치하는지도 확인한다. 없거나 다르면 sync-wave의 준비 순서를 보장하지 못하므로 배포 전에 중단한다.
3. Loki S3, 플랫폼 Pod Identity 및 기존 역할 정책/신뢰, 해당 image digest, 필요한 SSM 이름 메타데이터를 확인한다. 플랫폼이 실제 값을 읽을 수 있다는 통합 보장은 배포 전 메타데이터 확인과 구분한다. 필수 역할 연결·파라미터·이미지·버킷 부재 또는 권한 부족이면 배포 전 중단하고 별도 변경안을 제시한다. 기존 EC2 DB 데이터는 이관하지 않으며 새 DB로 배포하고 트래픽은 전환하지 않는다.
4. 두 클러스터 target과 관리 deploy IAM 역할/Access Entry가 맞고 management 외부 target 등록이 없는 것이 확인되는 경우에만 정확한 endpoint 전용 cluster Secret 하나를 create한다. 이름/동일 target 충돌이 의심되면 덮어쓰기나 값 조회로 해결하지 않고 중단한다. workload target의 존재만으로 연결 성공을 단정하지 않는다. Secret 이름·label만으로 같은 endpoint의 기존 등록 유무를 확정할 수 없으면 생성하지 않고, 기존 인증된 Argo 세션의 클러스터 목록 조회로 확인하거나 별도 인증 승인을 요청한다. 세션 부재 시 admin password Secret 조회나 새 로그인/세션 발급으로 우회하지 않는다.
5. Loki 실제 버킷 수정안을 구현하고 Helm 및 diff 검증 후 전용 PR을 만든다. 승인된 merge 범위와 필수 CI 성공을 확인한 경우만 main에 반영한다. PR 생성·merge 권한이나 CI가 막히면 우회하지 않는다. root는 이 수정이 main에 반영되기 전 적용하지 않는다.
6. 실제 target으로 root 및 하위 chart를 로컬 렌더링한다. root/AppProject 이름 충돌 없음, 13개 하위 Application과 2개 프로젝트, 목적지, 자동 sync와 리소스 종류, 플랫폼 ingress 비활성화를 검증한다. 기존 StorageClass/CRD/RBAC 등과 소유권 충돌이 없고 자원 여유가 있는지 확인한다. root/AppProject의 server dry-run으로 API/RBAC 구조를 검증하되 하위 전체 자원 검증으로 표현하지 않는다.
7. 적용 직전에 Application/ApplicationSet·root/project/등록 메타데이터와 main 변경을 다시 확인한다. 검토 이후 관련 main이 바뀌었거나 다른 배포가 생기면 중단한다. 의도한 root AppProject와 Application만 create하고, 하위 앱은 기존 자동 sync에 맡긴다. root 생성은 baseline/애드온/플랫폼의 실제 자동 배포 실행 승인을 포함해야 한다.
8. 최대 15분간 앱 status/conditions/sync 결과, Pod/init-container 상태·이벤트, PVC 상태로 확인한다. 모든 앱 Synced/Healthy 및 플랫폼 Ready 여부를 보고한다. 실패가 나면 오류를 분류하고 임의 retry/patch/restart/권한 수정은 하지 않는다. 필요 변경이 승인 범위를 벗어나면 별도 계획을 제시한다.
9. 성공·실패 모두 이번 임시 터널·kubeconfig·Secret 입력 임시파일을 정리한다. 로컬 root manifest는 비밀값이 없으며 0600으로 보관한다. 실제 생성 리소스는 자동 철거하지 않는다. 실행 결과·한계·승인 범위·계획 대비 변경을 기록한다.

## 검증

- 검증 도구는 기존 `/private/tmp/charlie-tools/darwin-arm64/helm`의 3.19.1을 버전 확인 후 사용하고, 기본 Python에 없는 PyYAML 6.0.3은 보호된 임시 venv에 설치한다. 공개 의존성 다운로드만 허용하고 저장소 의존성이나 글로벌 환경은 바꾸지 않는다.
- Loki diff가 두 placeholder 치환으로 제한됨을 확인한다. `git diff --check`, 고정 Helm 3.19.1/Kubernetes 1.35로 `make helm-check`, `make inventory-check` 및 해당 PR CI.
- 기존 inventory 누락이나 로컬 도구 불일치는 변경으로 생긴 실패와 구분하고 검사 통과로 표현하지 않는다. `make inventory-check`의 기존 실패인 ignored `helm/bootstrap/charts/argo-cd-10.9.6.tgz` README 링크 누락 하나만 baseline 예외로 제안한다. 별도 읽기 전용 검사로 이 한 파일을 제외한 모든 파일 목록·로컬 참조 검사를 동일하게 수행하여 통과해야 한다. 다른 inventory 실패 또는 Helm/CI 실패가 있으면 배포를 진행하지 않는다. 이 예외는 최종 승인 요약에 명시한다.
- 로컬 렌더링은 실제 repo 인증·IAM·SSM·이미지 pull·운영 상태의 증명이 아니다. 실제 검증은 Application/Pod/PVC 상태까지이며 API 업무 기능·외부 접속·기존 DB 데이터 연속성은 검증하지 않는다.

## 권한·영향·중단 및 복구

필요 권한은 기존 AWS STS/EKS/EC2/SSM 접속·IAM/ECR/S3 및 파라미터 메타데이터 읽기, Kubernetes 두 클러스터 읽기와 management cluster 등록 Secret create(필요 시), root Application/AppProject create, Argo의 기존 deploy 권한을 통한 두 클러스터 리소스 자동 생성·수정이다. GitHub 비공개 저장소/CI 조회와 Loki 단일 파일 PR 생성·merge도 명시적 승인 범위에 포함해야 한다. 새 자격 증명 발급은 없다.

배포는 NetworkPolicy·RBAC·CRD·webhook·namespace 설정을 만들며 기존 namespace argocd의 baseline label에도 영향을 준다. EBS PVC 및 관측 스택의 저장공간/CPU/메모리 비용이 발생한다. PostgreSQL은 새 빈 DB이고 ingress는 비활성화되어 기존 EC2 트래픽과 데이터는 이동하지 않는다. root와 하위 앱은 이후 main merge도 자동 배포할 수 있다. prune false는 rollback이나 무영향을 보장하지 않는다.

실패 시 진단을 보고하고 추가 배포 조작을 보류한다. 최초 배포라 이전 정상 Argo revision이 없으며, root만 삭제해도 하위 앱/생성 자원이 자동 복구되지 않는다. 자동 sync 중지나 리소스 철거는 별도 구체적인 승인을 받고 진행하며 PVC/DB 삭제는 포함하지 않는다. 기존 서비스나 데이터 손상 가능성이 확인되면 root 생성 전 중단한다.

## 관련 ADR 및 미확인 사항

- ADR 0002 권한 작업 사전 승인, 0010 기존 EKS 운영자, 0014 로컬 EKS 접속, 0014 플랫폼 관리 클러스터 운영을 따른다.
- root 최초 적용·기존 target 등록·기존 Loki placeholder 치환은 README에 이미 정한 운영 방식의 반복 적용이므로 새 ADR은 필요하지 않다. 설계/소유권/정책을 변경해야 한다면 실행을 멈추고 새 ADR과 변경 계획을 작성한다.
- 미확인: 두 target 통합 연결, management 외부 target 등록, 플랫폼 Pod Identity/SSM/ECR 준비, Loki 실제 버킷, 기존 자원 소유권·여유 자원, GitHub PR/CI 권한. 이들은 승인 후 조회로 확인할 배포 전 필수 gate다.

## 독립 리뷰

새 plan-reviewer의 예비 리뷰에서 기존 inventory 실패와 필수 통과 gate의 모순(중요), management 등록 Secret 계약·중복 검증, 고정 검증 도구 및 live Application health customization 확인 보완을 요청했다. 이 문서에 모두 반영했고 같은 리뷰 에이전트의 재검토 결과 GREEN, 중대한 미해결 지적 없음으로 확인했다. 리뷰 통과는 사용자 실행 승인이 아니다.

## 승인 전 로컬 검증

- Helm 3.19.1 / Kubernetes 1.35.0의 strict lint/render: GitOps(하위 Application 13개/AppProject 2개), 두 baseline, 플랫폼 차트 통과. 플랫폼 Ingress 미생성 확인. 합성 target 검증이며 실제 배포 검증은 아니다.
- `git diff --check` 통과. 전체 `make inventory-check`는 기존 ignored bootstrap chart tgz의 README 링크 누락으로 실패.
- 알려진 tgz 누락 한 건만 제외하여 inventory 파일 목록·로컬 참조 검사를 별도로 수행한 결과 180개 파일, 추가 오류 0건. 원래 `make inventory-check` 실패는 그대로 보고한다.
- 전체 `make helm-check`는 아직 미실행. 기본 Python에 PyYAML이 없어 임시 venv 준비가 필요하다.

## 승인된 범위 실행 결과 및 중단 — 2026-11-03

- 사용자가 최종 계획/실행 범위를 승인하고 코드 수정은 새 브랜치에서 진행하도록 지시했다.
- 별도 worktree `/private/tmp/charlie-root-deploy/worktree`, branch `fix/argocd-root-loki-bucket`을 원격 main `7a3fc8e`에서 생성했다. 사용자 작업 디렉터리의 기존 변경은 보존했다.
- AWS 계정·두 EKS ACTIVE/1.35/private API/동일 VPC, Loki 버킷의 서울 리전 존재, 플랫폼 ECR digest 두 개, SSM 필수 이름, 플랫폼 SA의 Pod Identity 연결을 확인했다. Secret/SSM 값은 읽지 않았다. IAM 정책의 완전한 검증과 workload 통합 연결은 완료하지 않았다.
- management live Application/ApplicationSet 0개, default 프로젝트 하나, 하위 Application health customization 존재를 확인했다.
- **새로 확인한 중요 사실:** management에서 플랫폼 Pod 네 개 Ready, PVC 세 개 Bound(10/2/1GiB), baseline gp3에 Helm release `baseline`/namespace `charlie-platform` annotation이 존재한다. 플랫폼과 LBC에도 Helm managed-by label이 있다. 최초 배포가 아니라 기존 Helm 리소스 관리 주체 전환이 필요하다. DB의 기존 데이터 존재 여부는 조회하지 않았다.
- 기존 Helm 배포를 변경하거나 root/등록 Secret을 생성하지 않았다. 실제 자원 충돌/관리 주체 변경 가능성이 있어 승인된 계획의 중단 조건을 적용했다. 원격 main merge도 보류한다.
- Loki 실제 버킷 두 곳 치환을 새 브랜치에서 구현했다. 전체 `make helm-check`(Helm 3.19.1/PyYAML 6.0.3, 21개 고정 이미지), 깨끗한 worktree의 `make inventory-check`(201개 파일), `git diff --check`가 통과했다. 원래 사용자 작업 디렉터리의 기존 inventory 예외와 구분한다.
- 기존 CLI Argo 인증 설정이 없었고 Firefox 세션 확인은 컴퓨터 사용 자동 승인 검토에서 거절되어 사용하지 않았다. 새 로그인이나 Secret 조회로 우회하지 않았다.
- management 후속 release annotation 조회 및 workload Kubernetes 조회는 임시 SSM 연결 timeout/종료로 완료하지 못했다. workload에 기존 리소스가 없다고 단정하지 않는다.

## 변경 계획 — 기존 Helm 리소스 보존 전환 (제안)

기존 계획의 조회·Loki 수정·검증 범위는 유지한다. 기존 최초 배포 가정을 폐기하며 다음 추가 범위는 새 독립 리뷰와 사용자 승인 전 실행하지 않는다.

1. **목표/완료 기준:** 기존 관리 클러스터의 실행 서비스·PVC/DB를 보존하면서 Argo에 연결한다. 기존 볼륨 UID/PV, StatefulSet identity 및 replica Ready를 전후 기록하고, 기존 자원의 의도하지 않은 Pod rollout이나 PVC 변경 없이 root와 하위 앱이 정상 관리되는 것을 확인한다. 신규 관측 애드온 배포는 기존 승인한 목록만 허용한다.
2. **추가 조회 승인 대상:** management `argocd/dev-workload-cluster`의 등록 데이터(name/server/config)를 메모리에서 읽어 실제 workload endpoint·CA·IAM role 설정과 일치하는지 확인한다. 등록 내용에는 자격 증명이 포함될 가능성이 있으므로 기존의 Secret 값 조회 제외 범위와 구분하여 명시적 승인받는다. 값·토큰·CA 원문은 출력/저장/문서화하지 않는다. 기대한 IAM 방식 외 bearerToken·password·clientKey 등 인증 형식이면 그 값을 출력하거나 사용하지 않고 중단한다. 다른 Secret 값과 SSM 값은 계속 제외한다. 이 한정 확인과 표준 label Secret 전체 메타데이터로 중복 여부가 불명확하면 management 등록을 생성하지 않는다.
3. **소유권 및 입력 검토:** 두 클러스터에서 기존 deployment/statefulset/namespace/StorageClass/정책/LBC의 release annotation, Argo tracking 유무, image·Pod template·PVC/서비스·보안 필드를 확인하고 실제 target으로 고정한 chart 렌더링과 비교한다. Kubernetes 기본값과 기존 런타임 필드, Argo tracking metadata를 실제 설정 변경과 구분한다. Secret 원문을 포함하는 Helm release archive/get-values/get-manifest 조회는 허용하지 않는다. 차이를 안전하게 확인할 수 없으면 해당 리소스는 동기화하지 않는다.
4. **동시 관리 방지:** 기존 Helm 배포의 실행 주체와 자동 upgrade/rollback 경로가 중지되어 있는지 사용자에게 확인한다. 명확하지 않으면 adoption을 시작하지 않는다. 다른 저장소의 CI/IAM/배포 설정은 변경하지 않는다.
5. **적용 조건:** 기존 Pod template·StatefulSet immutable/data 필드·PVC spec·기존 네트워크/권한/외부 노출에 의도하지 않은 차이가 없고, 기존 자원을 재생성하지 않는 것이 확인된 경우에만 기존 계획의 management 등록(필요 시)·Loki PR merge·root 생성 순서로 진행한다. 차이가 발견되면 정리된 diff와 위험을 제시하고 변경 계획을 다시 리뷰·승인받는다. 강제 덮어쓰기/Replace/Force/uninstall/PVC 삭제를 사용하지 않는다.
6. **자동 동기화 위험:** root 생성 뒤에는 13개 하위 앱 자동 sync가 시작된다. 단순 tracking metadata 추가는 허용하지만 기존 Pod rollout·데이터 리소스 변경을 함께 승인받았다고 간주하지 않는다. 기존 selfHeal/prune false, ingress false를 유지한다. 새로운 차이나 실패가 있으면 추가 조작을 보류하고 보고한다. 이미 생성한 리소스를 자동 삭제하지 않는다.
7. **ADR/파일:** [ADR 0017](../adr/0017-adopt-existing-helm-resources-in-argocd.md)를 제안으로 작성했다. 승인 이후 전환 결정을 채택하고 실제 실행 여부를 따로 기록한다. 코드 수정은 계속 새 브랜치에서 수행하고 Loki 단일 파일 PR과 계획/ADR 문서는 분리한다. README 파일 목록에 ADR·계획 링크를 유지한다.
8. **검증 및 복구 한계:** 전체 Helm·inventory·필수 PR CI, actual target 렌더링과 server dry-run, 소유권/불변·데이터 필드 비교, 전후 Pod/PVC 확인을 수행한다. 차이가 없어 보이는 로컬 비교만으로 무중단을 보장하지 않는다. 기존 Helm release uninstall/rollback과 PVC 삭제는 복구 수단으로 자동 실행하지 않는다. 실패 시 수동 변경·sync 중지는 별도 구체적인 승인 범위에서만 수행한다.

변경 계획 최초 독립 리뷰: YELLOW. 자동 sync가 시작된 뒤의 중단 수단 미비를 지적받아 아래의 자동 동기화 없는 준비 단계로 범위를 수정했다. 기존 Helm 실행 주체의 동시 작업 여부는 사용자 확인이 필요한 미해결 조건이다.

### 변경 계획 리뷰 반영: 이번 승인 대상은 자동 동기화 없는 연결 준비로 제한

위 변경 계획 5~6단계의 자동 동기화 실행 제안을 **대체**한다. 기존 계획의 root 자동 배포 실행 범위도 현재 상태에서는 사용하지 않는다. 이번 변경 계획의 완료 기준은 workload 변경이 아니라, 안전한 diff 검토 자료와 자동 동기화가 꺼진 Argo 연결 준비다. 기존 서비스의 실제 관리 전환/동기화는 검토한 diff를 제시한 뒤 별도 실행 승인을 받는다.

- `argocd/dev-workload-cluster`는 Kubernetes API 특성상 **Secret 객체 한 개 전체가 프로세스 메모리에 반환되는 조회**임을 새 승인 범위에 명시한다. name/server/config만 검사하고 원문·다른 필드·인증값을 출력/저장하지 않는다. 처리 중 예기치 않은 인증 형식이 발견되면 사용하지 않고 중단한다.
- 필수 선행 조회와 Helm 동시 작업 중지 확인 후 실제 target으로 렌더링한 root 및 하위 manifests의 모든 Application에서 `spec.syncPolicy.automated`와 실행 요청 `operation`을 제거한 보호된 임시 입력을 만든다. repo의 기본 자동 sync 코드는 바꾸지 않는다. root 자체도 자동 sync를 끄고 sync operation을 요청하지 않는다.
- 예상 Application 14개(root 1+하위13), AppProject 3개(root1+하위2)를 각각 수동으로 create한다. 기존 같은 이름 객체가 있으면 덮어쓰지 않고 중단한다. root가 자동으로 하위 객체를 만들도록 sync시키지 않는다. 사전 client/server dry-run을 수행하고 생성 직전 자동화와 operation 부재, AppSet/기존 앱 충돌 부재를 재검증한다.
- 생성 직후 및 준비 단계 종료 시 모든 Application에서 automated가 없거나 비활성화이고 `.operation`이 없어 대기 중 operation도 없으며, operationState가 Running/Terminating이 아닌지 확인한다. 이 준비 단계의 OutOfSync는 예상된 상태이며 완료된 동기화로 표현하지 않는다. sync·refresh 요청·자동 동기화 활성화·리소스 채택·workload 변경은 이번 단계에서 하지 않는다. Argo 자체의 비교/상태 갱신은 있을 수 있지만 배포 실행으로 표현하지 않는다. 예상 밖 기존 sync 실행/다른 controller가 발견되면 객체를 생성하지 않는다.
- 기존 Helm/live 리소스와 렌더링의 diff, 데이터/immutable 필드, Pod/PVC identity, 기존 release 관리 주체를 정리한다. Secret을 포함한 diff 원문은 출력하지 않는다. 안전하게 검토할 수 없는 차이는 명시하고 workload sync를 실행하지 않는다. 새 Argo 인증/로그인은 수행하지 않는다.
- 실제 배포 전환의 후속 계획에는 승인된 동기화 대상/revision과 순서, 자동화 활성화 및 이미 진행 중 operation의 종료를 구분한 구체적인 중단 절차/인증 경로를 포함해야 한다. 그 절차를 검증·승인받기 전에는 자동화 활성화나 수동 sync를 요청하지 않는다. 자동화 해제만으로 이미 진행 중 operation이 멈춘다고 표현하지 않는다.
- 보류된 Loki PR은 필수 CI·diff 확인과 기존 승인 범위 내에서 merge할 수 있지만, 이 준비 단계에서는 어떤 Application도 sync하지 않는다. CI가 실패하면 우회하지 않는다.
- 문서/ADR은 이번 단계가 관리 주체 전환 준비이며 실제 채택은 미완료임을 유지한다. 최종 보고에 생성된 객체·비활성화 확인·기존 서비스/PVC 유지와 후속 승인 조건을 구분한다.

### 코드 수정 및 검증 최종 기록

- Loki 전용 commit: `40c655c`, branch `fix/argocd-root-loki-bucket`, [draft PR #8](https://github.com/2026-Softbank-Hackathon-Charlie/infra/pull/8). 수정은 버킷 placeholder 두 곳만이며 merge 미실행.
- PR CI: check/Helm/스크립트/Terraform·GCP 검증 모두 SUCCESS, deploy job은 SKIPPED. 수동 인프라 변경 workflow는 실행하지 않았다.
- 로컬 worktree: 전체 Helm·inventory·diff 검사 통과. 사용자 작업 디렉터리의 계획/ADR 링크는 기존 ignored tgz 한 건을 제외한 inventory 기준선 검사 통과. 원래 make inventory-check의 기존 실패는 그대로다.
- 이번 터널 프로세스 세 개 모두 종료 확인. 원격 TerminateSession은 이미 유효한 종료 가능 상태가 아니라는 ValidationException을 반환했다. 임시 kubeconfig·클러스터 조회 파일은 삭제했다. 코드 worktree/검증 venv는 보존했다.
- 계획 대비 변경: 기존 Helm 관리 자원 발견으로 root/클러스터 등록 Secret 생성·main merge·실제 배포를 보류하고, 자동 동기화 없는 준비 단계로 변경 계획을 제안했다.
- ADR: 0015를 제안으로 작성했으며 관리 주체 전환은 실행하지 않았다.

### 변경 계획 재검토 결과

새 plan-reviewer가 최종 준비 단계 문구와 ADR 변경을 재검토하여 GREEN, 중대한 미해결 지적 없음으로 판정했다. queued operation 및 Running/Terminating 부재 확인 권고를 반영했다. 이는 사용자 실행 승인이 아니며, 기존 Helm 동시 작업 중지 확인과 특정 cluster Secret 객체 조회·자동화 없는 Application/AppProject 등록 준비의 명시적 승인을 기다린다.

## 준비 단계 실행 완료 — 2026-11-03

### 승인과 실제 변경

사용자는 별도 Helm upgrade/install/rollback을 진행하지 않는 조건과 실행 중인 서비스 유지에 대한 설명 후 “진행해줘”라고 승인했다. 승인 범위는 특정 workload 등록 Secret 한 개의 메모리 조회, management target 등록(필요 시), 자동화/operation 없는 Argo 객체 생성 및 비교였다. 실제 workload sync·Helm 변경·DB/PVC 변경은 실행하지 않았다.

- 계정 525091405886, 서울 리전, 두 EKS ACTIVE/1.35/동일 VPC를 재확인했다.
- `argocd/dev-workload-cluster` 한 개의 전체 Secret 응답을 메모리에서 받아 name/server/config만 검증했다. 실제 workload identity/endpoint/CA/IAM 역할이 일치하고 기대한 IAM 인증 형식임을 확인했다. 원문·토큰·CA는 보고서나 파일에 저장하지 않았다. 기존 repository/다른 Secret 값과 SSM 값은 읽지 않았다.
- management deploy 역할의 EKS cluster 범위 관리 정책 연결을 확인한 뒤 `argocd/dev-management-cluster`를 create했다. 기존 workload 등록은 변경하지 않았다. 신규 자격 증명은 발급하지 않았다.
- Loki 전용 PR #8을 검증된 head `40c655c`로 merge했다. merge commit은 `bea060e14133f82fa46dc6ac5a51f6f329b72e96`. 필수 CI 성공을 확인했으며, 실제 인프라 apply workflow를 실행하지 않았다.
- 입력에서 모든 `spec.syncPolicy.automated`와 `operation`을 제거했다. 서버 dry-run 후 수동 Application 14개(root 1+하위 13), AppProject 3개(root 1+하위 2)를 create했다. 기존 `default` 프로젝트는 보존했다.
- root/하위 앱의 자동 sync는 비활성화이며 queued operation 및 Running/Terminating operation이 모두 없음을 생성 직후와 최종 검증에서 확인했다. 수동 sync·강제 refresh도 요청하지 않았다.

### Argo 관측 결과

| 대상 | Sync | Health | 조건 오류 |
| --- | --- | --- | --- |
| management baseline·플랫폼·LBC (3개) | OutOfSync | Healthy | 없음 |
| 미설치 management 애드온 4개와 workload baseline·애드온 6개 (10개) | OutOfSync | Missing | 없음 |
| charlie-root | OutOfSync | Progressing | 없음 |

비교가 성공했다는 결과와 배포 완료는 구분한다. Missing은 해당 리소스를 이번 준비 단계에서 설치하지 않았기 때문이며 OutOfSync는 의도한 수동 상태다. **root를 Sync하면 Git의 하위 Application 자동 sync 설정이 복원되어 실제 배포가 시작될 수 있으므로, 후속 검토·승인 전에는 root/하위 앱을 Sync하지 않는다.**

### 기존 리소스 보존 및 비교

- management Pod 23개·PVC 3개와 workload Pod 12개·PVC 0개를 전후 비교했다. Pod UID/Ready/phase/restart count, PVC UID/PV/spec hash가 모두 유지됐다.
- 현재 main에는 이전 로컬 브랜치에 없던 PR #5/#6의 Terraform 소유권 이관·플랫폼 이미지 및 ALB Ingress 설정이 이미 포함되어 있었다. 기존 main을 변경/되돌리지 않았고 이번 단계에서 이를 실제 적용하지 않았다. 기존 계획의 ingress false 가정과 현재 main을 구분한다.
- 실제 management baseline·플랫폼의 선언 리소스 23개가 존재하고 비교한 desired 필드(보안 label·Pod template 포함)는 일치했다. 이 검사는 Kubernetes 기본값/런타임 필드 등을 전부 대칭 비교한 것 또는 Argo 실제 sync 결과는 아니다.
- workload baseline 리소스 6개와 LBC 선언 리소스 11개는 아직 없다.
- management LBC 선언 리소스 11개가 존재한다. TLS Secret은 조회하지 않았고 webhook CA는 기존 ignoreDifferences에 따라 비교에서 제외했다. ValidatingWebhookConfiguration `aws-load-balancer-webhook`의 세 번째 webhook `objectSelector`/`namespaceSelector`에는 차이 후보가 남아 있다. 후속 실제 sync 검토 전에 의미·기본값 여부를 확인해야 한다.
- Helm release archive, Secret을 포함한 Helm get-values/get-manifest, Pod exec/log, state, 실제 Terraform 실행, ALB/DNS·권한 변경은 수행하지 않았다.

### 산출물과 후속 조건

- 보호된 로컬 입력과 비밀값 없는 비교 결과: `.generated/argocd-preparation/` (0600 파일, Git 제외). root 입력은 `root-application.json`, 전체 입력은 `manual-applications.json`이다. 기존 cluster Secret 원문은 포함하지 않는다.
- ADR 번호는 최신 main의 기존 0015/0016과 충돌하지 않도록 [0017](../adr/0017-adopt-existing-helm-resources-in-argocd.md)로 변경했다. 자동화 없는 준비만 승인·실행한 사실과 실제 sync 미실행을 구분했다.
- 계획 대비 변경: 최초 자동 배포 대신 승인받은 수동 등록·비교 준비 단계만 완료했다. 실제 sync/자동화 활성화는 구체적 diff·순서·비파괴 중단 절차를 리뷰하고 별도 실행 승인을 받은 뒤 진행한다.

### 정리 결과

이번 준비 단계의 임시 SSM 세션 두 개를 정상 종료했고 kubeconfig·EKS 연결 메타데이터·실행 스크립트를 정리했다. 보호된 수동 입력과 비밀값 없는 비교 결과만 `.generated/argocd-preparation/`에 남겼다. Secret 입력/원문은 파일로 저장하지 않았다.

## 읽기 전용 차이 재검토 — 2026-11-03

사용자의 “차이 검토 해주고, 미설치 애드온은 뭐 말하는거야” 요청으로 기존 승인한 management 읽기 전용 범위에서 재검토했다. sync·refresh 요청·추가 Secret 값 조회·설정 변경은 하지 않았다. 이번 문서 보완은 기존 검토 결과의 기록이며 새 운영 결정을 내리지 않아 새 ADR은 추가하지 않는다.

### 검토 결과

- 기준 main: `bea060e14133f82fa46dc6ac5a51f6f329b72e96`. 기존 서비스·baseline·LBC는 Healthy이며 리소스 생성/교체를 요구하는 선언 설정 차이는 검토 범위에서 발견하지 못했다.
- LBC `vingress.elbv2.k8s.aws`의 objectSelector/namespaceSelector는 chart가 `null`, 실제 API 저장값은 `{}`였다. 둘 다 빈 selector이며 추가 label 제한이 없다. 앞서 남긴 차이 후보는 동작상 차이가 아닌 표현/defaulting 차이로 해소했다. 다른 webhook의 matchPolicy/timeoutSeconds/rule scope 추가도 Equivalent/10초/* 기본값과 일치했다. 근거: [Kubernetes ValidatingWebhookConfiguration API](https://kubernetes.io/docs/reference/kubernetes-api/admissionregistration/validating-webhook-configuration-v1/).
- 플랫폼·baseline의 양방향 spec 필드 비교에서 live-only 필드 115개를 확인했다. Kubernetes 기본값·자동 할당값(서비스 IP, PVC volumeName 등)과 일치했으며 예상 밖 값은 없었다. image/replica/Pod template의 선언값, 네트워크 정책, PVC 요청 크기/StorageClass, Ingress 선언 등에는 값 차이가 없었다. Service/PVC의 자동 할당 identity 필드는 Git의 리소스 교체 요청으로 해석하지 않았다.
- 기존 Helm release annotation은 남아 있고 예시 플랫폼 Deployment/StatefulSet/Ingress에 Argo 추적정보는 아직 없었다. 이전 23개 desired 필드 비교 및 현재 양방향 spec 비교와 함께, 운영 설정 차이와 관리용 metadata 차이를 구분했다. 모든 OutOfSync가 기능 오류라는 뜻은 아니다.
- management LBC의 CRD 7개는 Argo 자체 비교에서도 Synced였다. webhook TLS Secret 값과 CA 원문은 기존 범위대로 비교에서 제외했다. 따라서 실제 sync/인증서 변경까지 성공을 보증하는 검토는 아니다.
- root가 생성할 하위 Application 13개의 spec을 실제 객체와 비교했다. 차이는 모두 Git의 `automated: {prune: false, selfHeal: true}`가 live에는 없다는 한 항목뿐이었다. root Sync 시 이 정책을 복원하여 미설치 리소스 배포도 시작할 수 있다.
- 현재 health 집계는 Healthy 4개(root 포함), Missing 10개, Progressing 0개다. 모든 앱에 queued/Running/Terminating operation이 없고 자동 동기화도 꺼져 있음을 재확인했다.

### 미설치 목록과 의미

| 구성 요소 | management | workload | 역할 |
| --- | --- | --- | --- |
| metrics-server | 미설치 | 미설치 | CPU/메모리 메트릭 API·HPA 기반 |
| kube-prometheus-stack | 미설치 | 미설치 | Prometheus/Grafana/Alertmanager·메트릭/대시보드/알림 |
| Loki | 미설치 | 기본 목록에 없음 | 로그 저장·조회 |
| Sealed Secrets | 미설치 | 미설치 | 암호화된 SealedSecret의 Secret 변환 |
| AWS Load Balancer Controller | 기존 설치 | 미설치 | ALB/NLB 및 관련 Kubernetes 리소스 관리 |
| Argo Rollouts | 기본 목록에 없음 | 미설치 | Canary/Blue-Green 배포 controller |
| baseline (기반 설정) | 기존 설치 | 미설치 | namespace/gp3/Quota/LimitRange/NetworkPolicy |

미설치 애드온은 9개 Application이고 workload baseline 1개를 합쳐 Missing 10개다. Application 등록만 했고 실제 리소스 sync를 하지 않은 결과다. CoreDNS/VPC CNI/kube-proxy/EBS CSI/Pod Identity 같은 이미 설치된 EKS 기본 구성 요소와 구분한다. OTel은 기본 addon 목록에 없어 이 Missing 수에 포함되지 않는다.

모니터링 values는 두 클러스터 모두 `observability/grafana-admin`이라는 기존 Secret(admin-user/admin-password)을 참조한다. 이번 범위에서 해당 Secret의 존재/내용은 확인하지 않았으므로 Prometheus/Grafana 설치 전 준비 여부를 확인해야 한다. 새 애드온 설치 시 관측 스택 PVC·리소스와 CRD/RBAC/APIService 등도 생성되므로 기존 리소스 관리 전환과 별도 단계로 실행 범위를 정해야 한다.

### 검증·계획 대비 변경·ADR

검토는 예정대로 읽기 전용으로 수행했으며 코드·클러스터 설정 변경과 실제 sync는 없다. 비교 산출물은 `.generated/argocd-preparation/difference-review.json`에 비밀값 없이 보존했다. 기존 문서 PR #9에 결과를 추가하며 새 ADR은 필요하지 않다. ADR 0017의 실제 sync 미실행 경계를 유지한다.

## 플랫폼 앱 인수 실행 결과 — 2026-11-03

플랫폼 담당(조서현)이 팀 Slack에서 알린 뒤 `charlie-management-hopcharlie-platform` 하나만 Argo CD로 넘겨받았다. root, baseline, LBC, 워크로드 Application은 건드리지 않았다.

- 사전 확인: infra main으로 렌더링한 차트와 운영 리소스를 서버 측 dry-run(`kubectl diff --server-side`)으로 비교했다. 바뀌는 것은 API, 게이트웨이, 콘솔 Deployment와 `hopcharlie-fetch-settings` ConfigMap뿐이었다. PVC, Service, Ingress, NetworkPolicy, PostgreSQL은 그대로였다. PostgreSQL이 바뀌던 원인은 #25에서 고쳤다.
- 실행: Application에 sync operation(ServerSideApply) 하나를 요청했다. 결과는 `Succeeded`, API와 게이트웨이는 `platform:efeb509`, 콘솔은 `console:8deb471`로 바뀌었고 PostgreSQL pod는 재시작되지 않았다.
- sync 뒤 PostgreSQL StatefulSet만 OutOfSync로 남았다. API 서버가 `volumeClaimTemplates`에 기본값(apiVersion, kind, volumeMode, status)을 채워서 생기는 차이라, 이 필드를 `ignoreDifferences`로 비교에서 빼고 `RespectIgnoreDifferences=true`를 켰다. root 템플릿([applications.yaml](../../helm/gitops/templates/applications.yaml))에도 같은 설정을 넣어 root sync 뒤에도 유지된다.
- 이 Application에만 자동 sync(prune false, selfHeal true)를 켰다. 결과는 `Synced`, `Healthy`다. 이후 infra main의 플랫폼, 콘솔 이미지 커밋(ADR 0025)은 사람 없이 반영된다.
- 운영 확인: `api.hopcharlie.dev/healthz` 200, API 명세에 배포와 webhook 경로가 있음, 서명 없는 webhook 요청은 401, 콘솔 `version.txt`는 frontend `8deb471`.
- 이제 플랫폼은 Helm으로 upgrade하지 않는다. Helm release 기록은 남아 있지만, 리소스는 Argo CD가 관리한다.
