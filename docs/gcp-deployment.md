# GCP 인프라 구축과 배포

범위: 기존 AWS 관리 영역에서 사용할 GCP workload GKE 한 개의 기반이다. 실제 배포는 사용자가 수행한다. 코드를 merge해도 GCP 자원이 자동 생성되지 않는다. 프로젝트·결제·quota·IAM·연결 경로는 실제 환경에서 확인해야 한다.

## 구조와 입력

| 위치 | 역할 |
|---|---|
| terraform/bootstrap/gcp/ | API 활성화, 버전 관리·공개 차단 GCS state 버킷, 배포/노드 SA, GitHub WIF |
| terraform/environments/gcp/dev/workload/ | 별도 GCS state, VPC/서브넷/NAT, private-node zonal GKE·node pool |
| helm/charts/gcp-baseline/ | 전용 PD CSI StorageClass, 앱 namespace, quota·기본 리소스 한도 |
| .github/workflows/terraform-gcp.yml | backend 없는 자동 검사와 main 수동 runtime plan/apply |

Bootstrap은 로컬 state를 사용한다. state를 분실하지 않도록 안전하게 보관·백업하고 관리자 한 명이 소유한다. runtime state와 합치거나 기존 state를 지워서 재생성하지 않는다. state·plan·실제 tfvars/backend/자격 증명은 Git에 넣지 않는다.

## 1. 최초 Bootstrap — 관리자 로컬 실행

기존 결제 연결 프로젝트, API 활성화·IAM·버킷 생성 권한과 GKE/Compute 할당량이 필요하다. 버전은 저장소의 .terraform-version을 사용한다. 아래 login·plan·apply는 사용자가 권한과 비용을 검토한 뒤 실행한다.

```bash
gcloud auth application-default login
cp terraform/bootstrap/gcp/terraform.tfvars.example terraform/bootstrap/gcp/terraform.tfvars
```

실제 project_id, 유일한 state_bucket_name, region을 입력한다. GitHub 식별자는 추측하거나 예제 숫자를 재사용하지 않고 조회한다.

```bash
gh api repos/2026-Softbank-Hackathon-Charlie/infra --jq '{repository_id: .id, owner_id: .owner.id}'
terraform -chdir=terraform/bootstrap/gcp init -lockfile=readonly
terraform -chdir=terraform/bootstrap/gcp plan -var-file=terraform.tfvars -out=bootstrap.tfplan
# 생성/변경·권한·비용을 검토한 사용자가 실행
terraform -chdir=terraform/bootstrap/gcp apply bootstrap.tfplan
terraform -chdir=terraform/bootstrap/gcp output
```

출력은 state_bucket_name, workload_identity_provider, deploy_service_account_email, node_service_account_email이다. 배포 SA에는 runtime 권한과 해당 버킷 object 권한·해당 노드 SA actAs를 부여한다. Bootstrap 관리 권한 전체를 위임하지 않는다. WIF는 저장소/소유자의 숫자 ID와 지정 main ref를 제한한다. Google 프로젝트의 Service Usage API 자체가 비활성이라면 관리자가 먼저 활성화해야 한다.

## 2. Runtime 입력 준비

```bash
cp terraform/environments/gcp/dev/workload/terraform.tfvars.example terraform/environments/gcp/dev/workload/terraform.tfvars
cp terraform/environments/gcp/dev/workload/backend.hcl.example terraform/environments/gcp/dev/workload/backend.hcl
```

프로젝트와 node_service_account_email을 bootstrap 결과로 채운다. control_plane_authorized_cidrs에는 운영자와 AWS 관리 영역의 실제 공인 egress CIDR를 입력한다. 예제 문서용 IP는 교체해야 하며 전체 인터넷 /0은 거부된다. CIDR가 AWS·VPN·다른 클러스터와 겹치지 않는지 확인한다. 기본값은 서울 단일 zone, 노드1개이며 운영 HA 구성이 아니다. GKE·노드·디스크·NAT 비용이 발생한다. backend에는 bootstrap GCS 버킷과 gcp/dev/workload prefix를 사용한다.

