# 0074. 한 환경에 등록한 앱 DB도 짝의 primary로 태어나고, 상대가 생길 때 tailnet 객체를 더한다

- 날짜: 2026-11-07
- 상태: 제안 (차트 분기와 검사 준비. 플랫폼이 `peerPresent`를 쓰는 변경은 platform 저장소에서 따로 한다)
- 관련 ADR: [0063](0063-born-paired-app-databases-for-two-environment-apps.md), [0064](0064-platform-creates-management-app-db-routes.md), [0050](0050-let-migrations-own-app-database-topology.md). platform 쪽 설계는 platform ADR 0030이 다룬다.

## 배경과 제약

확인된 사실:

- ADR 0063의 짝은 등록할 때 두 환경을 함께 고른 앱에만 생긴다. 한 환경으로 등록한 postgres 앱은 토폴로지 없는 독립 primary로 태어나고, primary로 태어난 DB는 나중에 분산 토폴로지의 primary로 바꾸려면 살아 있는 Cluster를 patch하고 CNPG가 만든 CA와 계정을 넘겨받아야 한다.
- 차트가 그리는 Cluster의 `replica`, `externalClusters`, `certificates`는 ApplicationSet이 생성 뒤 무시한다(ADR 0050, 0063). 그래서 처음부터 같은 값으로 그려 두면 나중에 바꿀 일이 없다.
- 상대 환경이 아직 없을 때 `dbnetwork.yaml`이 그리는 세 객체(ingress Service `<cluster>-ts`, egress Service `<cluster>-to-<peer>`, NetworkPolicy `<cluster>-replication`)는 쓸 곳이 없다. ingress와 egress Service는 각각 Tailscale 프록시 파드 1개와 tailnet 장치 1개를 만든다. egress는 존재하지 않는 tailnet 호스트 `hop-db-<peer>-<instance>`를 가리킨다.
- 차트 안에서 egress Service와 tailnet 호스트를 부르는 곳은 Cluster의 `externalClusters[<cluster>-<peer>].connectionParameters.host`와 `replica.source`뿐이다. 네임스페이스 NetworkPolicy `hopcharlie-app`, 백업(ObjectStore, ScheduledBackup), 봉인 Secret은 상대를 참조하지 않는다. ApplicationSet의 `externalName` 무시 설정은 ExternalName Service가 있을 때만 적용된다. 공용 프록시 정책(`hopcharlie-db-network`의 `platform-apps.yaml`)과 플랫폼 계정 VAP는 네임스페이스 레이블 `hopcharlie.dev/db-topology`만 본다.

가정과 미확인:

- `replica.self == replica.primary`인 Cluster는 primary로 돌고 `source`의 externalCluster에 접속하지 않는다고 본다. egress Service가 없는 상태에서 primary가 healthy한지는 실제 클러스터에서 확인하지 않았다.
- 관리 EKS의 앱 DB 경로(ADR 0064, `db-<env>-<instance>`)는 ingress 호스트 `hop-db-<env>-<instance>`를 가리킨다. `peerPresent: false` 동안 이 호스트가 없으므로 그 경로로는 접속되지 않는다. 플랫폼이 이 경로를 만드는 시점은 platform 쪽에서 정한다.

## 검토한 대안

- 한 환경 앱은 지금처럼 독립 primary로 두고, 환경을 더할 때 살아 있는 Cluster를 patch해 짝으로 만든다: 이미 있는 앱에도 쓸 수 있지만 CNPG가 만든 CA와 앱 비밀번호를 플랫폼이 읽어야 하고, CA를 다시 서명해야 하며, Secret 읽기 권한이 필요하다. 새로 등록하는 앱에는 과하다. 새 앱에는 쓰지 않는다.
- 상대가 없어도 `dbnetwork.yaml`을 그대로 그린다: 차트 변경이 없지만 앱마다 쓸모없는 프록시 파드 2개와 tailnet 장치 2개가 생기고, egress 프록시는 없는 호스트를 계속 찾는다. 제외한다.
- 상대가 없을 때 Cluster에서 `replica`와 `externalClusters`를 빼고 그린다: 상대를 더할 때 이 필드가 생겨야 하는데 ApplicationSet이 무시하므로 다시 플랫폼 patch가 필요하다. 제외한다.
- Cluster와 Secret은 짝일 때와 똑같이 그리고 tailnet 객체만 뺀다: 상대를 더할 때 primary 파일을 다시 그리면 새 객체 세 개만 생기고 Cluster는 바뀌지 않는다. 선택한다.

