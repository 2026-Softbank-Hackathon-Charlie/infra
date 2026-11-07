# 0090. 기능 브랜치 push의 중복 인프라 검사를 PR 검사로 모은다

- 상태: 채택
- 날짜: 2026-11-08

## 배경

인프라 CI 실행 수를 점검했다. AWS 공통 `terraform-check.yml`과 GCP `terraform-gcp.yml`은 `pull_request`와 제한 없는 `push`를 함께 구독한다. 열린 PR의 같은 기능 브랜치 커밋에 같은 검증 workflow가 두 번 실행된다.

2026-11-08 KST 점검 당시 최근 100개 Actions 실행에는 AWS workflow의 push 31개·PR 12개, GCP workflow의 push 30개·PR 12개가 있었다. 전부 중복이라는 뜻은 아니다. 실제 중복은 동일 workflow·동일 SHA에 push와 pull_request가 함께 존재하는지 확인했다. 예를 들어 `fix/onprem-registrar-image`의 `32e3a0947ffbcf14e9159d05be7c0070228dab61`과 `0dbfdb4e92aabf404e3f9fca767ee8dced878dbf` 각각에서 두 workflow 모두 두 이벤트를 실행했다.

main의 branch-protection 조회는 `Branch not protected`, repository rulesets 조회는 빈 목록이었다. 이는 조회 시점의 상태이며 이후 규칙을 변경하거나 보호 검사를 우회하지 않는다. 검증 job 이름과 집계 조건을 보존한다.

## 결정

두 workflow의 `push` 대상만 `branches: [main]`으로 제한한다. 기능 브랜치는 PR 이벤트에서 검증하고 main merge와 수동 실행은 기존대로 검증한다. 기능 브랜치를 PR 없이 올리기만 하면 자동 검사는 실행되지 않으며, PR 생성 또는 수동 실행으로 검증할 수 있다.

AWS 7개 Terraform root와 GCP 7개 root, Helm, script·구성·inventory 검사, AWS 필수 집계 `check`, GCP baseline, 실제 CNPG·온프레미스 admission 검사와 별도 platform Terraform 검사는 유지한다. 초기화·mock test·정적 검사 단계, credential 권한, main/수동 배포 조건과 배포의 취소 금지 설정은 변경하지 않는다. workflow 전체 취소 정책도 추가하지 않는다.

이 변경은 불필요한 중복 실행 경로를 제거한다. 사용되지 않는다고 입증하지 못한 workflow·Terraform root·안전 검사를 이름이나 실행 시간만으로 삭제하지 않는다.

## 검토한 대안

- workflow 전체를 합치면 다른 변경 범위·수동 배포 경계·check 이름과 의존 관계까지 바뀌어 이번 정리에 비해 위험이 크다.
- Terraform root나 admission 검사를 생략하면 검증 범위가 줄어드므로 제외했다.
- 전체 workflow에 `cancel-in-progress`를 켜면 main·수동 인프라 변경이 중단될 수 있어 추가하지 않았다.
- docs·이미지 pin 경로 필터는 추후 검사 의존 그래프를 별도로 증명한 뒤 검토한다. 이번에는 PR 검사 누락이나 pending check를 만들지 않는다.

## 검증과 결과

기존 workflow gate 테스트에 두 workflow의 PR·main push·수동 trigger 보존 회귀를 추가한다. 변경 전 두 경우 모두 실패하고 변경 후 통과한다. 기존 배포 권한 결정표·AWS 집계·모든 root 검사와 GCP 승인 조건 검사를 유지하고 실행한다. 최종 Linux PR CI에서 실제 Terraform backend-disabled validate/mock test·Helm·script 검사를 확인한다.

PR이 열린 기능 브랜치에서 같은 SHA에 두 번 돌던 두 workflow는 각각 한 번으로 줄어든다. 전체 CI 시간이 절반이 된다고 주장하지 않으며 main push와 PR의 서로 다른 통합 검사는 계속 필요하다. 운영 Terraform·클러스터·Secret·IAM 변경은 실행하지 않는다.

## 관련 파일

- [.github/workflows/terraform-check.yml](../../.github/workflows/terraform-check.yml)
- [.github/workflows/terraform-gcp.yml](../../.github/workflows/terraform-gcp.yml)
- [scripts/tests/test-workflow.py](../../scripts/tests/test-workflow.py)
