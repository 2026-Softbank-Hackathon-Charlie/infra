# 0029. GCP 앱 이미지 저장소를 기존 bootstrap에서 관리

- 날짜: 2026-11-03
- 상태: 채택
- 관련 ADR: [0013](0013-add-gcp-workload.md)

## 배경과 제약

GKE 앱 배포에 사용할 Docker 이미지 저장소가 필요하다. 기존 GCP bootstrap은 Artifact Registry API와 전용 노드 서비스 계정의 `roles/artifactregistry.reader`를 이미 관리한다. 2026-11-03 읽기 전용 점검에서는 프로젝트의 Artifact Registry 저장소가 없고 실제 GKE 노드 계정과 읽기 역할·`cloud-platform` 범위가 일치함을 확인했다. 실제 이미지 pull은 아직 검증하지 않았다.

기존 runtime 배포용 GitHub WIF 계정은 GKE·네트워크 권한만 가진다. 이미지 저장소 준비를 위해 이 계정의 신뢰 조건이나 프로젝트 권한을 확대하지 않는다.

## 검토한 대안

- 기존 GCP bootstrap에 표준 Docker 저장소를 추가한다. API·노드 계정과 소유권이 같고 별도 CI 권한이 필요 없다.
- runtime root 또는 별도 root에서 저장소를 만든다. 배포 계정 권한 확대나 추가 state·CI 경로가 필요하므로 현재 범위에서 제외한다.
- AWS ECR 또는 GHCR 인증을 GKE에 별도로 배포한다. 원본 이미지 인수 후 선택할 수 있으나 지금은 원본 접근 방식과 인증 회전 계약이 정해지지 않았다.

## 결정과 이유

`terraform/bootstrap/gcp`에서 `charlie-apps` 표준 Docker 저장소 1개를 관리한다. 프로젝트와 리전은 기존 `project_id`·`region`을 사용하며 저장소 ID와 Docker prefix를 출력한다. 기존 노드의 읽기 역할을 재사용해 GKE가 같은 프로젝트의 이미지를 가져오도록 한다. 사용자 승인 후 대상 계획의 생성1·수정0·삭제0을 확인해 적용했으며, 실제 이미지 게시·pull 검증은 별도로 남는다.

노드·WIF 배포 계정에 writer 권한을 추가하지 않는다. 실제 이미지 게시 주체가 확정되면 해당 저장소에 한정한 게시 권한을 별도로 검토한다. 자동 이미지 삭제 정책과 원격 캐시는 추가하지 않는다.

## 영향과 후속 작업

- 기대 효과: 기존 GKE·노드 풀·IAM·WIF·CI를 바꾸지 않고 앱 이미지 저장소를 준비할 수 있다.
- 단점과 위험: 이미지 저장·전송 비용이 발생할 수 있고 실제 게시·pull 검증은 원본 이미지 인수 뒤에 남는다.
- 적용 경계: bootstrap은 기존처럼 구체적 승인 후 별도 로컬 적용한다. 현재 CI의 runtime plan/apply 대상과 권한은 유지한다. 이 제안과 로컬 mock 통과가 실제 생성 승인을 뜻하지 않는다.
- 검증: backend 없는 validate/mock으로 저장소의 프로젝트·리전·형식·prefix와 기존 노드 reader·배포 계정/WIF 제한을 확인한다. 실제 적용 후 저장소와 권한을 재조회하고 승인된 이미지로 pull을 확인한다.

## 관련 자료

- [GCP bootstrap](../../terraform/bootstrap/gcp/main.tf)
- [GCP 배포 절차](../gcp-deployment.md)
- [GKE의 Artifact Registry 접근](https://cloud.google.com/artifact-registry/docs/integrate-gke)
- [표준 저장소 생성](https://cloud.google.com/artifact-registry/docs/repositories/create-repos)
