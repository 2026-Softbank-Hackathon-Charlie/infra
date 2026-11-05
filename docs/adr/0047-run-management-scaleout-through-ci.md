# 0047. 관리 노드 증설을 기존 GitHub CI에서 제한적으로 실행

- 날짜: 2026-11-05
- 상태: 채택 (구현·PR 준비 승인, merge·실제 배포 미실행)
- 관련 ADR: [0008 수동 stack 선택](0008-select-terraform-stack-and-action.md), [0009 CI 역할](0009-grant-ci-terraform-administrator-access.md)

## 배경과 제약

사용자는 관리 EKS 노드 증설 및 설명된 state refresh 차이 11건을 승인하고, 배포를 CI에서 실행하도록 요청했다. 기존 CI는 main의 수동 실행과 OIDC를 지원하지만 특정 노드 수량만 변경하도록 제한하는 검사가 없다. 별도의 ClickStack 구현까지 함께 merge하지 않아야 한다.

## 검토한 대안

- 로컬 apply: 사용자의 CI 실행 선호에 따라 사용하지 않는다.
- 기존 management apply 즉시 실행: plan의 변경 범위를 자동 제한하지 못해 제외한다.
- 기존 수동 CI에 제한된 scaleout 선택 추가: 기존 인증·검증·동시 실행 방지를 재사용하고 범위 검사를 추가할 수 있어 제안한다.

## 결정과 이유

승인한 merge SHA와 github.sha 일치를 OIDC 인증 전에 검사하고, 기존 workflow의 기본 동작과 main 인증 제한을 유지하면서 `management-scaleout` 선택을 추가한다. 대상 노드 그룹의 min/desired/max 1→2 한 건과 승인된 refresh 차이만 허용하는 guard를 통과한 saved plan만 적용한다. 용량 관련 변경만 별도 브랜치/PR로 제출한다. 사용자는 CI 변경 구현과 검증·push·PR 생성까지 승인했다. merge는 사용자가 진행하며 Agent는 이번 요청에서 merge·수동 배포 workflow 실행을 하지 않는다.

## 영향과 후속 작업

- CI에 검증과 실행 이력이 남으며 자격 증명을 발급하지 않고 기존 OIDC를 재사용한다.
- 기존 CI 역할은 관리자 권한이며 이번 guard가 IAM상의 보안 경계를 대신한다고 주장하지 않는다.
- 정확한 refresh fingerprint가 달라지면 실행을 중단한다. 일반 management 배포의 정책을 변경하지 않으며 이번 선택은 지정 증설에만 사용한다.
- 독립 계획 리뷰를 통과했다. 구현과 검증 후 PR을 전달한다. PR merge, main 수동 plan/apply 및 노드·기존 서비스 상태 확인은 후속 작업이며 완료로 표현하지 않는다.

## 관련 자료

- [CI 전환 계획](../plans/management-capacity-ci.md)
- [기존 workflow](../../.github/workflows/terraform-check.yml)
- [기존 CI 진입점](../../scripts/tf-ci.sh)
