# 0046. AWS workload의 VPC CNI를 정책 준비 후 strict 모드로 전환한다

- 날짜: 2026-11-05
- 상태: 제안
- 관련 ADR: [ADR 0002](0002-require-approval-for-privileged-operations.md), [ADR 0005](0005-scope-aws-cnpg-poc.md)

## 배경과 제약

현재 shared EKS 모듈은 VPC CNI의 NetworkPolicy를 켜되 `NETWORK_POLICY_ENFORCING_MODE=standard`를 고정한다. AWS 문서에 따르면 standard에서는 새 Pod의 정책이 적용될 때까지 default allow 구간이 있다. strict는 그 구간을 default deny로 시작하므로 정책이 없는 CoreDNS와 controller도 통신이 막힐 수 있다. 이 변경은 AWS workload만 대상으로 하며 management의 standard 동작을 유지한다.

저장소의 선언상 시스템 namespace는 kube-system, observability, cnpg-system, cert-manager다. 이 목록이 실제 클러스터 전체를 포괄하는지, 기존에 더 좁은 정책이 있는지는 확인하지 않았다. Terraform addon 변경과 Argo CD 정책 sync 사이에는 자동 의존성이 없다. GitOps baseline wave 0은 Terraform apply보다 먼저 적용된다는 보장이 아니다. baseline Application은 main을 추적하고 automated sync하므로 이 변경을 main에 merge하는 것만으로 호환 allow 정책이 적용될 수 있다.

AWS는 ownerReferences가 있는 Deployment 등에서 관리하는 Pod를 NetworkPolicy 지원 대상으로 설명한다. ownerReferences 없는 standalone Pod에는 정책이 안정적으로 적용되지 않을 수 있다. 현재 ai-sandbox probe가 standalone이라는 조사 결과와 실제 hopai의 runtime `-sandbox-kubeconfig` 대상은 별도 확인 사항이다. 이 infra 변경만으로 hopai의 실행 위치나 격리가 입증되지는 않는다.

## 검토한 대안

- standard 유지: 시스템의 통신 방식은 유지하지만 Pod 시작 시 default allow 구간을 닫지 못한다.
- shared 모듈 전체를 strict로 전환: management까지 영향을 확대하고 GitOps 자체 복구 경로를 위험하게 만든다.
- workload만 strict + 시스템별 최소 허용 규칙: 장기적으로 더 좁은 권한이지만 실제 통신 inventory가 없는 상태에서 controller 의존성을 추측하게 된다. 별도 후속 작업으로 남긴다.
- workload만 strict + 고정 시스템 namespace 호환 정책: 현재 제한이 없는 시스템 namespace의 연결을 유지하면서 앱·DB·sandbox 경계를 건드리지 않는다. 아래 사전 점검에서 기존 제한 정책이 발견되면 이 대안은 적용하지 않는다.

## 결정과 이유

shared 모듈의 검증된 입력 `network_policy_enforcing_mode`는 standard를 기본값으로 두고 standard/strict만 받는다. AWS workload root는 strict가 기본이며, local/CI wrapper는 공유 입력 `config/project.json`의 `workload_network_policy_enforcing_mode`(기본 strict)를 workload의 `network_policy_enforcing_mode`로만 전달한다. canonical config와 Terraform 양쪽에서 standard/strict만 허용한다. management 등 다른 stack에는 전달하지 않는다. 이 canonical 입력을 standard로 바꾸어 최초 bootstrap과 rollback을 수행할 수 있고, 기존 `TF_VAR_*` 등 hidden-input 차단은 유지한다. addon 버전, prefix delegation, IRSA와 다른 CNI 옵션은 유지한다. 모듈 출력은 구성된 addon 값을 보여 주며 live rollout 완료를 의미하지 않는다.

cluster-baseline의 `networkPolicy.systemCompatibilityNamespaces`는 기본 빈 배열이다. workload 값에서만 kube-system, observability, cnpg-system, cert-manager를 지정한다. schema는 이 네 값과 중복 없는 배열만 허용한다. 각 namespace의 모든 Pod에 ingress/egress 호환 정책을 생성한다. 이 정책은 기존 restrictive NetworkPolicy도 합집합으로 넓히므로 현재 무제한인 namespace에만 사용한다. management 값에는 추가하지 않는다. workload AppProject destination도 cnpg-system과 cert-manager 두 개만 추가한다.

charlie-apps의 baseline, charlie-db의 cnpg-poc, 동적 앱 및 sandbox 정책은 변경하지 않는다. 시스템 allow-all은 연결 호환성을 위한 임시 범위이며, 시스템 Pod가 다른 namespace에 갈 때 상대방의 ingress 정책까지 허용하지 않는다.

## 영향과 후속 작업

- 기대 효과: 준비된 AWS workload에서 새 관리형 Pod가 정책 적용 전에 임의 통신하는 구간을 줄인다. management에는 영향이 없다.
- 단점과 위험: 네 시스템 namespace는 세밀하게 격리하지 않는다. 누락된 namespace나 정책 selector/필수 egress 오류는 strict 전환 후 장애로 이어질 수 있다. hostNetwork Pod 등 AWS NetworkPolicy 지원 제약은 그대로다.
- 이 PR은 코드와 로컬 검증만 제공한다. merge, Argo CD sync, Terraform apply, aws-node/업무 Pod 재시작, live probe는 별도 명시적 승인이 필요하며 실행하지 않았다.

### merge 전 필수 gate

