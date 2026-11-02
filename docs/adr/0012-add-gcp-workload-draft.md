# 0012. GCP 이전 대상 인프라 추가

- 날짜: 2026-11-02
- 상태: 제안
- 관련 ADR: 0011

## 배경과 제약
AWS 전용 인프라에 GCP 이전 대상이 필요하다. 사용자는 이슈와 초안 PR을 요청했으며 직접 merge·배포한다. 프로젝트·quota·연결 상태는 미확인이다.

## 검토한 대안
- AWS 관리 영역과 별도 GCP workload 한 개: 데모 범위와 공통 제어를 단순화한다.
- GCP에 전체 관리 영역 복제: 운영·비용·상태 소유가 추가되므로 현재 범위에서 제외한다.

## 결정과 이유
별도 bootstrap/runtime root, GCS backend, 저장소/소유자 ID·branch 제한 GitHub WIF, GKE Standard와 GCP 전용 baseline 초안을 제안한다. AWS 구성은 보존한다. 실제 API·IAM 적용은 이 PR 범위가 아니다.

## 영향과 후속 작업
- 기대 효과: AWS와 분리된 GCP 실행 환경의 검토 가능한 기반.
- 단점과 위험: GKE/NAT/노드 비용, 로컬 bootstrap state 관리, 별도 CI 승인 설정.
- 필요한 후속 작업 및 검증: Terraform init/validate/mock·lockfile, CI, 실제 IAM/클러스터·스토리지·cross-cloud 연결 검증. Draft에는 미완료를 표시한다.

## 관련 자료
- [배포 초안](../gcp-deployment.md)
- [GCP 추가 이슈](https://github.com/2026-Softbank-Hackathon-Charlie/infra/issues/2)
- [GKE Terraform](https://docs.cloud.google.com/kubernetes-engine/docs/quickstarts/create-cluster-using-terraform)
- [WIF](https://docs.cloud.google.com/iam/docs/workload-identity-federation-with-deployment-pipelines)
