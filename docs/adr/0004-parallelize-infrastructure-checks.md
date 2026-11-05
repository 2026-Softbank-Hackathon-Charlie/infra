# 0004. 인프라 검증을 병렬 job으로 분리하고 provider 캐시 사용

- 날짜: 2026-11-02
- 상태: 채택
- 관련 ADR: [0003](0003-share-project-inputs-between-local-and-ci.md)

## 배경과 제약

기존 CI는 Terraform 검증·mock test·Helm·스크립트 검사를 순차 실행하며 Terraform init을 총 11회 수행했다.
사용자는 CI 대기 시간 개선을 요청하고 검토한 개발 계획을 승인했다. 실제 GitHub 소요 시간과 runner 사용 분 변화는 아직 측정하지 않았다.
검사 병렬화 도입 당시에는 검사 범위와 수동 main 배포 조건, 적용 순서, state 잠금과 foundation 삭제 guard를 유지했다. 현재 main 머지/push의 자동 apply 조건은 [ADR 0008](0008-select-terraform-stack-and-action.md)을 따른다.

## 검토한 대안

- make 병렬 실행만 추가: 같은 프로세스의 캐시·임시 디렉터리 경쟁을 관리하기 어렵다.
- 변경 경로에 따라 검사 생략: 빠르지만 연계 변경의 회귀를 놓칠 수 있어 제외한다.
- root matrix와 독립 Helm·스크립트 job: 격리된 runner에서 병렬화하고 모든 결과를 집계할 수 있어 선택한다.

## 결정과 이유

[워크플로](../../.github/workflows/terraform-check.yml)의 Terraform 6개 root를 matrix로 분리하고 동시 실행을 최대 3개로 제한한다.
Helm과 경량 검사는 독립 job으로 실행한다. 기존 `check` 이름은 집계 job으로 유지하고 실패·취소·skip을 성공으로 간주하지 않는다.
[통합 runner](../../scripts/terraform-verify.py)는 정제한 임시 복사본에서 root당 init 한 번 후 validate와 mock test를 실행한다.
bootstrap은 validate만 하며 나머지 root의 기존 mock test 28개를 유지한다. 로컬 기존 검사도 같은 runner를 사용한다.
provider만 OS·아키텍처·CLI 버전·root·lock hash별로 캐시하고 checksum 검증을 유지한다.
각 job은 별도 쓰기 디렉터리를 사용한다. state·plan·tfvars·전체 .terraform는 캐시하지 않는다.
실제 apply는 병렬화하거나 취소하지 않고 기존 순서를 유지한다.

## 영향과 후속 작업

- 기대 효과: 독립 검사의 대기 시간 감소, init 11회에서 6회로 감소, warm cache의 다운로드 감소.
- 단점과 위험: runner 시작·대기 비용과 전체 사용 분이 증가할 수 있으며 cache miss와 네트워크 지연에 따라 효과가 달라진다. 캐시는 metadata 조회를 없애지 않는다.
- 필요한 후속 작업 및 검증: root 목록·mock 범위·성공 집계·배포 gate·정제 복사·초기화 횟수를 회귀 검증한다. 이후 승인된 GitHub 실행에서 cold/warm 소요 시간을 비교한다. 특정 속도 배율은 보장하지 않는다.

## 관련 자료

- [Makefile](../../Makefile)
- [Terraform provider 캐시 공식 문서](https://developer.hashicorp.com/terraform/cli/config/config-file#provider-plugin-cache)