이 PR은 실제 대상의 namespace/Pod/controller/기존 NetworkPolicy inventory를 완료하고, 네 시스템 namespace에 기존 restrictive 정책이 없거나 정확한 허용 규칙으로 수정됐다는 검토가 끝날 때까지 **merge하지 않는다**. restrictive 정책이 있으면 현재 allow-all 정책을 merge/sync하지 않는다. 사전 승인으로 baseline automated sync를 중지하거나 안전한 revision에 pin한 경우에만 그 보호 상태를 확인한 뒤 merge할 수 있다. 이 PR에서는 pause/pin을 실행하지 않는다. 나중에 수동 sync하거나 Terraform apply하기 직전에 확인하는 것으로는 main automated sync를 막을 수 없다.

### 기존 클러스터의 전환 순서 (실행 전 별도 승인)

1. 위 merge gate에서 실제 대상이 aws-dev-workload인지 확인하고 모든 namespace, 실행 중인 Pod/controller, NetworkPolicy와 필수 통신을 inventory한다. 승인된 pause/pin 경로를 택했다면 이 점검을 마치기 전에는 추적 revision/sync를 재개하지 않는다. 네 시스템 namespace에 restrictive 정책이 있으면 중단하고 필요한 통신만 허용하는 규칙으로 다시 설계한다. 네 곳 밖에 정책 없는 Pod가 있거나 앱·DB·sandbox 정책이 필수 통신을 다루지 못하면 해당 정책 준비 전에는 진행하지 않는다.
2. mode가 standard인 상태에서 AppProject와 workload baseline을 먼저 sync한다. 네 호환 정책 및 앱·DB·sandbox의 기존 정책이 실제 존재하고 selector가 대상 Pod와 일치하는지 확인한다. baseline sync가 실패하거나 새 시스템 Pod가 비정상이면 중단한다.
3. 별도 검토한 workload Terraform plan에서 CNI mode 변경과 예상 변경만 있는지 확인한 뒤 strict를 apply한다. 모든 노드의 aws-node DaemonSet rollout이 완료되고 실제 env가 strict인지 확인한다. 남은 standard 인스턴스가 있으면 완료로 간주하지 않는다.
4. 새 Deployment/Job 등 ownerReferences를 가진 probe를 사용한다. 서버가 실제 도달 가능한지 허용된 fresh probe로 먼저 확인하고, 동일 목적지의 금지된 fresh probe가 시작 직후부터 차단되는지 반복 확인한다. DNS·허용 egress의 positive control과 금지 egress/namespace의 negative control을 함께 기록한다. 기존 연결만 검사하거나 standalone Pod 실패만으로 통과 처리하지 않는다.
5. 실제 hopai의 sandbox kubeconfig가 가리키는 cluster/namespace, Pod 관리 방식 및 startup 통신을 별도 검증한다. CNPG 복제·연결·backup/restore 및 operator/webhook, CoreDNS·metrics·로그·인증서 controller의 정상 동작을 확인한다. 이 확인 전에는 전체 운영 검증 완료로 보고하지 않는다.

### 최초 bootstrap과 rollback

- 로컬 bootstrap/rollback: `config/project.json`의 `workload_network_policy_enforcing_mode`를 `"standard"`로 편집하고, 기존 account 입력을 사용해 `make tf-plan STACK=aws/dev/workload`로 검토한다. 별도 승인 후 `make tf-apply STACK=aws/dev/workload`를 수행한다. 다른 stack에는 mode가 전달되지 않는다. `TF_VAR_network_policy_enforcing_mode`는 지원하지 않으며 wrapper가 거절한다.
- CI bootstrap/rollback: 동일 canonical 파일의 값을 standard로 바꾼 reviewed revision을 main에 반영한 뒤 기존 workflow_dispatch에서 `stack=workload`, `action=plan`을 선택한다. 별도 승인된 apply만 실행한다. workflow는 main의 파일을 읽으므로 로컬 편집만 하거나 환경 변수/GitHub 변수에 mode를 넣어서는 CI 입력이 바뀌지 않는다. main 반영에는 위 merge 전 gate와 Git 게시 승인이 별도로 필요하다. CI에서 즉시 mode override하는 입력은 추가하지 않는다.
- 새 클러스터는 standard 상태로 bootstrap하고 Argo CD 등록과 baseline/policy sync를 마친 다음 inventory와 검증을 거쳐 canonical 값을 strict로 바꾼다. 처음부터 strict로 bootstrap하면 정책을 배포할 controller 자체가 통신하지 못할 수 있다. main의 기본 strict 값으로 최초 workload apply를 바로 실행하지 않는다.
- rollback 후 모든 aws-node가 standard가 되었는지 확인한다. 필요 시 영향받은 업무 Pod를 다시 생성하고 DNS/controller 복구를 확인한다. baseline 정책은 먼저 지우지 않는다. standard로 돌아가면 startup default allow 위험도 돌아오므로 원인과 다음 전환 조건을 기록한다.

## 관련 자료

- [Issue #60](https://github.com/2026-Softbank-Hackathon-Charlie/infra/issues/60)
- [AWS VPC CNI NetworkPolicy 문서](https://docs.aws.amazon.com/eks/latest/userguide/cni-network-policy.html)
- [EKS 모듈](../../terraform/modules/eks/main.tf), [workload root](../../terraform/environments/aws/dev/workload/main.tf)
- [baseline template](../../helm/charts/cluster-baseline/templates/baseline.yaml), [workload values](../../clusters/aws-dev-workload/values/baseline.yaml), [AppProject](../../helm/gitops/templates/projects.yaml)
- [구현·검증 범위](../plans/2026-11-05-eks-strict-network-policy.md)
