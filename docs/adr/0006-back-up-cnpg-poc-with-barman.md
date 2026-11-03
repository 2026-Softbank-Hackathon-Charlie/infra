# 0006. CNPG PoC를 Barman Plugin과 전용 S3로 백업·복구

- 날짜: 2026-11-02
- 재검토: 2026-11-03 — 선행 조건·권한 범위 보완 후 개발 계획 승인
- 상태: 채택 — 2026-11-03 개발 계획 승인, 코드 구현. 실제 백업·복원 미실행
- 관련 ADR: [0002](0002-require-approval-for-privileged-operations.md), [0005](0005-scope-aws-cnpg-poc.md)

## 배경과 제약

단일 PostgreSQL PoC는 장애 중단을 수용하더라도 백업이 실제 복원 가능한지 검증해야 한다. EBS PVC 재사용만으로 데이터 오삭제·볼륨 손실에 대비할 수 없다. 2026-11-03 코드 개발 계획을 승인받았으며 실제 배포·백업·복원은 별도 실행 승인 대상이다.

기존 저장소에는 Pod Identity agent와 EBS CSI가 있고 Loki 전용 S3/IAM이 있으나 PostgreSQL 백업은 없다. Barman plugin 공식 Helm 설치는 cert-manager를 요구한다. 실제 배포 상태와 선택 이미지의 credential 지원 여부는 확인 전이다.

이 문서의 PostgreSQL 백업 제안은 신규 workload CNPG에 한정한다. 관리 플랫폼의 기존 PostgreSQL/SSM/관련 IAM 소유권을 가져오지 않는다. workload Sealed Secrets와 cert-manager가 이미 설치됐다고 가정하지 않으며, controller/키 준비·등록은 선행 실행 승인 범위로 구분한다.

## 검토한 대안

- 재주입 또는 PVC만 사용: 단순하지만 시점 복구를 검증하지 못한다.
- DB별 pg_dump만 사용: 선택 DB 복원이 쉽지만 연속 WAL/PITR 검증이 부족하다. 선택 복구의 보조 수단으로는 사용한다.
- deprecated 내장 Barman 연동: 신규 도입에 적합하지 않다.
- Barman Cloud Plugin과 전용 S3: CNPG의 물리 백업·WAL/PITR을 검증할 수 있다. plugin과 인증서 관리 의존성이 추가된다.

## 결정과 이유

Barman Cloud Plugin, ObjectStore, 초기·일일 base backup, 연속 WAL 보관, 7일 보존을 제안한다. 공식 차트를 사용하고 workload에 cert-manager를 선행 설치해 plugin TLS 인증서를 발급한다. 외부 공개 인증서·DNS/IAM issuer는 도입하지 않는다.

foundation이 전용 비공개 SSE-S3 bucket과 IAM 역할을 만들고 workload가 source/restore ServiceAccount의 Pod Identity를 연결한다. source는 자기 prefix만 관리하고 restore는 source 읽기와 별도 출력 prefix 관리만 한다. 장기 access key는 생성하지 않는다. 선택한 이미지의 SDK·sidecar Pod Identity 지원을 확인하지 못하면 다른 인증 방식으로 자동 전환하지 않고 재검토한다.

2026-11-03 별도 실행 승인으로 foundation의 CNPG 전용 S3·IAM 관련 8개와 workload의 source/restore Pod Identity 연결 2개를 생성했고, 두 stack 모두 적용 후 plan에서 변경 없음으로 확인했다. 이 클라우드 기반 준비는 Barman plugin·DB 설치나 실제 백업·복원 성공을 의미하지 않는다. 기존 Operator와 AppProject는 재사용하고, Sealed Secrets·cert-manager·plugin·DB 및 신규 키·암호 생성은 다음 실행 승인 대상으로 남아 있다.

