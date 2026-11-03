# AWS CNPG PoC 도입 계획

- 날짜: 2026-11-02
- 재검토: 2026-11-03 — 저장소·운영 기록 업데이트 반영. 후속 승인한 읽기 전용 배포 확인 결과는 11절에 기록
- 상태: 개발 계획 승인 — 사용자 “시작해줘”에 따라 코드·로컬 검증 진행. 실제 배포·Secret 생성은 미승인·미실행
- 작업 분류: 영향이 크거나 불확실한 작업. 데이터 영속성, IAM, 네트워크, GitOps 소유권 및 여러 구성 요소의 연계 변경을 포함한다.
- 사용자 확정 조건: 해커톤 PoC, 실제 개인정보·운영 데이터가 아닌 현실적인 합성 서비스 데이터 사용, AWS 워크로드 클러스터 우선.
- 브랜치 조건: 개발은 `codex` prefix 없는 `feat/aws-cnpg-poc`에서 진행. 기존 작업 파일은 원래 checkout에 보존하고 별도 worktree에서 로컬 `origin/main@82b6617`로 fast-forward해 구현한다. 원격 조회·merge·배포 실행과 구분한다.
- 관련 결정 초안: [단일 인스턴스 범위](../adr/0005-scope-aws-cnpg-poc.md), [백업·복구](../adr/0006-back-up-cnpg-poc-with-barman.md)

## 0. 재검토 기준과 현재 전제

계획 재검토는 로컬 `argocd-workload-target` 브랜치의 `b9a00fa`와 작업트리의 ADR·실행 기록을 기준으로 수행했고 그 단계에서는 원격 main fetch, AWS/Kubernetes/Argo API, Secret/state/plan 조회를 하지 않았다. 아래 표는 당시 근거 문서의 과거 실행 기록이다. 이후 별도 승인받은 실제 배포 점검 결과는 11절과 구분한다. 모니터링 기록은 로컬 HEAD보다 이후의 main revision `82b6617`을 기준으로 하므로 구현 전에 승인된 최신 기준 commit과 로컬 차이를 정합화해야 한다. 다른 작업의 미커밋 문서를 배포 가능한 main으로 간주하지 않는다.

