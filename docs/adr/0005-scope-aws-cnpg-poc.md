# 0005. AWS CNPG PoC를 단일 인스턴스와 서비스별 DB로 시작

- 날짜: 2026-11-02
- 재검토: 2026-11-03 — 업데이트된 저장소/실행 기록 반영 후 개발 계획 승인
- 상태: 채택 — 2026-11-03 클라우드 기반·workload baseline 적용. PoC DB 설치·운영 검증 미실행
- 관련 ADR: [0001](0001-record-decisions-as-adrs.md), [0002](0002-require-approval-for-privileged-operations.md), [0006](0006-back-up-cnpg-poc-with-barman.md)

## 배경과 제약

해커톤 PoC에서 현실적인 합성 서비스 데이터를 사용한다. 사용자는 AWS workload 우선 도입 및 서비스별 접근 격리·백업·복원 검증 범위의 계획을 요청했고, 2026-11-03 “시작해줘”로 코드 구현을 승인했다. 이 결정의 채택은 실제 배포·운영 검증 완료를 의미하지 않는다.

초기 검토 당시 workload는 2AZ·노드 2대이며 CNPG가 없었다. 후속 확인에서는 다른 작업으로 CNPG Operator가 먼저 설치돼 있어 이를 재사용한다. 관리 플랫폼에는 별도의 PostgreSQL StatefulSet이 있으며 이 관리 DB는 실험·통합·이전 대상이 아니다. source·임시 restore와 나머지 controller·검증 Job까지 계산한 2026-11-03 점검의 workload 잔여 요청량은 CPU 25.39%, 메모리 69.06%였고 노드별 20% 여유 배치가 가능했다. 이 값은 당시 관측이며 다음 실행의 용량 보장이 아니다. 초기부터 3개 DB 인스턴스를 서로 다른 노드에 배치하려면 증설 검토가 필요하다.

## 검토한 대안

- 단일 DB와 재주입만 제공: 가장 작지만 영속성·복구 검증을 못 한다.
- 단일 CNPG 인스턴스와 영속 스토리지·백업: 운영 복잡도를 제한하면서 접근 격리와 복원 절차를 검증할 수 있다. 장애 중 전체 서비스 중단을 수용해야 한다.
- 복제본과 HA: 자동 장애전환을 검증하지만 용량·배치·복제 정책·비용 검토가 확대된다.
- 클라우드 간 이전까지 함께 구현: 목표 확장성은 높지만 네트워크·단일 writer·DDL/sequence 전환 문제를 동시에 도입한다.

## 결정과 이유

AWS workload에 CNPG Operator 하나와 `instances: 1`의 공용 PostgreSQL `Cluster` 하나를 구축하고, 서비스 A/B에 Database 및 owner/migration·runtime Role을 분리해 제공한다. 앱은 내부 `-rw` endpoint와 TLS를 사용한다. DB는 전용 namespace·암호화 gp3 Retain StorageClass를 사용하며 공통 EKS/default StorageClass를 바꾸지 않는다.

현재 단계의 권고는 무중단 HA 대신 복구 가능한 PoC다. Pod 재시작 시 같은 PVC 재사용, S3 기반 수동 복구를 시험한다. 초기 RPO 15분·복구 실행 기준 RTO 60분은 합산 1GiB 이하 가상 데이터에서 검증할 목표이며 보장된 SLA가 아니다. 노드/AZ/리전 장애의 복구 시간 상한은 제공하지 않는다.

Terraform은 클라우드 기반, Argo CD는 workload addon·CNPG CR을 관리한다. DB 내부 schema/grant는 명시적 migration 작업이 관리한다. DB Provisioner·Migration Controller·PgBouncer·논리복제·GKE·HA는 제외한다. 이후 서로 다른 SLO·부하·보안 요구가 생기면 별도 CNPG Cluster 또는 HA 계획을 검토한다.

11월3일 수정안에서는 기존 root/앱의 수동 운영 기록을 유지하며 CNPG 전용 수동 AppProject/Application으로 등록·단계별 sync한다. root 전체 sync·기존 앱 자동화·management 플랫폼 DB/SSM 소유권 변경은 제외한다. workload baseline과 Git에만 준비된 Sealed Secrets의 실제 준비를 선행 조건으로 명시한다. 기존 관리 플랫폼의 SSM 비밀값을 새 DB에 재사용하지 않는다.

후속 재점검에서 기존 CNPG Operator와 전용 AppProject, 관리 플랫폼의 자동 Sync를 확인했다. 수정 계획을 독립 리뷰한 뒤 사용자는 기존 Operator·Project·관리 플랫폼 자동화를 보존하는 계획과 baseline 단계의 실행을 승인했다. Operator의 앱 namespace 감시 범위를 되돌리거나 재설치하지 않는다. 명시적으로 승인한 baseline 8개 자원 생성과 기존 `gp3`·`cnpg-system`의 설정 불변 Argo 관리 연결을 완료했으며, DB·자격 증명·추가 addon 설치는 아직 실행하지 않았다. 상세 실행 범위는 [계획의 후속 기록](../plans/cnpg-aws-poc.md#13-terraform-및-workload-baseline-적용--2026-11-03)을 따른다.

관리 Prometheus 설치 기록은 workload exporter의 자동 수집·경보 보장이 아니다. 첫 검증은 상태/SQL/metrics 수동 확인과 RPO/RTO 측정으로 한정한다. workload Prometheus·cross-cluster scrape·외부 통지는 별도 설계·승인 범위로 둔다. 사용자는 이 수정안을 검토한 뒤 “시작해줘”로 코드 구현을 승인했다. GCP 기반 코드는 추가됐으나 이번 AWS CNPG의 GKE 등록·이전 제외는 유지한다.

## 영향과 후속 작업

- 기대 효과: 현재 노드 구성을 유지하며 서비스별 DB와 복구 절차의 실현 가능성을 검증한다.
- 단점과 위험: Primary·자원·WAL·장애·업그레이드·물리 복구 경계가 공유된다. 서비스별 DB는 강한 자원·가용성 격리가 아니다.
- 필요한 후속 작업 및 검증: [계획](../plans/cnpg-aws-poc.md)의 로컬 검사, namespace/RBAC/Secret 경계, 접근 거부·Pod 재생성·복원 시험. source와 임시 restore의 동시 수용량 확인 후 부족하면 재계획한다.
- 코드 개발 승인은 실제 조회·클라우드·Secret 변경 실행 승인과 구분한다. 별도 [수동 실행 runbook](../runbooks/cnpg-poc.md)으로 데이터·권한·복구 시험 범위를 검토한 뒤 실행한다.

## 관련 자료

- [AWS CNPG PoC 계획](../plans/cnpg-aws-poc.md)
- [EKS 기본 노드 설정](../../terraform/modules/eks/main.tf)
- [CNPG HA 동작](https://cloudnative-pg.io/docs/1.29/operator_capability_levels/)
- [CNPG DatabaseRole 도입](https://cloudnative-pg.io/releases/cloudnative-pg-1-30.0-released/)
- [관리 플랫폼 운영 ADR](0016-run-hopcharlie-platform-on-management-cluster.md), [수동 Argo 준비 ADR](https://github.com/2026-Softbank-Hackathon-Charlie/infra/blob/c6e1034/docs/adr/0017-adopt-existing-helm-resources-in-argocd.md), [관리 관측 ADR](0018-stage-minimal-management-monitoring.md)
