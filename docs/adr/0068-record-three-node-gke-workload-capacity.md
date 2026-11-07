# 0068. GKE workload 노드 3대 기준을 소스에 기록

- 날짜: 2026-11-07
- 상태: 채택
- 관련 ADR: [0032](0032-add-gke-capacity-for-cnpg-poc.md)를 대체

## 배경과 제약

AWS→GCP 리허설에서 GKE의 임시 원본 DB와 격리 검사 Pod가 `Insufficient cpu`로 배치되지 못했다. 당시 기존 두 노드의 할당 가능 CPU는 각각 1930m, 기존 Pod 요청량은 1875m와 1885m여서 노드별 여유가 55m와 45m뿐이었다. 샌드박스의 CPU quota 5는 사용 상한이며 노드 용량을 예약하지 않는다.

당시 Plan `pln_fe8b471c7cd0` revision 3은 대상 CNPG 인스턴스 1개와 추가 plugin 없는 구성이었다. CPU를 지정하지 않아 샌드박스 기본 요청량 100m가 적용된다. 임시 원본과 격리 검사도 각각 100m이며, 단계별 동시 요청량은 약 200m다. 작업 전환의 여유를 둔 400m는 보수적 참고값이며 모든 Plan과 동시 리허설의 상한은 아니다.

사용자 승인으로 기존 `charlie-dev-gcp-workload`의 `workload` 풀을 `e2-standard-2` 2대에서 3대로 이미 증설했다. 기존 GCS state에서 Terraform의 `node_count`만 2→3으로 변경했고, GitHub 입력 `GCP_TFVARS_JSON.node_count`도 3으로 맞췄다. 증설 직후 노드 3대와 활성 Pod 69개 Ready, 새 노드 CPU 여유 1396m와 전체 여유 1496m를 확인했다. 기존 노드와 PoC DB는 유지했다.

운영 입력만 바꾸면 Git 소스의 기본값과 예제는 여전히 1대라 팀원이 현재 기준을 확인하기 어렵다. 사용자는 적용된 3대 설정을 소스와 PR에 남기도록 요청했다. 이번 소스 변경에는 추가 apply, 실제 이전, strict 전환을 포함하지 않는다.

## 검토한 대안

- 기본값과 예제를 3대로 맞추고 기존 입력 변수를 유지한다. 현재 환경의 기준을 Git에서 확인할 수 있고 기존 Terraform·workflow 구조를 보존하므로 선택한다.
- ADR에만 기록한다. 실제 기본값과 예제가 1대로 남으므로 제외한다.
- 별도 환경별 tfvars를 추가하고 workflow의 입력 우선순위도 바꾼다. 기존 GitHub 입력 구조까지 변경해야 하므로 이번 범위에서는 제외한다.

## 결정과 이유

GCP dev workload의 `node_count` 기본값과 `terraform.tfvars.example`을 3으로 맞춘다. 기존 `node_count = var.node_count`, 단일 zone, `e2-standard-2`, 30Gi 부팅 디스크와 노드 수 1~3 검증은 유지한다. 명시적 입력으로 1대를 선택하는 기능도 유지한다.

기존 workflow는 `GCP_TFVARS_JSON`을 명시적 var-file로 전달하므로 해당 값이 기본값보다 우선한다. 현재 운영 입력도 3이며, 이 PR은 입력 병합이나 우선순위를 바꾸지 않는다. 향후 노드 기준을 바꿀 때는 소스 기본값·예제와 실제 운영 입력을 함께 확인한다.

## 영향과 후속 작업

- 기대 효과: 팀원이 PR에서 현재 3대 기준, 증설 근거와 이미 확인한 적용 결과를 검토할 수 있다.
- 단점과 위험: 신규 환경도 별도 입력이 없으면 3대를 사용하므로 VM 3대와 각 부팅 디스크 비용이 발생한다. 기존 운영 환경의 추가 노드·디스크 비용은 앞선 증설에서 이미 발생했다.
- 필요한 검증: Google mock provider로 기본 3대·기존 보안 설정과 명시적 1대 입력을 검사하고, Terraform 포맷과 inventory를 확인한다. 실제 리허설 재실행은 이번 검증에 포함하지 않는다.
- 적용 범위: 현재 GKE 노드 수와 GitHub 입력은 이미 3이다. 이 PR 작업에서는 추가 apply, 리허설·이전 실행, PoC DB 조정이나 AWS strict 전환을 수행하지 않는다.

## 관련 자료

- [노드 수 입력](../../terraform/environments/gcp/dev/workload/variables.tf), [노드 풀](../../terraform/environments/gcp/dev/workload/main.tf)
- [운영 입력 예제](../../terraform/environments/gcp/dev/workload/terraform.tfvars.example), [mock 검사](../../terraform/environments/gcp/dev/workload/tests/workload.tftest.hcl)
- [GCP Terraform workflow](../../.github/workflows/terraform-gcp.yml)
