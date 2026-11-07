# 0053. GKE moimroom 이전 대상은 새 standby `moimroom-db-gke`다

- 날짜: 2026-11-05
- 상태: 제안
- 2026-11-07 갱신: 손으로 배포했던 GKE moimroom을 지웠다. 지금 moimroom은 EKS(`moimroom/moimroom-db`)에서만 돈다. `moimroom-db-gke`와 `moimroom-gcp-db`도 지웠고 앱 목록에서 gke 환경을 뺐다.
- 관련 ADR: [0048](0048-connect-cloud-databases-with-scoped-migration-access.md), [0050](0050-let-migrations-own-app-database-topology.md)

## 배경과 제약

infra #68은 플랫폼 앱 목록(`terraform/platform/aws/host/apps.yaml`)의 gke moimroom DB를 실제로 있던 `charlie-apps/moimroom-gcp-db`로 맞췄다. 이 DB는 EKS DB와 상관없이 따로 초기화된 독립 CNPG Cluster다(자체 시드 데이터, database·owner `moimroom`).

이전 엔진은 CNPG 물리 복제를 쓴다. 대상은 원본에서 `pg_basebackup`으로 만든 standby여야 한다. 독립 DB는 시스템 식별자가 달라 원본을 따라갈 수 없다. 독립 DB를 대상으로 쓰려면 지우고 같은 이름으로 다시 만들어야 하고, 그러면 그 안의 데이터가 사라진다.

2026-11-05 운영 moimroom 이전은 사용자 결정에 따라 `charlie-apps/moimroom-db-gke`를 새 standby로 만들어 진행했다. 지금 moimroom 운영 primary가 이 DB이고, EKS `moimroom/moimroom-db`가 이 DB를 따라가는 replica다. `moimroom-gcp-db`는 건드리지 않았다.

## 검토한 대안

- `moimroom-gcp-db`를 지우고 같은 이름으로 standby 재생성: 앱 목록은 그대로 둘 수 있다. 그러나 독립 DB의 데이터가 사라지고, 되돌릴 수 없다
- 새 이름의 standby(`moimroom-db-gke`): 선택한다. 기존 DB를 보존하고 실제 이전 결과와 앱 목록이 일치한다. 독립 DB는 따로 정리를 결정해야 한다

## 결정과 이유

- 앱 목록의 gke moimroom `cnpgCluster`를 `moimroom-db-gke`로 바꾼다. namespace(`charlie-apps`)는 #68 그대로 둔다
- `dbHost`는 바꾸지 않는다. 플랫폼의 tailnet 경로(관리 클러스터 DB 네트워크)는 아직 설치되지 않았고, 실제 이름은 설치할 때 정한다

## 영향과 후속 작업

- 기대 효과: 플랫폼이 다음 이전(GCP → AWS 원복 포함)을 계획할 때 실제 primary를 대상으로 삼는다
- 단점과 위험: `moimroom-gcp-db`는 앱 목록에서 빠지지만 클러스터에 남아 비용을 쓴다. 이 DB를 지울지 사람이 정해야 한다
- 필요한 후속 작업 및 검증:
  1. `moimroom-gcp-db` 보존 또는 삭제 결정
  2. `dbHost`를 관리 클러스터 DB 네트워크 설치 때 실제 tailnet 이름으로 맞춘다
  3. GKE 앱 `moimroom-gcp`의 `DATABASE_URL` 변경(kubectl patch)을 배포 설정에 반영한다

## 관련 자료

- [terraform/platform/aws/host/apps.yaml](../../terraform/platform/aws/host/apps.yaml)
- 이전 기록: migration-engine `docs/test-records` 2026-11-05 운영 moimroom 실제 이전
