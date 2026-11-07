# 0065. 이미지 스캐너에 Artifact Registry 읽기 권한을 GitHub OIDC로 준다

- 날짜: 2026-11-06
- 상태: 채택
- 2026-11-07 갱신([ADR 0075](0075-remove-moimroom-static-catalog-and-image-publishing.md)): AR `charlie-apps`를 지워 스캐너 추가 저장소 기본값을 빈 목록으로 바꿨다. 스캐너는 `hopcharlie-apps`만 읽는다.
- 관련 ADR: [0042](0042-copy-app-images-to-artifact-registry-for-gke.md)

## 배경과 제약

platform의 `scan-images.yml`(SEC-04)이 매시간 실행 중인 이미지를 Grype로 스캔한다. ECR은 `hopcharlie-image-scan` 역할로 읽는다. GKE의 moimroom 이미지(`charlie-apps`)는 자격 증명이 없어 "스캔 실패"로 남았다. 첫 실행 결과는 20개 중 19개 성공이었다. 실패는 0건이 아니라 실패로 기록된다.

bootstrap/gcp의 `charlie-github` 풀은 관리자 한 명의 로컬 state에 있어 이 작업에서 바꾸지 않는다.

## 검토한 대안

- bootstrap의 `charlie-github` 풀에 provider 추가: state 소유자만 적용할 수 있어 제외했다.
- 러너 VM 서비스 계정에 reader 부여: 러너의 모든 job이 권한을 갖게 되어 제외했다.
- 서비스 계정 키를 secret으로 저장: 장기 키를 만들지 않는 원칙에 맞지 않아 제외했다.

## 결정과 이유

`terraform/integrations/gcp/app-images`에 전용 풀 `charlie-image-scan`과 provider `platform-main`, 서비스 계정 `charlie-image-scanner`를 둔다.

- 신뢰 조건: platform 저장소·조직의 숫자 ID, `refs/heads/main`, 최상위 워크플로가 `scan-images.yml`일 때만 허용한다.
- 권한: `hopcharlie-apps`와 `charlie-apps` 저장소 단위 `roles/artifactregistry.reader`만 준다. push·삭제·프로젝트 권한은 없다.

## 영향과 후속 작업

- 기대 효과: GKE 앱 이미지도 스캔 결과가 생긴다.
- 단점과 위험: 다른 저장소를 읽게 하려면 `image_scan_extra_repositories`를 바꿔 적용해야 한다.
- 후속: 적용 후 platform 저장소 변수 `GCP_IMAGE_SCAN_PROVIDER`, `GCP_IMAGE_SCAN_SERVICE_ACCOUNT`를 출력값으로 설정한다.

## 관련 자료

- [image-scan.tf](../../terraform/integrations/gcp/app-images/image-scan.tf)
- [접근 테스트](../../terraform/integrations/gcp/app-images/tests/access.tftest.hcl)
