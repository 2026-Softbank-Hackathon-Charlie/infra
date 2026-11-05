# AWS workload strict NetworkPolicy 구현·검증 범위

- 날짜: 2026-11-05
- 상태: PR 제안; live 적용·merge 제외
- 결정: [ADR 0046](../adr/0046-use-strict-network-policy-on-aws-workload.md)

## 목표와 범위

AWS workload의 Pod startup default allow 구간을 줄일 수 있는 설정과 사전 시스템 정책을 준비한다. shared module/management는 standard를 유지하고 workload는 strict 기본값 및 standard override를 제공한다. 네 고정 시스템 namespace만 호환 정책을 받고 앱·DB·sandbox 정책은 그대로 유지한다.

## merge 전 gate

baseline Application은 main을 automated sync하므로 merge 자체가 새 allow 정책을 적용할 수 있다. 실제 namespace와 기존 restrictive NetworkPolicy inventory/검토가 끝날 때까지 이 PR을 merge하지 않는다. restrictive 정책이 있으면 필요한 통신만 허용하도록 다시 설계한다. 별도 승인된 sync pause/revision pin을 먼저 확인한 경우에만 보호된 merge가 가능하며, 이 작업에서는 실행하지 않는다.

## 구현 순서와 검증 계약

1. existing Terraform mock test에서 shared default standard, strict 전달과 다른 CNI 옵션 유지, 잘못된 mode 거절을 검증한다. root mock test는 management standard와 workload strict/default standard override를 실제 addon 구성에서 확인한다.
2. existing Helm checker에서 workload 호환 정책이 정확히 네 namespace에 생성되고 management/default에서는 생성되지 않는지 확인한다. unsafe namespace/중복 입력을 거절하고 기존 app/DB baseline 렌더와 AppProject 권한을 확인한다.
3. backend 없는 disposable tree에서 고정 Terraform/provider의 validate와 affected mock test를 실행한다. Helm lint/render와 config/inventory, diff whitespace를 확인한다. 원본 lockfile·의존성·CI gate는 바꾸지 않는다.
4. canonical `config/project.json`의 `workload_network_policy_enforcing_mode`를 검증하고 workload에만 전달한다. local/CI `prepare`에서 기본 strict, standard bootstrap/rollback, invalid mode 거절 및 management 비전달을 검사한다. 전체 diff를 한 번 독립 리뷰하고 구체적 결함을 수정한다. 커밋·push·PR 게시에는 저장소의 명시적 승인 규칙을 적용한다.

## 별도 승인 이후 남은 작업

실제 namespace/기존 제한 정책 inventory → standard 상태의 baseline 선행 sync → workload strict apply → 전체 aws-node rollout 확인 → 새 관리형 Pod의 허용/차단 통신 controls → 실제 hopai와 CNPG/controller 확인이다. fresh bootstrap과 rollback은 canonical `workload_network_policy_enforcing_mode`를 standard로 바꾸고 기존 workload plan/apply 경로를 쓴다. CI는 main의 reviewed canonical 파일을 사용하므로 로컬 편집이나 `TF_VAR_*`로 override할 수 없다. 최초 workload apply 전에 standard revision을 준비해야 한다. 정확한 중단 조건과 순서는 ADR 0046을 따른다. 로컬 렌더/mock 통과는 실제 배포 안전성이나 트래픽 격리 증거가 아니다.