로컬 배포를 선택한 경우 사용자의 ADC 권한으로 실행한다. 아래 명령은 클라우드 조회·변경을 수행하므로 별도 검토 후 실행한다.

```bash
terraform -chdir=terraform/environments/gcp/dev/workload init -lockfile=readonly -backend-config=backend.hcl
terraform -chdir=terraform/environments/gcp/dev/workload plan -var-file=terraform.tfvars -out=workload.tfplan
terraform -chdir=terraform/environments/gcp/dev/workload apply workload.tfplan
```

기본 삭제 보호를 임의 해제하지 않는다. 생성 후 node Ready와 시스템 Pod 상태를 확인한다. 연결 경로와 허용 CIDR가 준비되지 않으면 API 접속이 되지 않을 수 있다.

## 3. GitHub Actions로 Runtime 배포하는 경우

로컬 runtime 배포 대신 선택하는 경로다. 같은 state에 두 경로를 동시에 실행하지 않는다. GitHub repository/environment 변수:

| 변수 | 값 |
|---|---|
| GCP_PROJECT_ID | 프로젝트 ID |
| GCP_STATE_BUCKET | bootstrap 버킷 |
| GCP_WIF_PROVIDER | bootstrap workload_identity_provider |
| GCP_TERRAFORM_SA | bootstrap deploy_service_account_email |
| GCP_TFVARS_JSON | runtime 입력을 담은 JSON 객체. project_id 일치, node_service_account_email·control_plane_authorized_cidrs 필수 |

JSON에는 토큰이나 비밀값을 넣지 않는다. apply를 사용하려면 gcp-apply 환경에 required reviewers·main 제한을 별도 구성하고 GCP_APPLY_ENABLED=true를 설정한다. workflow가 승인자를 생성하거나 그 존재를 검증하지는 않는다. plan은 gcp-plan 환경을 사용한다.

Actions에서 Charlie GCP infrastructure를 열고 main의 수동 실행으로 action=plan을 선택한다. 계획을 확인한 뒤 action=apply와 confirm_apply=true로 새 실행한다. apply 실행에서는 계획을 다시 생성하므로 앞선 plan 실행과 동일한 계획임을 보장하지 않는다. 고정된 실행 commit·변수와 apply 실행의 계획을 승인자가 검토한다. 이후 변경 규모가 커지면 실행 간 동일 plan 승인 계약을 추가한다.

push/PR은 검증만 수행하며 credentials를 발급하지 않는다. 두 TF root는 backend 없는 validate/mock test를 수행한다. Bootstrap 자체는 Actions 배포 대상이 아니다.

## 4. 클러스터 기본 설정과 연결

GKE 자격 증명을 가져온 뒤 의도한 project/cluster/context인지 직접 확인한다. 접근 가능한 관리 환경에서 사용자가 아래 설치를 검토·실행한다.

```bash
helm lint --strict helm/charts/gcp-baseline
helm template charlie-gcp-baseline helm/charts/gcp-baseline
helm upgrade --install charlie-gcp-baseline helm/charts/gcp-baseline --namespace charlie-apps --create-namespace
```

전용 StorageClass는 charlie-gcp-balanced이며 AWS EBS·기존 GKE 기본 클래스를 덮어쓰지 않는다. baseline 자체에는 플랫폼 RBAC, NetworkPolicy, CNPG operator, 이미지 registry 접근이나 cross-cloud 네트워크가 포함되지 않는다. 실제 DB/앱의 자원 요청에 맞춰 quota/limits를 조정한다.

완료 확인은 GKE 생성뿐 아니라 API 접근, Pod 실행·이미지 pull, PVC 생성, CNPG 설치, 관리 플랫폼 인증/RBAC, DB·파일 전송 경로 및 실제 이전 시험까지 필요하다. 이 PR은 해당 환경별 통합이 완료됐다는 증거가 아니다.

## 검증 범위

backend 없는 Terraform 검사·mock tests, Helm lint/render·workflow 회귀, 인벤토리를 수행한다. 실제 GCP 권한 충분성·리소스 생성·망 연결·비용은 live 실행 전 확인 대상이다. provider lockfile의 버전·checksum 변경은 의도적으로 갱신하고 검토한다.