## 결정과 이유

플랫폼은 한 환경에 등록한 postgres 앱의 파일에 ADR 0063의 값과 함께 `peerPresent: false`를 쓴다.

```yaml
values:
  dbTopology:
    primary: eks        # 등록한 환경
    peer: gke           # 나중에 더할 환경. replica로 태어난다
    peerPresent: false  # 상대 환경이 아직 없다. 없으면 true
  sealedSecrets:
    pgApp: {...}        # ADR 0063과 같다
    pgCA: {...}
```

- `peerPresent`가 없거나 `true`면 출력은 이 변경 전과 바이트 단위로 같다. 운영 중인 `moimroom-mig`의 두 파일도 그대로다.
- `peerPresent: false`는 `env == primary`인 파일에만 쓸 수 있다. 다른 파일에 쓰면 렌더링이 실패한다. schema는 boolean만 받는다.
- `peerPresent: false`일 때 Cluster(`bootstrap.initdb`와 봉인 `<cluster>-app`, `certificates`의 `app-pg-ca`와 `serverAltDNSNames`, `replica`, `externalClusters`, `topologyStorageSize`, `smartShutdownTimeout: 1`, `wal_retrieve_retry_interval`), 봉인 Secret `<cluster>-app`과 `app-pg-ca`, 네임스페이스 레이블 `hopcharlie.dev/db-topology: "true"`는 `true`일 때와 같다.
- `dbnetwork.yaml`의 세 객체만 그리지 않는다. `profile.dbNetwork` 필수값 검사와 Service 이름, 호스트 이름 63자 검사는 그대로 한다. 상대를 더할 때 쓸 이름이므로 처음부터 걸러 낸다.
- 상대 환경을 더하면 플랫폼은 primary 파일의 `peerPresent`를 빼거나 `true`로 바꾸고, 새 환경 파일을 ADR 0063의 replica 쪽으로 쓴다. primary 쪽에서는 세 객체만 새로 생기고 Cluster는 바뀌지 않는다.

## 영향과 후속 작업

- 기대 효과: 한 환경으로 등록한 앱도 환경을 더한 뒤 버튼으로 이전할 수 있다. 살아 있는 Cluster를 patch하거나 Secret을 읽을 일이 없다.
- 단점과 위험
  - 한 환경 앱의 디스크가 `storageSize` 대신 `topologyStorageSize`로 태어난다. GKE에서는 1Gi가 아니라 4Gi다.
  - 앱 계정 비밀번호와 CA를 처음부터 플랫폼이 만들어 봉인한다(ADR 0063과 같다).
  - `peerPresent: false`인 동안 primary의 `externalClusters`와 `replica.source`는 없는 Service를 가리킨다. 위 가정이 틀리면 primary가 경고를 내거나 상태가 바뀔 수 있다.
  - 관리 EKS 경로(ADR 0064)는 상대가 생기기 전에는 접속되지 않는다.
- 후속 작업과 검증
  1. platform: 한 환경 postgres 앱의 gitops 파일에 `dbTopology`, `pgApp`, `pgCA`, `peerPresent: false`를 쓰고, 환경을 더할 때 이 값을 지운다(platform ADR 0030).
  2. 실제 워크로드 클러스터에서 `peerPresent: false`인 앱의 Cluster가 healthy한지, 상대를 더한 뒤 replica가 따라오는지 확인한다.

## 관련 자료

- [helm/charts/hopcharlie-app/templates/_helpers.tpl](../../helm/charts/hopcharlie-app/templates/_helpers.tpl)
- [helm/charts/hopcharlie-app/templates/dbnetwork.yaml](../../helm/charts/hopcharlie-app/templates/dbnetwork.yaml)
- [helm/charts/hopcharlie-app/values.schema.json](../../helm/charts/hopcharlie-app/values.schema.json)
- [scripts/check-helm.py](../../scripts/check-helm.py)
