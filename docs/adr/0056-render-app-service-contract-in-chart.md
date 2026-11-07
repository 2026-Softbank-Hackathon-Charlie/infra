# 0056. 앱 차트가 워커 서비스, 서비스별 메모리와 CPU, DB 연결 변수를 그린다

- 날짜: 2026-11-05
- 상태: 제안
- 2026-11-07 갱신: 손으로 배포했던 GKE moimroom을 지웠다. 지금 moimroom은 EKS(`moimroom/moimroom-db`)에서만 돈다. GKE 사본 `charlie-apps/moimroom-gcp`와 그 DB는 지웠다.
- 관련 ADR: [0023](0023-deploy-github-apps-with-applicationset.md), [0050](0050-let-migrations-own-app-database-topology.md), platform ADR 0024(서비스 계약과 숫자의 근거)

## 배경과 제약

앱 차트(`helm/charts/hopcharlie-app`)는 플랫폼이 쓴 gitops 파일을 그린다. 2026-11-05 기준으로 세 가지가 막혀 있었다.

- 앱은 `DATABASE_URL` 하나만 받는다. 호스트, 사용자, 비밀번호를 따로 읽는 앱(Spring, Keycloak, Gitea)은 시작 스크립트에서 URL을 쪼개야 했다.
- 서비스마다 `port`가 필수이고, `healthCheck`가 없으면 TCP readiness probe를 단다. 포트를 열지 않는 큐 워커는 준비되지 않고, 배포가 300초 뒤 이유 없이 실패한다.
- 모든 컨테이너가 `profile.resources`(request cpu 50m, memory 64Mi, limit memory 256Mi)를 받는다. JVM 앱은 256Mi에서 뜨지 않는다. Postgres 파드도 LimitRange 기본값(limit 256Mi)을 받는다.

확인한 사실:

- 운영 `moimroom/moimroom-db-app` Secret의 키는 `dbname`, `host`, `jdbc-uri`, `password`, `port`, `uri`, `user`, `username`(그 밖에 `fqdn-*`, `pgpass`)이다(키 이름만 조회).
- `eks`, `gke` 프로필의 ResourceQuota는 requests.cpu 2, requests.memory 4Gi, limits.memory 8Gi, pods 20이고 LimitRange 기본값은 request cpu 50m, memory 64Mi, limit memory 256Mi다.
- Deployment는 `maxSurge: 1`, `maxUnavailable: 0`이다. 롤링 업데이트 동안 서비스마다 파드가 하나 더 뜬다.
- gitops 저장소에는 `apps/moimroom/eks.yaml`, `apps/docker-doom/eks.yaml`만 있다. GKE의 moimroom 사본(`charlie-apps/moimroom-gcp`, DB `charlie-apps/moimroom-gcp-db`)은 이 차트로 그리지 않는다. 그 DB 위치는 이전 엔진의 `terraform/platform/aws/host/apps.yaml`에만 쓰인다.
- 2026-11-05 23시 `moimroom/moimroom-db`의 `spec.replica.primary`는 `moimroom-db-gke`였다. EKS DB는 지금 이전 토폴로지의 복제본이다.

## 검토한 대안

- DB 변수를 프로필이나 환경마다 다른 Secret에서 읽기: 환경마다 연결 방법이 달라진다. 차트가 그리는 CNPG Cluster의 app Secret 하나에서 `DATABASE_URL`과 함께 읽으면 모든 환경에서 같다. 선택하지 않았다.
- 포트 없는 서비스에도 Service를 만들고 probe만 빼기: 가리킬 포트가 없는 Service는 쓸모가 없고 Endpoints만 비어 있다. 선택하지 않았다.
- 서비스의 `memory`와 `cpu` 범위, 합계를 schema로 검사: JSON schema는 quantity의 크기를 비교할 수 없다. 범위와 합계는 플랫폼(`pkg/appspec`)이 검사하고, schema는 quantity 형식만 본다. quota가 마지막 방어선이다.
- Postgres 파드를 LimitRange 기본값에 맡기기: limit 256Mi는 작고, 플랫폼의 합계 계산이 DB 몫을 알 수 없다. 프로필에 값을 정한다.

## 결정과 이유

