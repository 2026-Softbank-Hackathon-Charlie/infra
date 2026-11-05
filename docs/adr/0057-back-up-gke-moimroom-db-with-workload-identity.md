# 0057. GKE의 운영 moimroom DB는 Workload Identity로 GCS에 백업한다

- 날짜: 2026-11-06
- 상태: 제안
- 관련 ADR: [0031](0031-reproduce-cnpg-poc-on-gke.md), [0050](0050-let-migrations-own-app-database-topology.md), platform 저장소 ADR 0025(앱 Postgres 백업)

## 배경과 제약

2026-11-05 이전으로 moimroom의 운영 primary는 GKE `charlie-apps/moimroom-db-gke`이고, EKS `moimroom/moimroom-db`는 이 DB를 따르는 replica다. `moimroom-db-gke`는 이전 때 손으로 만든 CNPG Cluster라 앱 차트, 플랫폼, Argo CD가 관리하지 않는다. 그래서 platform ADR 0025의 백업(차트가 그리고 플랫폼이 키를 만드는 경로)이 닿지 않고, 지금 백업이 없다.

확인한 사실은 다음과 같다.

- GKE에는 CNPG 1.30.1과 Barman Cloud plugin v0.15.1이 있고, `charlie-db/postgres-poc`가 Workload Identity로 GCS에 매일 백업하고 있다(ADR 0031, 버킷에 11/04 base backup 두 개).
- 노드 풀은 `GKE_METADATA` 모드다. `moimroom-db-gke` 파드는 모든 egress를 허용하는 정책(`moimroom-db-gke-baseline`)을 받으므로 메타데이터 서버와 GCS에 닿는다.
- `charlie-apps`는 Pod Security `baseline`, quota(`limits.cpu` 4 중 2.5, `requests.cpu` 2 중 0.5 사용), LimitRange(기본 한도 500m/512Mi)가 있다. 노드 두 대 중 DB가 있는 노드는 CPU 요청이 1665m/1930m다.
- GCS의 S3 호환 API와 HMAC 키로 Barman을 쓰는 경로는 검증하지 않았다.
- GCP bootstrap 스택은 로컬 state라 다른 사람이 적용할 수 없고, CI의 GCP 계정은 버킷과 서비스 계정을 만들 권한이 없다.

## 검토한 대안

- 차트로 GKE에 다시 배포: 운영 primary를 새 Cluster로 옮기는 일이라 이번 범위가 아니다.
- HMAC 키와 S3 호환 API: 플랫폼 `gcs` 드라이버와 같은 경로지만 Barman으로 검증하지 않았다.
- PoC 백업 계정과 버킷에 KSA 하나만 추가: 자원 하나로 끝나지만 앱 백업과 PoC 백업이 서로의 객체를 지울 수 있다.
- 앱 DB 전용 버킷과 계정, Workload Identity(선택): PoC에서 검증한 방식이고 키가 없다.

## 결정과 이유

- 새 Terraform 스택 [terraform/integrations/gcp/app-db-backup](../../terraform/integrations/gcp/app-db-backup/)이 비공개 버킷 `charlie-414068132732-app-db-backup`(asia-northeast3, 수명 주기 규칙 없음, 삭제 방지), Google 서비스 계정 `charlie-app-db-backup`, 그 계정을 쓸 수 있는 KSA `charlie-apps/moimroom-db-gke` 하나, 버킷 한정 `objectAdmin`과 `legacyBucketReader`를 만든다. state는 기존 GCP state 버킷의 `gcp/dev/app-db-backup`이고 다른 integrations 스택처럼 로컬에서 적용한다.
- [clusters/gcp-dev-workload/moimroom-db-backup.yaml](../../clusters/gcp-dev-workload/moimroom-db-backup.yaml)의 ObjectStore와 ScheduledBackup을 적용하고, [moimroom-db-backup-patch.json](../../clusters/gcp-dev-workload/moimroom-db-backup-patch.json)으로 Cluster에 `serviceAccountTemplate` annotation, plugin, 4Gi 디스크를 넣는다. 앱 차트의 백업과 같은 값(gzip, 7일, 매일 03:00 KST, 켜자마자 한 번)이다.
- 경로는 `charlie-apps/<Cluster 생성 시각>/`이다. 같은 이름의 Cluster를 다시 만들면 다른 경로에 쓰므로 Barman의 빈 보관소 검사를 통과한다.
- plugin sidecar는 CPU 요청 10m, 메모리 64Mi로 작게 둔다. 노드 CPU가 거의 다 찼다.
- 디스크를 1Gi에서 4Gi로 늘린다. WAL 보관이 실패하면 WAL이 쌓이기 때문이다(platform ADR 0025). `standard-rwo`는 확장을 허용하고 파드를 다시 만들지 않는다.

## 영향과 후속 작업

- plugin을 넣으면 CNPG가 sidecar를 넣으려고 운영 primary 파드를 한 번 다시 만든다. 20~40초 동안 moimroom 쓰기가 실패한다. 사용자가 이 중단을 승인했다.
- 원본의 WAL 보관이 실패하면 이전의 원본 강등이 멈춘다(platform ADR 0025). 이전 전에 [DB 이전 runbook](../runbooks/aws-gcp-db-migration.md)의 백업 확인을 한다.
- 이 Cluster와 이 파일은 GitOps가 아니다. Cluster를 다시 만들면 운영자가 이 파일을 다시 적용하고 경로의 시각을 바꾼다.
- 플랫폼 `gcs` 드라이버로 GKE 앱을 백업하려면 HMAC 경로 검증이 먼저다. 그때 이 스택의 버킷과 계정은 정리하거나 그대로 둘지 정한다.
- 비용: 버킷 저장량(7일치 base backup과 WAL)과 요청 비용. GCS 기본 soft delete 7일 동안 지운 객체도 과금된다.

## 관련 자료

- [Barman Cloud plugin, GKE Workload Identity](https://cloudnative-pg.io/plugin-barman-cloud/docs/object_stores/)
- [terraform/bootstrap/gcp/cnpg-poc-backup.tf](../../terraform/bootstrap/gcp/cnpg-poc-backup.tf)(같은 권한 모양)
