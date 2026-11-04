# 0032. PostgreSQL PoC를 위한 기존 GKE 노드 풀 증설

- 날짜: 2026-11-04
- 상태: 채택
- 관련 ADR: [0031](0031-reproduce-cnpg-poc-on-gke.md)

## 배경과 제약

ADR0031 배포 후 DB 초기화 Pod가 `Insufficient cpu`로 대기했다. 확인 당시 Ready 노드 1개의 할당 가능 CPU는 1930m, 기존 Pod 요청 합계는 1556m였다. 남은 374m로는 DB 초기화 요청 500m를 배치할 수 없다. SQL·백업 검증은 아직 실행하지 않았다.

기존 `workload` 풀은 서울 `asia-northeast3-a`의 `e2-standard-2` 1대다. 자동 확장은 꺼져 있다. DB 요청을 낮추거나 다른 워크로드를 변경하지 않고 기존 AWS와 같은 DB 설정을 유지한다. 앞선 실행 승인은 추가 노드를 제외했으므로 증설에는 별도 승인이 필요하다.

## 검토한 대안

- 기존 풀의 노드 1대 추가: 현재 Terraform의 `node_count` 입력을 재사용한다. DB 설정과 노드 규격을 보존하므로 선택한다.
- DB CPU 요청 축소: 비용 증가는 없지만 원본 설정과 달라지고 정상 DB·백업 실행 여유를 보장하지 못해 제외한다.
- 다른 워크로드 축소 또는 노드 규격 변경: 관련 없는 실행 자원에 영향을 주거나 노드 교체가 필요할 수 있어 제외한다.

## 결정과 이유

승인 후 기존 풀의 `node_count`만 1에서 2로 바꾼다. 로컬 운영 `workload.tfvars.json`과 infra GitHub 변수 `GCP_TFVARS_JSON`에 같은 값을 유지하고 다른 필드는 보존한다. `gcp-plan`·`gcp-apply` 환경에는 해당 변수의 재정의가 없음을 확인했다.

기존 workload GCS state를 사용하는 Terraform에서 `google_container_node_pool.workload`만 대상으로 계획한다. 자원 생성 0·제자리 수정 1·삭제 0이며 교체가 없을 때만 적용한다. 이는 Terraform 노드 풀 자원 1개의 수정이며, 실제 VM은 1대 추가된다. 기존 클러스터와 노드는 재생성하지 않는다.

## 영향과 후속 작업

- 추가 `e2-standard-2` VM과 30Gi 부팅 디스크의 비용이 발생한다. 노드는 DB 운영용으로 유지하며 정확한 청구액은 산정하지 않았다.
- state는 Terraform 내부에서만 처리한다. 원문·비밀값·저장 plan을 출력하거나 별도 보관하지 않는다. 정상 원격 state 갱신·잠금을 사용한다.
- 노드 Ready 2대 확인 후 기존 설치를 재사용해 DB Ready, 승인된 SQL Job 최대 5개와 백업 1회를 검증한다. 새 비밀번호 발급이나 재설치는 하지 않는다.
- 노드 증설 성공이 DB·SQL·백업 검증 성공을 뜻하지 않는다. 실제 완료 후 임시 검사 자원을 정리하고 노션에 한 번만 기록한다.

## 관련 자료

- [노드 풀](../../terraform/environments/gcp/dev/workload/main.tf), [입력 변수](../../terraform/environments/gcp/dev/workload/variables.tf)
- [GCP Terraform CI/CD](../../.github/workflows/terraform-gcp.yml)
- [기존 DB 배포 PR35](https://github.com/2026-Softbank-Hackathon-Charlie/infra/pull/35)