- `postgres` 리소스가 있으면 `DATABASE_URL`과 같은 Secret `<app>-<resource>-app`에서 `PGHOST`(`host`), `PGPORT`(`port`), `PGDATABASE`(`dbname`), `PGUSER`(`username`), `PGPASSWORD`(`password`), `JDBC_DATABASE_URL`(`jdbc-uri`)을 넣는다. 플랫폼 변수는 서비스 `env`보다 앞에 두어 `$(PGHOST)` 같은 확장이 된다. `AWS_*`는 넣지 않는다.
- `port`가 없는 서비스는 Service, `containerPort`, probe, `PORT`를 그리지 않는다. 실행되면 준비된 것으로 본다. 대신 Deployment에 `minReadySeconds: 10`을 두어 10초 동안 재시작 없이 돌아야 available로 센다. probe가 없으면 시작하자마자 죽는 워커도 잠깐 Ready가 되어 롤링 업데이트가 옛 파드를 내리고 끝날 수 있기 때문이다. schema는 `port` 없는 `public: true`와 `healthCheck`를 거부한다.
- 서비스의 `memory`는 memory request와 limit, `cpu`는 CPU request가 된다. 없으면 지금처럼 `profile.resources`다.
- `eks`, `gke` 프로필의 `postgres.resources`를 request memory 256Mi, limit memory 512Mi로 정한다. CPU request는 LimitRange 기본값 50m이다. 플랫폼의 합계(서비스 몫 memory 3Gi, CPU 1500m, 롤링 업데이트 포함)는 quota에서 이 DB 몫과 여유를 뺀 값이다(platform ADR 0024).
- ADR 0050의 `ignoreDifferences`는 Cluster의 `spec.replica`, `spec.externalClusters`, `spec.certificates`만 무시한다. `spec.resources`는 그대로 GitOps가 갖는다. `RespectIgnoreDifferences=true`라서 resources를 바꾸는 sync가 이전 엔진이 쓴 세 필드를 덮지 않는다.

## 영향과 후속 작업

- 기대 효과: JVM 앱과 큐 워커를 시작 스크립트 없이 올릴 수 있다. 플랫폼이 통과시킨 배포는 롤링 업데이트 중에도 namespace quota를 넘지 않는다.
- 단점과 위험:
  - merge하면 Argo CD가 모든 앱 Application을 다시 그린다. 2026-11-05 렌더링 비교에서 docker-doom은 바뀌는 것이 없고, moimroom은 `moimroom-web`에 DB 변수 6개가 더해지며 `moimroom-db` Cluster에 resources가 생긴다.
  - `moimroom-web`은 파드 템플릿이 바뀌어 한 번 롤링 재시작한다(새 파드가 준비된 뒤 옛 파드가 내려감).
  - CNPG는 resources가 바뀐 `moimroom-db-1`을 한 번 다시 시작한다. 단일 인스턴스라 그동안 EKS DB 연결이 끊긴다. 지금 EKS DB는 이전 토폴로지의 복제본이므로 이전 담당자와 맞춘 시각에 merge한다.
  - `RespectIgnoreDifferences=true`로 sync하면 Argo CD는 무시하는 세 필드(`spec.replica`, `spec.externalClusters`, `spec.certificates`)의 live 값을 읽어 desired에 넣고 apply한다. 그래서 이전 뒤 첫 apply가 그 값을 `kubectl.kubernetes.io/last-applied-configuration`에 남긴다. 또 Argo CD가 값을 읽은 뒤 이전 엔진이 `spec.replica`를 patch하면, 이어지는 apply가 읽어 둔 옛 값을 다시 쓸 수 있다. 이 변경의 sync를 포함해, Cluster를 바꾸는 sync는 이전 단계가 돌고 있지 않을 때만 한다.
  - 차트가 플랫폼보다 먼저 바뀌므로 그 사이에 `platform.yaml`의 `env`에 `PGHOST` 같은 이름을 쓴 앱은 schema 거부로 sync가 실패한다. 지금 운영 앱(moimroom, docker-doom)은 그런 이름을 쓰지 않는다.
- 필요한 후속 작업 및 검증:
  1. 이 변경을 먼저 merge하고 platform의 서비스 계약 PR을 merge한다. 반대 순서이면 플랫폼이 쓴 `memory`, `cpu`, `port` 없는 서비스를 지금 차트의 schema가 거부한다.
  2. merge 뒤 `moimroom-eks`, `docker-doom-eks`가 Synced, Healthy인지, `moimroom-db-1`이 Ready이고 `spec.replica`가 그대로인지 확인한다.
  3. `hopcharlie-apps.yaml`의 quota, LimitRange, `postgres.resources`를 바꾸면 platform `pkg/appspec`의 합계(ADR 0024)도 다시 계산한다.

## 관련 자료

- [helm/charts/hopcharlie-app/templates/_helpers.tpl](../../helm/charts/hopcharlie-app/templates/_helpers.tpl), [deployment.yaml](../../helm/charts/hopcharlie-app/templates/deployment.yaml), [service.yaml](../../helm/charts/hopcharlie-app/templates/service.yaml), [values.schema.json](../../helm/charts/hopcharlie-app/values.schema.json)
- [clusters/aws-dev-management/values/hopcharlie-apps.yaml](../../clusters/aws-dev-management/values/hopcharlie-apps.yaml)
- [scripts/check-helm.py](../../scripts/check-helm.py)
- Kubernetes: [Dependent environment variables](https://kubernetes.io/docs/tasks/inject-data-application/define-interdependent-environment-variables/)
- CloudNativePG: [Connecting from an application](https://cloudnative-pg.io/documentation/current/applications/)
