# GCP 배포 초안

상태: Draft. 이슈 [GCP 추가](https://github.com/2026-Softbank-Hackathon-Charlie/infra/issues/2)에 연결된 코드 검토용 초안이다. 실제 배포는 사용자가 수행한다.

## 포함 범위
- terraform/bootstrap/gcp: 버전 관리 GCS state 버킷, GitHub WIF, 배포/노드 service account. Bootstrap은 로컬 state를 사용하며 분실하지 않도록 보호·백업한다.
- terraform/environments/gcp/dev/workload: GCS backend, VPC/서브넷/Cloud NAT, private-node GKE Standard 한 개와 node pool. 공개 API는 명시한 인가 CIDR만 허용한다.
- helm/charts/gcp-baseline: GCP PD CSI StorageClass, namespace, quota/limit. AWS baseline과 분리한다.
- .github/workflows/terraform-gcp.yml: backend 없는 자동 검사, main에서 수동 workload plan/apply. bootstrap은 로컬 전용이다.

## 적용 전에 준비할 것
1. 프로젝트·결제·API 활성화 권한·Compute/GKE 할당량·다른 환경과 겹치지 않는 CIDR를 확인한다.
2. 로컬 관리자 인증으로 bootstrap 예제를 실제 입력으로 복사하고 plan을 검토한다. bootstrap 적용은 별도 승인한 사용자가 수행한다. 출력의 state bucket, WIF provider, 배포 및 노드 SA를 보관한다.
3. runtime 예제를 채운다. node_service_account_email과 control_plane_authorized_cidrs는 필수다. backend.hcl.example을 로컬 backend.hcl로 복사한다. 실제 tfvars/backend/state/plan/자격 증명은 커밋하지 않는다.
4. 로컬 또는 GitHub 수동 배포를 선택한다. GitHub 변수는 GCP_PROJECT_ID, GCP_STATE_BUCKET, GCP_WIF_PROVIDER, GCP_TERRAFORM_SA, GCP_TFVARS_JSON이다. JSON의 project_id는 프로젝트 변수와 일치해야 한다.
5. GitHub apply를 쓸 경우 gcp-apply 환경의 required reviewers·main 제한을 직접 설정하고 GCP_APPLY_ENABLED=true를 등록한다. 수동 action=apply와 confirm_apply 확인도 필요하다. workflow 파일만으로 승인자가 생성되지는 않는다.

## Draft에서 남은 일
- Terraform init/validate/mock tests, provider lockfiles·플랫폼 checksum 확보와 권한 충분성 검증.
- CI 결과 확인 및 mock 회귀시험 추가. terraform test의 실행만으로 테스트 존재를 보장하지 않는다.
- CNPG operator 설치, 플랫폼 Kubernetes RBAC, cross-cloud API/DB/파일 네트워크, 이미지 pull, 실제 이전 검증.
- bootstrap 로컬 state 보관 방식 확정, 비용·규모·쿼터 확인. 기본 클러스터 삭제 보호를 해제하거나 destroy하지 않는다.

초안 작성 시 실제 GCP 리소스·계정·API 상태를 조회하거나 변경하지 않았다. merge/auto-merge/배포 workflow 실행도 하지 않는다.