| 업데이트 | 확인 근거·계획에 미치는 영향 |
| --- | --- |
| 관리 플랫폼 및 자체 PostgreSQL | [플랫폼 ADR](../adr/0016-run-hopcharlie-platform-on-management-cluster.md), [기존 DB chart](../../helm/charts/hopcharlie-platform/templates/postgres.yaml), [실행 기록](metrics-and-management-monitoring.md). 플랫폼 DB/SSM/PVC를 CNPG 실험과 완전히 분리 |
| CI 관리자 정책 | [ADR0009](../adr/0009-grant-ci-terraform-administrator-access.md), [정책 attachment](../../terraform/account/aws/ci-administrator.tf). account IAM 확장·apply를 필수 단계에서 제거. DB runtime IAM은 별도 최소 권한 유지 |
| Argo 등록·Git 접근 | [workload 등록 기록](argocd-workload-target.md), [수동 준비 ADR0017](https://github.com/2026-Softbank-Hackathon-Charlie/infra/blob/c6e1034/docs/adr/0017-adopt-existing-helm-resources-in-argocd.md). 기본 등록을 반복하지 않고 지정 대상·revision으로 신규 CNPG 앱만 수동 준비 |
| Git 자동화와 실행 상태 차이 | 기본 Git 템플릿은 automated/selfHeal, 실행 기록은 root·하위 앱 모두 수동. root 전체 sync로 자동화가 복원되지 않도록 보호 |
| 관측 | [ADR0018](../adr/0018-stage-minimal-management-monitoring.md)·[실행 기록](metrics-and-management-monitoring.md)에 양쪽 metrics-server와 관리 Prometheus 설치 완료. workload Prometheus는 설치 완료 기록 없음. 관리 Prometheus가 workload ServiceMonitor/ClusterIP를 자동 수집한다고 가정하지 않음 |
| workload baseline·Sealed Secrets | Git 정의는 있으나 이전 실행 기록상 미설치. 필요한 baseline과 Sealed Secrets만 지정 revision으로 선행 설치·준비 확인 |
| GCP 기반 | [ADR0013](../adr/0013-add-gcp-workload.md)에 GKE/WIF/Terraform 추가. 실제 적용·연결은 재확인 전이며 AWS PoC의 GKE 등록·논리복제 제외는 유지 |

이번 수정안은 최초 검증을 수동 실행·측정에 한정하고, workload Prometheus/새 cross-cluster scrape/외부 알림 설치를 후속 범위로 분리한다. 자동 경보가 필요하면 기존 관리 관측 도구를 포함한 수집·인증·보관 설계를 별도 리뷰·승인해야 한다. 이 변경을 숨긴 채 기존 자동 경보 목표를 달성했다고 보고하지 않는다.

## 1. 목표와 완료 기준

AWS workload EKS에 공용 CNPG `Cluster` 하나를 만들고 가상 서비스 A/B에 독립 Database와 Role을 제공한다. 접근 격리, 영속성, S3 백업과 수동 복구를 검증한다. 이번 결과는 운영 SLA나 HA 보증이 아닌 명시한 시험 조건에서의 관측 결과다.

- A/B 각각의 CRUD·트랜잭션·sequence 동작이 정상이다.
- A 자격 증명으로 B DB 접속·조회·변경이 거부되고 반대 방향도 같다. 미허용 Pod의 네트워크 접속도 거부된다.
- DB Pod 재생성 후 동일 PVC에서 커밋한 데이터가 보존되고, 재연결이 성공한다.
- 초기 base backup, 일일 ScheduledBackup, 연속 WAL 보관이 성공한다. 백업 완료 상태뿐 아니라 별도 Cluster로 실제 복원한 데이터를 확인한다.
- 지정 시점 PITR, 전체 데이터 복원, A만 추출·복구하는 절차를 검증한다. A 복구 중 B 데이터가 변경되지 않음을 확인한다.
- 권한·namespace·버전·이미지·삭제 방어를 로컬 검사로 검증하고, 실제 시험 결과는 실행 승인 후 기록한다.

## 2. 장애 대응 선택지와 권고

| 선택지 | 구성·대응 | 보장 한계·비용 | 이번 판단 |
| --- | --- | --- | --- |
| 재생성형 | 단일 DB, 합성 데이터 재주입 | DB/PVC 손실 시 데이터 연속성 포기 | 단순 데모에는 가능하지만 복구 검증 목표에 부족 |
| 복구형 | 단일 Primary, EBS PVC, S3 base backup·WAL, 수동 복구 | 장애 중 공용 DB 전체 중단, 노드/AZ 장애 자동 전환 없음 | 권고 |
| 비동기 HA | Primary+standby를 다른 노드에 배치, 자동 failover와 백업 | 최근 트랜잭션 손실 가능, 복제본·노드 비용, AZ 배치별 한계 | 후속 단계 |
| 동기 HA | 여러 노드/AZ에 복제본과 동기 commit 정책 | 가용성과 commit 지연의 절충, 유효 standby·quorum·장애 범위에 따라 보장 달라짐 | 이번 범위 제외 |
| 리전·클라우드 DR | 다른 리전·클라우드 복구 사본과 복구·전환 절차 | 별도 네트워크·보관·전환 비용, 서비스별 단일 writer 설계 필요 | 이번 범위 제외 |

인스턴스 개수만으로 무손실·무중단을 보장하지 않는다. HA도 사용자 오삭제·논리 오류를 그대로 복제하므로 백업을 대체하지 않는다. 향후 비동기/동기 HA는 별도의 RPO·RTO와 장애 범위를 정의하고 검증한다.

권고안의 초기 시험 목표는 아래와 같다. RPO는 허용 데이터 유실 시간, RTO는 복구 소요 시간이다.

| 상황 | 대응 및 초기 목표 | 조건·제외 |
| --- | --- | --- |
| PostgreSQL/Pod 장애 | 같은 PVC를 사용하는 Pod 재기동, 재연결 10분 이내, 커밋 데이터 보존 확인 | 노드·EBS·제어면 정상, 자동 failover 아님 |
| DB 데이터/PVC 사용 불가 가정 | S3에서 새 Cluster로 수동 복구, RPO 15분·RTO 60분 목표 | 실제 볼륨은 삭제하지 않고 복원 시험으로 대체, 정상 base backup과 WAL·IAM·용량 필요 |
| A의 잘못된 데이터 변경 | 임시 Cluster PITR → A만 추출·검증·복구, 실행 시작부터 60분 목표 | A 쓰기 중단 필요, B 데이터 보존 확인, 실제 개인정보 없음 |
| 노드/AZ 장애 | 동일 AZ 자원으로 재배치하거나 수동 복원 | 복구 시간 상한·무중단·AZ 내성 보장 없음, 실제 장애 주입 제외 |
| 리전/S3/전체 계정 장애 | 이번 범위에서 복구 보장 없음 | 타 리전·타 계정 백업 제외 |

- 시험 부하는 A/B DB 합산 `pg_database_size` 1GiB 이하, 앱 연결 합계 10개 이하로 제한한다. 대용량 성능·부하 격리 보장은 제외한다.
- Pod 복구 시간은 재생성 요청부터 앱 계정 재연결·CRUD 검증 완료까지 측정한다.
- 전체 손실 복구 RTO는 운영자의 복구 실행 시작부터 데이터 검증·접속 전환·CRUD 성공까지다. 탐지·승인 대기 시간은 별도 기록하고 감추지 않는다. 자동 복구 SLA로 표현하지 않는다.
- 장애 RPO는 모의 장애 시각 `t0`와 그때 S3 보관이 완료된 base backup·WAL만으로 복원 가능한 최신 커밋 marker의 차이로 측정한다. 마지막 원본 커밋·WAL의 S3 도착 시각도 함께 기록한다. `t0` 이후 도착한 archive를 사용한 결과는 장애 RPO 통과로 인정하지 않는다.
- `archive_timeout` 5분은 초기 설정이며 RPO 5분 보증이 아니다. 업로드 지연·실패가 있으면 RPO 목표를 초과할 수 있다. 시험 중 WAL 보관 지연·백업 실패·용량은 수동으로 확인하고 기준 초과 시 다음 시험을 중단한다. 상시 탐지·자동 경보·외부 알림은 이번 단계의 완료 기준이 아니다.

## 3. 범위와 제외 범위

포함:

- AWS workload에 CNPG Operator 1개와 `postgres-poc` 단일 인스턴스, 기본 노드 구성 재사용.
- workload baseline의 `charlie-apps`·`charlie-db`·gp3 등 필요한 기반 준비, workload Sealed Secrets 선행 설치. `cnpg-system`에 Operator/Barman plugin, `cert-manager`에 인증서 컨트롤러, `charlie-db`에 DB 자원. `charlie-apps`에 가상 서비스 검증 Job.
- A/B Database, 서비스별 owner/migration 계정과 최소 권한 runtime 계정, Secret 참조, 앱→DB TLS 검증.
- DB 전용 암호화 gp3 StorageClass (`Retain`, `WaitForFirstConsumer`, 확장 가능), PVC, 자원 예산, 통신 정책.
- Barman Cloud Plugin, 전용 S3 저장소·IAM·Pod Identity, 백업 스케줄·보존, 복원 예제 및 runbook.
- 초기 데이터 주입·검증 fixture, 수동 상태/metrics/백업 확인·RPO/RTO 측정, 로컬 검사, 문서와 ADR.

제외:

- GCP/GKE, Publication/Subscription/논리복제, 클라우드 간 네트워크, DB Provisioner/Migration Controller, PgBouncer.
- 복제본·HA·동기 commit, 서비스별 강한 CPU/I/O 격리, DB별 무중단 보장, 리전·계정 장애 DR.
- 공통 EKS 모듈 기본값, 노드 증설, VPC/SG/CIDR, management addon 변경, 기존 리소스 주소/state 이동·교체.
- 관리 플랫폼의 `hopcharlie-postgres`·API·gateway·PVC·SSM 설정 및 platform 저장소 소유 IAM/ECR/SSM 변경. 기존 플랫폼 DB의 CNPG 통합·이전·백업 전환과 orchestrator의 신규 DB/DSN 연결도 제외한다. 새 DB는 A/B 합성 fixture용이다.
- CI AdministratorAccess·OIDC·account 정책 변경, root 전체 sync/자동화 활성화, 무관한 addon sync, GKE GitOps target 일반화.
- workload Prometheus/Grafana/Alertmanager·장기 scrape 토큰 설치, 관리 Prometheus 변경·cross-cluster scrape·새 외부 알림 수신처. metrics-server로 DB/WAL 경보를 대체하지 않는다.
- 실제 운영 데이터, 외부 공개 DB endpoint, 장기 AWS access key, 신규 Secret Manager/Secret 동기화 controller.
- 노드 중지·AZ 차단·실제 PVC 삭제, 자동 파괴성 CI/Argo hook, 실행 승인 없는 조회·배포·자격 증명 작업.

## 4. 구성과 소유권

| 대상 | 소유자·방식 |
| --- | --- |
| S3·IAM 역할 | foundation Terraform. 신규 bucket은 public 차단·TLS 강제·SSE-S3·`force_destroy=false`·`prevent_destroy` 및 CI 삭제 방어 |
| Pod Identity association | workload Terraform. source/restore의 정확한 namespace·ServiceAccount에 연결 |
| workload Namespace | 기존 workload baseline Application이 `charlie-apps`·`observability`·추가 `charlie-db`·`cnpg-system`·`cert-manager` Namespace의 단일 선언 소유자. CNPG/개별 addon chart는 같은 Namespace 객체를 중복 선언하지 않음 |
| Operator·cert-manager·Barman plugin·DB CR | Management Argo CD의 CNPG 전용 수동 Application/AppProject가 workload에 배포. 기존 root sync와 분리. EBS CSI/Pod Identity agent는 기존 Terraform 소유 유지 |
| 서비스 DB·Role | Git에 선언한 CNPG 자원, 플랫폼만 수정. CNPG CR 수정 권한을 앱 계정에 부여하지 않음 |
| DB 내부 스키마·권한 | 명시적으로 실행하는 migration SQL/Job. CNPG Database CR의 생성과 SQL grant 완료를 별도 검증 |
| 자격 증명 | workload Sealed Secrets controller를 먼저 준비한 뒤 DB 운영 주체가 관리. CNPG 참조 Secret은 DB namespace, runtime 소비 Secret은 앱 namespace에 각각 존재. 기존 관리 플랫폼의 SSM/init 방식은 변경하지 않음 |
| 데이터 주입·복구·접속 전환 | 승인된 수동 Job/runbook. Argo self-heal 대상과 충돌하지 않도록 복구 전환 시 Git을 기준으로 변경 |

owner/migration 계정은 자기 DB의 스키마 변경에만 사용하고 앱 runtime 계정에 owner membership, SUPERUSER/CREATEDB/CREATEROLE/REPLICATION을 주지 않는다. DB의 PUBLIC CONNECT/TEMP 및 schema·table·sequence/default privileges를 명시해 A/B 격리를 구성한다. 관리·복구용 내부 계정의 접근은 별도 유지한다.

앱 namespace에는 runtime Secret만 제공한다. 동일 runtime 자격 증명의 DB/app Secret 두 사본은 하나의 플랫폼 절차로 생성·배포·교체하며, 평문을 values·SQL·Terraform/state·로그에 넣지 않는다. 자격 증명 생성·인증서 조회·등록은 별도 실행 승인 이후다. 자동 비밀번호 회전은 이번 범위에서 제외하고 수동 교체·재연결 절차를 문서화한다.

기존 공용 StorageClass·management 기본 정책은 유지한다. 앱 egress의 신규 DB 예외는 workload opt-in 값으로 제한한다. DB namespace에는 기본 차단 후 앱→DB 5432, Operator/instance manager, plugin, 모니터링, DNS·Kubernetes API·S3 HTTPS·Pod Identity agent 등 고정 버전의 필요 통신만 허용한다. TLS는 CNPG CA와 hostname 검증을 포함하며 plugin 내부 TLS는 cert-manager가 관리한다.

Sealed Secrets가 Git addon 목록에 있다는 것만으로 준비 완료로 보지 않는다. controller·CRD·대상 공개 인증서·복구 키 관리 책임을 확인한 뒤에만 봉인·등록한다. 기존 management 플랫폼의 SSM 비밀값을 읽거나 복사하지 않는다. CNPG `-rw`는 workload 내부 ClusterIP이며 management 플랫폼에서 바로 연결되는 endpoint로 간주하지 않는다.

신규 CNPG AppProject와 cert-manager/CNPG/plugin/DB용 Application manifest를 기존 root와 별도로 준비한다. 신규 앱은 `automated`와 초기 `operation`을 없애 수동 상태로 만들고, chart 버전과 Git values commit을 고정해 단계별 sync한다. workload baseline·Sealed Secrets의 기존 수동 Application은 해당 대상만 검토·sync하고 manual 상태를 유지한다. 기존 `helm/gitops` 전체 자동화 기본값을 바꾸거나 root를 sync하지 않는다. 기존 플랫폼·LBC·관측 앱의 spec/operation·Pod/PVC 보존을 전후 확인한다. 첫 새 동작 전 root가 수동이며 진행/대기 operation이 없는지 확인하고, 자동화가 감지되면 임의 정책 패치 대신 중단·재검토한다.

baseline의 Namespace 적용·보안 label 확인을 먼저 완료한 뒤 신규 CNPG 앱은 `CreateNamespace=false`로 실행한다. upstream chart의 Namespace 생성 옵션도 끄고 렌더링 시 baseline과 중복 소유가 없는지 확인한다. `kube-system`은 기존 namespace이며 Sealed Secrets를 위해 다시 만들지 않는다. baseline의 `applicationNamespace=charlie-apps`는 유지하고 DB 전용 quota/정책/Retain SC만 CNPG chart가 소유한다.

## 5. 백업·복원 설계

- 초기 full backup을 만들고, 이후 매일 1회 base backup과 연속 WAL 보관, Barman 보존 정책 7일을 사용한다. S3 lifecycle로 필요한 WAL을 먼저 지우지 않는다.
- source는 자기 백업 prefix에 필요한 List/Get/Put/Delete 권한만 가진다. 복원 Cluster 이름은 `postgres-poc-restore`로 고정한다.
- restore role은 source prefix 읽기, 별도 `restore/` prefix의 시험 입력 사본·출력 백업 읽기/쓰기/정리에만 허용한다. source prefix의 쓰기·삭제는 거부한다. 복원용 ServiceAccount에도 별도 association을 준비한다.
- 복원 결과의 신규 WAL/백업을 원본 archive에 덮어쓰지 않는다. source/restore의 ObjectStore·serverName·destination을 구분하고 렌더링·실행 시 확인한다.
- full restore와 PITR의 각 시험은 새 bootstrap과 새 PVC로 시작한다. IAM trust를 위해 Cluster/ServiceAccount 이름 `postgres-poc-restore`는 유지하되 시험은 순차 실행한다. 재사용 전에 기존 임시 Cluster/PVC 정리를 명시적으로 승인받고, Retain으로 남은 PV/EBS를 별도 추적한다. 이전 PVC/PV를 새 시험에 재결합하지 않는다.
- 출력은 `restore/<run-id>/`처럼 시험마다 새로운 prefix를 사용하고 올바른 plugin `serverName`을 지정한다. restore role의 출력 권한은 공통 restore prefix 아래로만 제한한다. source archive 확인과 새 출력의 empty-archive 검사를 끄거나 우회하지 않는다.
- 장애 RPO 시험에서는 `t0` 이전에 완료된 base backup과 WAL 오브젝트 목록·보관 완료 시각을 고정하고, 그 집합만 `restore/<run-id>/input/`에 복사해 복원 입력으로 사용한다. 복사 전후 원본 오브젝트 식별자/ETag 일치를 확인하고 변경·누락되면 시험을 실패 처리한다. 출력은 별도의 `restore/<run-id>/output/`에 둔다. 원본의 이후 업로드를 복원 입력에 추가하지 않는다. 목록·완료 시각 확인/복사를 위한 S3 조회·쓰기 역시 실행 승인 대상이며 백업 내용은 문서·로그에 출력하지 않는다.
- PITR은 과거 Role/password catalog를 되살리므로 승격 완료 후 복원 Cluster를 참조하는 Role 선언과 현재 Secret을 재조정한다. 복원 대상 hostname·CA를 사용해 TLS와 현재 계정으로 접속 검증한 뒤에만 클라이언트 접속을 전환한다. Secret/CA가 원본과 자동으로 같다고 가정하지 않는다.
- source와 restore에 모두 EKS Pod Identity를 사용한다. 선택한 Barman/operand 이미지의 boto3·botocore 및 sidecar credential 전달 지원을 고정 버전 자료로 확인한다. 미지원이면 access key/IRSA로 자동 전환하지 않고 계획을 재검토한다.
- S3는 DB 물리 백업이지 Kubernetes Secret/Sealed Secrets 개인키 백업이 아니다. Secret 재생성·재등록과 Sealed Secrets 복구 키의 보존 책임을 별도 runbook으로 명시한다. 개인키·비밀값은 Git에 저장하지 않는다.
- 백업 성공을 확인한 뒤 별도 Cluster 복원·시점 검증을 통과해야 복구 가능한 상태로 판정한다.

## 6. 변경할 파일·구성 요소

아래는 승인 대상 예상 범위다. 새 파일의 세분화와 이름은 동일 소유권·권한 범위 안에서 조정할 수 있다.

| 영역 | 예상 파일 |
| --- | --- |
| S3·IAM·outputs | 신규 `terraform/environments/aws/dev/foundation/cnpg.tf`, 기존 `foundation/outputs.tf` |
| Pod Identity | 신규 `terraform/environments/aws/dev/workload/identity.tf` |
| 삭제 guard | `scripts/check-foundation-plan.py`, `scripts/tests/test-tf-ci.py` |
| Terraform 검증 | foundation의 신규 backup/IAM mock test, workload Pod Identity test. 기존 account 관리자/제한 정책 검사 유지 |
| 수동 GitOps 등록·준비 확인 | 신규 CNPG 전용 AppProject/Application manifest 예제(예: `helm/cnpg-poc-applications.yaml.example`), 렌더링 fixture. 기존 workload baseline·Sealed Secrets Application은 전체 root 없이 해당 앱만 수동 사용 |
| workload values | `clusters/aws-dev-workload/values/baseline.yaml`, 신규 `cloudnative-pg.yaml`, `plugin-barman-cloud.yaml`, `cert-manager.yaml` |
| 네트워크 예외 | `helm/charts/cluster-baseline/values.yaml`, `values.schema.json`, `templates/baseline.yaml`의 workload opt-in |
| DB·보안·수동 관측 | 신규 `helm/charts/cnpg-poc/` chart와 workload values: Cluster/Database/DatabaseRole/StorageClass/Quota/LimitRange/NetworkPolicy/ObjectStore/ScheduledBackup. Prometheus CRD를 요구하는 PodMonitor/ServiceMonitor/PrometheusRule은 기본 렌더링에서 제외 |
| 버전·이미지·Helm 검사 | `helm/versions.json`, `helm/images.lock.json`, `scripts/check-helm.py` 및 필요한 fixture |
| 가상 데이터·수동 실행 | 신규 `examples/cnpg-poc/` SQL·Job·restore 예제, 검사 스크립트 및 `docs/runbooks/cnpg-poc.md` |
| 기록 | 이 계획, ADR 0005/0006, README 파일 목록·사용 안내 |

CNPG 전용 AppProject는 workload destination의 지정 namespace와 필요한 저장소·리소스 종류만 허용한다. cert-manager의 CRD·ClusterRole·webhook 및 namespaced Issuer/Certificate 권한도 여기에 필요한 범위만 추가한다. management에 새 workload addon을 설치하지 않는다. 기존 GitOps schema/map과 management AppProject/플랫폼 chart는 변경하지 않는다. 실제 chart의 명시적 선행 의존성 때문에 이 경계를 넘어야 하면 새 범위로 재검토한다.

CI는 현재 AdministratorAccess 정책이 선언돼 있어 제한 정책을 더 추가할 필요가 없다. 기존 account 정책·OIDC trust·6,144자 제한 검사는 유지한다. 이 관리자 권한을 신규 source/restore DB runtime 역할에 전달하지 않는다. 공유 JSON schema도 확장하지 않고 기존 project/environment/region/account 입력에서 bucket·역할 이름을 도출한다. PoC 이름·보존·DB 자원 값은 CNPG 전용 Terraform/Helm 설정으로 관리한다.

## 7. 단계와 의존성

1. 계획·ADR 초안 작성 → 새 에이전트 리뷰 → 지적 반영 → 사용자 개발 계획 승인.
2. 승인 후 코드 작업: Kubernetes 1.35 지원, CNPG DatabaseRole 지원(1.30 이상), PostgreSQL 지원 버전, plugin/cert-manager의 지원 조합을 공식 자료로 확정하고 chart·controller/operand/plugin/test 이미지 digest를 고정한다. chart가 CR에 암묵적으로 넣는 이미지도 검사한다. 비지원 조합/신규 권한·도구가 필요하면 중단하고 재계획한다.
   채택된 [공유 입력 ADR 0003](../adr/0003-share-project-inputs-between-local-and-ci.md), [검증 ADR 0004](../adr/0004-parallelize-infrastructure-checks.md), [선택 배포 ADR0008](../adr/0008-select-terraform-stack-and-action.md)을 따른다. 명명·backend 입력은 기존 공유 구성/wrapper를 사용하고 별도 tfvars·CI 배포 경로·공유 입력 schema 확장을 도입하지 않는다.
3. Terraform S3/IAM/association, 삭제 guard와 mock test 구현. 기존 state 주소를 유지하고 비밀값 없는 output만 추가한다.
4. workload namespace·전용 SC·정책 및 전용 수동 앱 등록을 구현한다. 승인 후 배포 순서는 필요한 workload baseline/gp3/namespace 준비 → workload Sealed Secrets controller·인증서 준비 → cert-manager API·컨트롤러 준비 → CNPG CRD·Operator 준비 → Barman CRD·plugin/인증서 준비 → 명시적 자격 증명 승인 아래 Secret 등록 → DB/Role/ObjectStore → DB·SQL grant·backup 준비 → 앱 검증이다. baseline·Sealed Secrets의 기존 앱과 CNPG 전용 새 앱만 수동으로 취급하며 다른 addon은 설치하지 않는다. 부모 Application sync wave만으로 자식 준비를 보장한다고 가정하지 않는다.
5. 서비스 DB·Secret 참조·권한 migration·fixtures·복구 예제·수동 검사 구현. 초기 DB 요청값은 CPU 500m/메모리 1Gi, limit CPU 1/메모리 2Gi, PVC 20Gi, max_connections 50, runtime role별 connection limit 10으로 시작한다. 실제 데이터·부하 확인에 따른 동일 자원 예산 내 조정은 기록한다.
6. 로컬 검사를 통과한 코드 diff·권한 요약·실행 runbook 제시. 이 시점까지 AWS/클러스터 조회·apply·Secret 생성·장애 주입은 실행하지 않는다.
7. 별도 조회 승인 후 현재 배포 revision과 기록의 차이, 두 클러스터 대상·root/앱 수동 상태·무관 operation 부재, 기존 리소스 identity, addon 중복·지원 버전, 실제 용량·네트워크·자격 증명 준비 상태를 확인한다. source+임시 restore, controller/sidecar, cert-manager, 새 Sealed Secrets controller, A/B 검증 Job, 기존 시스템/metrics 자원 및 CPU·메모리 20% 여유를 함께 계산한다. 이전 workload 여유 측정값을 현재 여유로 재사용하지 않는다. 부족하면 자동 증설하지 않고 추가 계획·승인을 받는다.
8. 별도 실행 승인 후 최신 승인 main 기준 `foundation` 선택 plan 검토/apply → `workload` 선택 plan 검토/apply → 지정 앱만 Argo staged sync한다. CI는 action/stack을 명시하고 `all-runtime`, account/bootstrap/management apply는 사용하지 않는다. CI의 foundation 삭제 guard를 확장·유지한다. 로컬 `tf-stack.sh apply`는 같은 guard를 자동 실행하지 않으므로 대체 실행 시 동일 plan에 guard와 주소/삭제·교체 검토가 별도로 완료돼야 한다. 비밀값을 노출하지 않는 변경 요약만 기록하며 실제 plan/state는 문서·artifact로 남기지 않는다.
9. 합성 데이터 주입과 정상/접근 거부 검증 → 초기 backup/WAL 확인 → Pod 재생성 시험 → 별도 Cluster full restore/PITR → A만 복구 검증. 수동 관측·RPO/RTO와 기존 관리 플랫폼·PVC 무변경 결과를 기록한다. 파괴성 데이터 변경은 지정 합성 DB로만 한정한다.

## 8. 검증 방법과 수용 기준

코드 검증:

- `make check`의 통합 검증(공유 입력 검사, fmt, backend 없는 validate/mock test, CI fake test, Helm render, inventory)을 따른다. 필요 시 `make tf-check`, `make tf-test`로 문제 범위를 확인하되 통과한 동일 Terraform 검사를 불필요하게 반복하지 않는다.
- management 렌더 결과와 기존 공용 기본값이 유지되는지 비교한다.
- DB runtime IAM prefix·SA trust·복원 권한 분리·삭제 guard를 mock/fake 검사한다. 기존 CI 관리자 attachment/OIDC/account 정책을 바꾸지 않았고 새 SG/공개 endpoint/장기 key/state migration이 없음을 확인한다.
- 전용 수동 앱에 automated/operation이 없고 대상이 workload로만 고정되는지 검사한다. root/기존 앱 자동화 기본값과 management 렌더링을 변경하지 않는다. chart의 Helm Hook이 필요한 경우 기본 Hook 전략을 유지하며 Force/Replace/prune는 사용하지 않는다.
- CRD·addon namespace·Sealed Secrets 선행 준비, operand/plugin 이미지 고정, DB 보존 정책·금지 권한·TLS/Secret 참조, workload-only 네트워크 예외를 렌더링 검사한다. Prometheus CRD가 없는 workload에서도 기본 CNPG 리소스 렌더링/적용이 가능한지 확인한다.

실행 검증(승인 후):

- A/B 각각 CRUD·트랜잭션 rollback·sequence 증가, 상호 DB 접근 거부, 미허용 Pod 접근 거부, TLS hostname 검증 실패도 확인한다.
- source Pod 재생성 뒤 commit marker·검증 합계 보존과 10분 이내 재연결 여부를 측정한다.
- base backup 이후 서로 다른 시점 marker를 기록하고 자연 WAL 보관을 관찰한다. 수동 WAL switch는 기본 RPO 증명의 대체로 쓰지 않는다. 모의 장애 `t0`에서 보관 완료된 오브젝트 집합으로 고정한 입력 사본을 사용해 복원하고, `t0` 이후 보관된 WAL의 도움 없이 RPO를 산출한다. 전체 복원 기능 성공과 장애 RPO 통과를 별도 판정한다.
- 원본 PVC가 없다는 가정으로 새 Cluster에서 full restore한다. 원본 볼륨은 실제 삭제하지 않는다. 원본 source archive가 변경되지 않았는지 확인한다.
- 각 복원 시험에서 run-id·새 PVC/PV UID·출력 prefix·source/target serverName을 기록하고, 이전 복원 데이터/볼륨을 사용하지 않았음을 확인한다. 실행 전 승인된 임시 자원 정리와 잔존 Retain PV/EBS 비용을 확인한다.
- A/B에 marker를 추가한 뒤 특정 시점 PITR을 실행해 이전 marker 존재·이후 marker 부재를 확인한다. A만 추출해 A를 복구하고 B의 marker·데이터 검증 합계가 동일한지 확인한다.
- 복구 시 승격 완료 → restore 대상 Role/현재 Secret 재조정 → restore hostname·CA의 TLS·권한 검증 → 앱 접속 전환 순서를 확인한다. GitOps와의 일치, 새 backup 경로의 archiving 성공까지 검증한다.
- DB 가용성·backup 실패/마지막 성공(일일 스케줄 기준 26시간 초과)·활동 중 WAL 보관 지연(10분)·PVC 잔여 공간(20% 주의/10% 시험 중단)·연결 수(80%)를 승인된 상태/SQL/metrics 조회로 수동 확인한다. CNPG exporter 접근이 필요하면 workload 대상 loopback 포트포워딩 등 지정 점검 경로만 사용하고 공개 Service/Ingress나 상시 management scrape를 만들지 않는다. 시험 중 기준 초과를 기록하고 중단하며 자동 탐지·외부 통지는 미검증으로 보고한다.
- workload Sealed Secrets의 신규 설치·봉인/Secret 등록을 명시적으로 승인받았는지 확인한다. controller bootstrap Secret·봉인 키 생성 및 CNPG/plugin/cert-manager가 생성하는 내부 인증서·키도 실행 승인에 포함한다. 관리 Prometheus의 기존 장기 토큰 승인을 workload의 신규 토큰 생성 승인으로 확대하지 않는다.
- 목표 초과 시 수치를 완화해 통과 처리하지 않고 원인·재시험 또는 후속 계획으로 남긴다. 배포/기능 성공과 복구 목표 통과를 구분한다.

## 9. 위험·필요 권한·승인 경계

현재 승인된 범위는 계획에 따른 코드·설정 구현과 로컬 검증이다. 아래 실제 환경 실행은 아직 승인되지 않았다.

- 조회: 기존 승인 로그인·로컬 EKS 접속 도우미를 쓰는 해당 AWS workload/management 상태·용량·현재 revision/수동 앱/operation·리소스 identity, addon·S3/IAM 관련 비공개 설정과 Sealed Secrets 공개 인증서. Secret 평문·감사 로그는 조회 대상에서 제외한다. 인증 만료 시 새 로그인/토큰 발급·Secret 읽기로 우회하지 않는다. 기존 [접속 ADR](../adr/0014-local-eks-access-commands.md)을 따른다.
- 변경: foundation S3·DB runtime 역할, workload Pod Identity, 지정 workload baseline·Sealed Secrets, CNPG 전용 관리 AppProject/Application 등록, workload의 cert-manager/CNPG/plugin CRD·RBAC·webhook·정책·PVC·DB 및 신규 내부 TLS/봉인 키·SealedSecret 등록, 실행을 유발하는 지정 앱 sync/선택 CI. account CI 정책 변경과 root 전체 sync는 제외한다.
- 중단: 실패 시 이번 승인 대상에 한해 자신이 시작한 Argo operation의 종료 요청을 승인 범위에 포함한다. 자동화 해제만으로 진행 작업이 중지됐다고 표현하지 않는다. 기존 앱·다른 사용자 터널/operation을 중단하지 않고 생성된 PVC/Secret/CRD는 자동 삭제하지 않는다.
- 시험: 명시한 합성 DB에 seed·오류 데이터 변경·복원, DB Pod 재생성, 임시 restore Cluster/PVC·검증 Job 생성. 노드·AZ·원본 PVC 삭제는 제외한다.
- 영향: 새 CNPG A/B 서비스의 단일 DB 장애 중단, 신규 EBS source20GiB+임시 restore20GiB 및 잔존 Retain 볼륨·S3/사본·요청·전송 비용, CRD/RBAC/webhook·Sealed Secrets controller/봉인 키 추가, DB runtime IAM 변경. 기존 관리 플랫폼 DB 중단은 허용 범위가 아니며 의도하지 않은 영향이 감지되면 중단한다. workload 관측 스택 PVC27GiB/장기 토큰은 이번 비용·권한 범위에 포함하지 않는다. 월 비용 확정은 실제 대상·사용 기간·용량 확인 후 실행 승인 자료에 제시한다.
- 보호: 자동 prune 금지, DB/Role reclaim retain, 전용 PV Retain, bucket 삭제 방어. 보존 설정이 수동 삭제·관리자 오류까지 막지는 않는다. CNPG CRD나 namespace 삭제를 teardown 수단으로 사용하지 않는다.
- 철거는 별도 데이터 보존·비용 검토 후 대상별 승인한다. 임시 restore Job/Cluster/PVC 정리도 명시한 실행 승인 범위에 포함해야 하며, retained PV/EBS는 별도 확인한다.

사용자는 단일 인스턴스·수동 복구, 초기 RPO/RTO 시험 목표, 자동 경보 대신 수동 측정으로 한정한 수정안의 코드 구현을 승인했다. 실제 적용 시점의 revision·자원 여유는 실행 승인 뒤 다시 검증한다. HA, 노드 증설, IRSA/Sealed Secrets 외 새 Secret controller, 외부 노출, GKE, 실제 데이터, workload Prometheus/cross-cluster 수집이 필요해지면 범위 변경 계획을 다시 리뷰받고 승인받는다.

## 10. 리뷰 및 변경 이력

- 독립 리뷰: 새 `plan-reviewer` 에이전트 `poc_plan_review`가 완전성·품질·실현 가능성·범위를 검토했다.
- 반영 1: 반복 복원마다 새 bootstrap/PVC와 고유 input/output archive를 사용하고, 고정 ServiceAccount·정리 승인·잔존 PV/EBS 추적을 유지한다.
- 반영 2: 모의 장애 `t0`에 보관 완료된 백업/WAL만 고정해 장애 RPO를 측정한다. 이후 업로드를 사용한 복원 성공과 구분한다.
- 반영 3: 복원 승격 후 현재 Role/Secret 재조정과 복원 hostname·CA·권한 검증을 접속 전환의 선행 조건으로 한다.
- 재검토 결과: 네 항목 모두 PASS, 미해결 CRITICAL/MAJOR 없음. 사용자 개발 계획 승인 대기이며 실행 승인은 포함하지 않는다.
- 문서 검증: `make inventory-check` 통과, 새 문서 3개의 로컬 링크 17개와 README 등록 확인, `git diff --check` 통과. 최초 inventory 실패는 동시 작업의 README 누락이 해소된 뒤 재검사하여 통과했다.
- 계획 대비 변경: 기존 범위를 유지하며 복원 시험·RPO 측정·자격 증명 재조정 기준을 구체화했다. 동시 작업의 ADR 0003/0004를 보존하고 이번 제안 ADR을 0005/0006으로 배정했다. 구현 코드·클라우드·Secret 변경은 없다.

### 2026-11-03 재검토

- 이전 GREEN은 11월2일 전제에 대한 판정이다. 이번 수정안은 새 `plan-reviewer` 에이전트 `cnpg_updated_plan_review`가 계획·ADR 전체를 독립 검토해 GREEN으로 판정했다. 미해결 CRITICAL/MAJOR는 없으며 사용자 승인은 아직 대기다.
- 유지: AWS 단일 CNPG·A/B 합성 데이터·1GiB/10연결·RPO15분/RTO60분·새 PVC/시점 고정 WAL 복구 시험, HA/GKE 이전 제외.
- 변경: 플랫폼 관리 DB 보호, account/CI 권한 확장 제외, 선택 foundation/workload 적용, root 없이 전용 수동 앱, workload baseline·Sealed Secrets 선행 설치, 수동 관측으로 최초 범위 한정.
- 승인 경계: 이 재검토 요청은 구현·실제 AWS/클러스터 조회·배포 승인으로 사용하지 않는다. 로컬 계획·ADR 보완만 수행한다.
- 리뷰 반영: Namespace는 baseline 단일 소유자로 명시하고 신규 앱의 CreateNamespace를 끈다. 신규 CNPG 수동 AppProject/Application manifest와 baseline·Sealed Secrets의 한정 수동 sync 경계를 구체화했다. ADR0006의 과거 CI 권한 추가 표현도 제거했다.
- 문서 검증: 변경 문서 3개의 로컬 링크·README 등록과 공백 검사는 통과했다. 전체 `make inventory-check`는 `helm/bootstrap/charts/argo-cd-10.9.6.tgz`라는 기존 Helm 다운로드 산출물의 README 미등록으로 실패했다. 해당 산출물을 임의 삭제하거나 이번 계획의 파일 목록에 추가하지 않았다. 구현 코드 미변경으로 인프라 테스트·실제 조회/배포는 수행하지 않았다.

## 11. 승인된 읽기 전용 배포 확인 — 2026-11-03

사용자는 두 EKS의 Namespace·CRD·Deployment·StatefulSet·Pod·PVC 및 관리 Argo Application의 이름·상태·소유권을 확인하는 구체적 요청에 “진행해도 될 것 같고”라고 승인했다. 개발 시 새 브랜치를 만들고 `codex` prefix를 쓰지 않는 조건도 추가했다. 이번 실제 실행은 이 읽기 전용 범위로 한정했으며 구현·apply·sync·자격 증명 생성은 하지 않았다.

- 점검 시각: 2026-11-03 약 14:49 KST.
- 대상: AWS 계정 `525091405886`, `ap-northeast-2`, `charlie-dev-management` 및 `charlie-dev-workload`.
- 기존 `charlie-admin` 인증으로 계정 일치, 각 EKS ARN·private endpoint·CA, 같은 VPC의 단일 running/SSM Online bridge를 확인했다.
- 저장소 접속 도우미의 알려진 hostname 대소문자 비교 문제와 고정 kubeconfig 덮어쓰기를 피하기 위해 별도 보호 임시 파일을 사용했다. EKS CA·TLS hostname 검증은 유지했고 저장소 접속 코드는 변경하지 않았다. 전역 kubeconfig 및 기존 사용자 터널도 변경하지 않았다.

| 대상 | 실제 관측 |
| --- | --- |
| 두 EKS | ACTIVE, Kubernetes 1.35 |
| CNPG·Barman | 양쪽 모두 `postgresql.cnpg.io`·`barmancloud.cnpg.io` CRD 없음. 관련 Operator/DB Pod·Deployment·StatefulSet 없음 |
| cert-manager·Sealed Secrets | 양쪽 모두 관련 CRD·controller 배포 없음 |
| 관리 플랫폼 | API·console·gateway Deployment 각각 Ready 1/1, `hopcharlie-postgres` StatefulSet Ready 1/1. 플랫폼 PVC 3개 Bound(10+2+1GiB) |
| 관리 관측 | metrics-server Ready 2/2. Grafana·Prometheus·Alertmanager 및 관련 수집 Pod Running/Ready. 관측 PVC 3개 Bound(20+5+2GiB) |
| workload | CoreDNS/VPC CNI/kube-proxy/EBS CSI/Pod Identity와 metrics-server Pod만 관측. metrics-server Ready 2/2. 앱 Deployment·DB StatefulSet·PVC 없음 |
| workload namespace | default·headlamp·kube-node-lease·kube-public·kube-system만 존재. charlie-apps·charlie-db·cnpg-system·cert-manager·observability 없음 |
| 관리 Argo | metrics-server 두 앱과 관리 kube-prometheus-stack은 Synced/Healthy·마지막 operation Succeeded. workload baseline·Sealed Secrets·Prometheus·LBC·Rollouts는 Missing. CNPG 관련 Application 없음 |

배포 목록 점검 결과는 CNPG가 아직 설치되지 않았다는 기존 계획의 전제를 뒷받침한다. 관리 플랫폼 PostgreSQL은 별도로 존재하므로 “DB가 전혀 없다”는 의미가 아니다. 이 점검은 DB 접속·데이터·백업 정상 여부, 현재 자원 여유, 모든 Argo 자동화/대기 operation 필드, GKE·RDS 등 외부 DB를 검증한 것이 아니다. 구현·실행 전 게이트는 계속 적용한다.

Secret 값·DB 데이터·Pod 로그·SSM Parameter 값·state/plan은 읽지 않았다. 이번에 시작한 SSM 세션 2개는 명시적으로 종료 확인했고 임시 kubeconfig 2개는 제거했다. 현재 브랜치는 `feat/aws-cnpg-poc`이며 기존 작업 파일을 보존했다. 새 아키텍처 결정은 없어 ADR 추가·변경은 없고 기존 제안 ADR0005/0006을 유지한다.

## 12. 승인된 코드 구현·로컬 검증 — 2026-11-03

사용자는 구현 가능 여부와 실제 배포를 구분한 질문 뒤 “시작해줘”로 수정 계획의 코드·설정 구현을 승인했다. 실제 환경 배포·Secret 생성·시험 실행은 이번 승인에 포함하지 않는다.

- 작업 위치: `feat/aws-cnpg-poc`, 별도 `/private/tmp/charlie-cnpg-zoz8j1ou/worktree`. 로컬에 저장된 `origin/main@82b6617`로 fast-forward한 뒤 구현했다. 원래 checkout의 다른 작업은 보존했고 원격 main fetch·push·PR merge는 실행하지 않았다.
- 구현: foundation 전용 S3/source·restore runtime IAM/비밀 아닌 outputs, workload Pod Identity, bucket 삭제 guard, 단일 CNPG와 A/B DatabaseRole·TLS/HBA·전용 Retain 스토리지·자원·네트워크·Barman 백업, workload Namespace·Sealed Secrets 선행 조건 및 수동 AppProject/Application 예제.
- 수동 시험 산출물: synthetic migration·CRUD/권한/TLS/네트워크 거부·marker/fingerprint·A-only app schema 복구 Job, 초기 Backup, 새 PVC full/PITR restore chart, 실행 플래그 없는 기본값은 AWS를 호출하지 않는 archive 고정 helper, [실행 runbook](../runbooks/cnpg-poc.md).
- 실제 고정값은 [versions](../../helm/versions.json)와 [image lock](../../helm/images.lock.json)에 기록했다. CNPG chart0.29.1/operator1.30.1, PostgreSQL18.6, plugin chart0.8.1/v0.15.1, cert-manager1.21.3이며 신규 이미지9개의 registry digest를 확인했다. SDK 지원은 고정 공식 소스 근거이며 실제 주입/연결은 검증 전이다.
- 검증: 고정 Helm3.19.1·Terraform1.16.4·PyYAML6.0.3으로 전체 `make check` 통과. AWS 6개 root backend-free validate/mock29개, 기존 CI/config/access·runner 검사, Job 프로브12개, freeze14개, 고정 CRD 구조/권한/이미지·수동 Job11종·full/PITR Helm 검사가 통과했다. inventory244개와 `git diff --check`, 문서 로컬 링크 검사도 통과했다.
- 네트워크 sandbox에서 공개 registry 조회가 막힌 첫 검사는 실패했고, 공개 provider/chart 다운로드만 허용해 동일 전체 검사를 다시 수행했다. 실제 AWS/backend/API 접속·plan/apply는 없었다.
- 독립 코드 리뷰: `cnpg_code_review`가 최종 코드·runbook을 재검토했고 미해결 CRITICAL/MAJOR 없음. SQL의 잘못된 `\\quit 1` 실패 판정을 ON_ERROR_STOP 아래 SQL 오류로 수정하고, restore bootstrap에 원본 db_a/owner/Secret을 명시했다. 정상 접속 뒤 network-denied, backupEndTime 이후 target, 새 PVC·입출력 prefix·complete 상태의 의미도 runbook에 반영했다.
- 계획 대비 범위 변경 없음. 구현 세부사항으로 Barman HeadBucket에 필요한 **전용 버킷의 목록 메타데이터 권한**을 허용하되 객체 prefix 경계는 유지했다. 선택 복구 Job은 synthetic app schema에만 한정하고 2GiB 임시 disk와 사전 write fence를 요구한다. helper는 단일 timeline·완료 full backup·객체당5GiB 이하에 한정하며 자동 정리/기존 prefix 덮어쓰기를 거부한다.
- ADR0005/0006은 개발 승인에 따른 채택으로 갱신했으며 실제 설치/백업/복구 완료로 표현하지 않는다. 기존 관리 플랫폼·계정 CI 권한·global Argo 자동화·노드·SG·VPC·state 주소는 변경하지 않았다.
- 남은 검증: 현재 배포 commit/용량·승인 범위 재확인, 실제 admission/CEL, credentials/CA·Pod Identity 주입·S3 접근·네트워크·SQL 동작, 백업/새 PVC 복원 및 RPO/RTO. 이 실행은 별도 승인 후 수행한다.

## 관련 자료

- [AGENTS.md](../../AGENTS.md), [ADR 0001](../adr/0001-record-decisions-as-adrs.md), [ADR 0002](../adr/0002-require-approval-for-privileged-operations.md)
- [CNPG 1.30 / DatabaseRole](https://cloudnative-pg.io/releases/cloudnative-pg-1-30.0-released/)
- [CNPG 장애 모드](https://cloudnative-pg.io/docs/1.30/failure_modes/), [복구](https://cloudnative-pg.io/docs/1.29/recovery/), [WAL 보관](https://cloudnative-pg.io/docs/devel/wal_archiving/)
- [Barman plugin 설치 및 cert-manager](https://cloudnative-pg.io/plugin-barman-cloud/docs/installation/), [Object storage](https://cloudnative-pg.io/plugin-barman-cloud/docs/object_stores/)
- [AWS Pod Identity SDK 요건](https://docs.aws.amazon.com/eks/latest/userguide/pod-id-minimum-sdk.html)
- [CNPG exporter 수동 점검](https://cloudnative-pg.io/docs/devel/monitoring/), [Prometheus Operator 대상 선택](https://prometheus-operator.dev/docs/api-reference/api/)

공식 문서의 current/devel 링크는 변할 수 있으므로 구현 시 실제 선택한 지원 릴리스의 문서·차트·이미지로 재확인하고 버전을 기록한다.