구현에서 Barman HeadBucket 검사를 위해 두 runtime 역할에 전용 CNPG 버킷의 ListBucket/GetBucketLocation을 허용한다. prefix 없는 HeadBucket 때문에 목록에도 prefix 조건을 강제하지 않으며 같은 전용 버킷의 키 메타데이터가 보일 수 있다. 객체 내용·쓰기·삭제 경계는 source/restore prefix로 제한한다. account CI 정책과 다른 버킷 권한은 바꾸지 않는다. [Barman S3 권한 요건](https://docs.pgbarman.org/release/3.20.0/user_guide/barman_cloud.html)을 따른 구현 세부사항이다.

CI는 [ADR0009](0009-grant-ci-terraform-administrator-access.md)의 관리자 정책이 선언돼 있으므로 기존 계획의 account CI 권한 추가·account apply는 필요하지 않다. 신규 DB runtime 역할의 prefix/ServiceAccount 제한과 bucket 삭제 방어는 유지한다. 기존 CI 관리자 정책의 제거·확장이나 OIDC trust 변경은 이 결정의 범위가 아니다.

물리 백업은 전체 PostgreSQL Cluster 단위다. 새 Cluster로 복원하고, A만 복구할 때는 임시 전체 PITR 후 A만 추출·검증·복구한다. source archive를 복원 Cluster의 출력에 재사용하지 않는다. Secret/Sealed Secrets 복구는 DB 백업과 별도 절차로 유지한다.

full restore/PITR 시험마다 새 PVC와 `restore/<run-id>/` 출력 경로를 사용한다. ServiceAccount trust를 위한 고정 restore Cluster 이름을 재사용하더라도 이전 PVC/PV의 데이터는 재사용하지 않는다. 순차 시험 사이 임시 자원 정리는 실행 승인을 받고 Retain PV/EBS를 추적한다. archive 안전 검사는 비활성화하지 않는다.

장애 RPO는 모의 장애 `t0` 시점에 보관 완료된 base backup·WAL 집합만 별도 시험 입력 prefix에 복사해 측정한다. `t0` 이후 도착한 WAL은 통과 근거에 포함하지 않는다. 시험 입력·출력은 `restore/<run-id>/input/`과 `output/`으로 분리하고 source archive는 읽기만 허용한다. PITR 승격 후 현재 Role/Secret을 재조정하고 복원 endpoint·CA의 TLS 검증을 통과한 뒤 접속을 전환한다.

## 영향과 후속 작업

- 기대 효과: 스토리지 재사용뿐 아니라 실제 백업 복원·시점 복구를 검증한다.
- 단점과 위험: S3/EBS·요청·전송·임시 복원 자원 비용, cert-manager CRD/RBAC/webhook, 백업용 DB runtime IAM 역할이 추가된다. 기존 CI 관리자 권한은 변경하지 않는다. 동일 리전·계정 장애와 관리자에 의한 백업 파괴는 보장하지 않는다.
- 필요한 후속 작업 및 검증: 버전·이미지 고정, source/restore 권한·경로 분리, bucket 삭제 guard, 실제 base backup/WAL/PITR·A 선택 복원 시험. S3 lifecycle로 Barman의 복구 체인을 조기에 지우지 않는다.
- `archive_timeout=5min`은 초기값이며 RPO 보증이 아니다. 시험 중 수동 보관 상태 확인과 실제 commit marker로 목표 달성 여부를 기록한다. workload 상시 자동 경보·외부 알림은 이번 수정안의 완료 기준에서 제외하고 별도 관측 설계로 남긴다.
- 자동 prune·bucket 강제 삭제를 사용하지 않고 PV/DB/Role 보존 정책을 둔다. 비용과 데이터 보존을 확인한 철거는 별도 승인한다.

## 관련 자료

- [AWS CNPG PoC 계획](../plans/cnpg-aws-poc.md)
- [Barman plugin 설치](https://cloudnative-pg.io/plugin-barman-cloud/docs/installation/)
- [CNPG Recovery](https://cloudnative-pg.io/docs/1.29/recovery/)
- [AWS Pod Identity SDK 요건](https://docs.aws.amazon.com/eks/latest/userguide/pod-id-minimum-sdk.html)
- [기존 Loki 저장소 소유권 예시](../../terraform/environments/aws/dev/foundation/observability.tf)
- [백업 구현](../../terraform/environments/aws/dev/foundation/cnpg.tf), [수동 실행 runbook](../runbooks/cnpg-poc.md), [고정 버전·이미지와 호환성 근거](../../helm/versions.json)
