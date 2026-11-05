# 0048. AWS workload NetworkPolicy의 기본값을 standard로 유지한다

- 날짜: 2026-11-05
- 상태: 제안
- 관련 ADR: [ADR 0046](0046-use-strict-network-policy-on-aws-workload.md)의 strict 기본값 결정만 변경

## 배경과 제약

시스템 호환 정책 준비와 strict 전환 승인은 별개다. canonical 설정과 workload Terraform 기본값이 strict이면, 이후 다른 목적의 workload apply에도 승인하지 않은 CNI mode 전환이 포함될 수 있다.

2026-11-05 14:28 KST의 이전 검증 기록은 호환 정책 4개 적용, 기존 정책 7개 불변, Pod 25개 Ready, baseline revision `749c88` Synced/Healthy, CNI standard다. strict는 적용하지 않았다. 이는 이전에 확인된 기록이며 이번 PR에서 새로 측정하지 않았다.

## 검토한 대안

- strict 기본값 유지: 미래 workload apply마다 mode 변경을 제외하는 검토에 의존하므로 제외한다.
- standard 기본값과 명시적 strict 선택: 현재 mode와 의도를 맞추면서 조율된 전환 경로를 유지하므로 선택한다.

## 결정과 이유

`config/project.json`의 `workload_network_policy_enforcing_mode`와 workload root의 `network_policy_enforcing_mode` 기본값을 모두 standard로 둔다. standard/strict 검증, workload에만 전달하는 canonical prepare 경로, 명시적 strict opt-in은 유지한다. shared 모듈과 management의 standard 기본값은 그대로다.

이미 준비한 kube-system, observability, cnpg-system, cert-manager의 호환 정책 4개 및 모든 Helm/cluster/AppProject 설정은 변경하지 않는다. 앱·DB·sandbox 경계도 바꾸지 않는다.

## 영향과 후속 작업

- 이후 workload apply가 기본값 때문에 strict로 전환되지 않는다. standard의 Pod startup default allow 위험은 남는다.
- strict 전환은 canonical 값을 strict로 명시한 별도 검토와 승인, 정책 inventory/선행 sync, 전체 aws-node rollout 및 새 관리형 Pod의 허용·차단 검증을 함께 조율한다. [ADR 0046의 전환 절차](0046-use-strict-network-policy-on-aws-workload.md)를 따른다. CI는 main의 canonical 파일을 쓰며 `TF_VAR_*` 우회는 지원하지 않는다.
- 이번 작업은 코드·로컬 검증·후속 PR 준비만 포함한다. merge, Terraform apply, cluster 변경 및 새로운 live 검증은 수행하지 않는다.

## 관련 자료

- [canonical 설정](../../config/project.json), [workload 입력](../../terraform/environments/aws/dev/workload/variables.tf)
- [원래 구현·검증 계획](../plans/2026-11-05-eks-strict-network-policy.md)
