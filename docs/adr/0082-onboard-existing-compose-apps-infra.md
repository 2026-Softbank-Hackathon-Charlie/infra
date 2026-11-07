# 0082. compose로 돌던 앱 가져오기의 차트, Argo, 승인 정책, 플랫폼 권한을 둔다

- 날짜: 2026-11-07
- 상태: 제안
- 관련 ADR: [0036](0036-platform-api-without-cloudflare-token.md), [0045](0045-hopcharlie-apps-applicationset.md), [0048](0048-platform-migrates-app-databases.md), [0050](0050-let-migrations-own-app-database-topology.md), [0056](0056-render-app-service-contract-in-chart.md), [0063](0063-born-paired-app-databases-for-two-environment-apps.md), [0064](0064-platform-creates-management-app-db-routes.md), [0074](0074-born-paired-app-database-before-its-peer-exists.md), [0078](0078-manage-three-member-db-paths-with-gitops.md), platform ADR 0042(추가 절 포함)

## 배경과 제약

platform ADR 0042는 플랫폼 밖 VM 한 대에서 docker compose로 돌던 앱을 조사하고, 계약 PR을 만들고, DB를 물리 복제로 따라가게 한 뒤 무중단으로 가져온다. platform 쪽 계약(`internal/deploy/gitops.go`의 `PostgresValues` 주석, `internal/onboard`)은 머지되었고, infra는 아래를 맡는다(ADR 0042 "한 번 하는 설정").

- 앱 차트는 지금 `imageName`을 그리지 않고, DB 이름과 owner를 `app`으로 고정하고, `replicas: 0`을 `default 1`로 1로 바꾼다. DB 토폴로지는 플랫폼 안 두 환경 사이의 CNPG만 그린다.
- ApplicationSet은 `selfHeal: true`, `RespectIgnoreDifferences=true`이고 Cluster의 `replica`, `externalClusters`(세 멤버 제외), `certificates`만 무시한다(ADR 0050, 0078). Deployment는 무시하지 않는다.
- 워크로드 admission policy는 플랫폼이 Cluster의 `spec.replica.primary`와 `promotionToken`만 바꾸게 한다(세 멤버 primary의 `source`는 예외, ADR 0078).
- 관리 클러스터 route policy는 이름 `^db-(eks|gke|onprem)-`만 받는다(ADR 0064, 0072).
- 플랫폼 역할 `hopcharlie-platform-pod`에는 EC2 조회, SSM 명령, 인스턴스 중지 권한이 없고, GCP SA에는 compute 조회 권한이 없다.
- hopai Job 템플릿은 `hopai run`만 실행하고, 플랫폼은 그 템플릿을 복사해 `APP`, `FROM`, `TO`만 채운다(platform `internal/airun`).

2026-11-07 kind(CNPG 1.30.1)에서 `replica.primary: legacy`, `bootstrap.pg_basebackup.source: legacy`, 자기 자신과 짝을 포함한 `externalClusters`로 외부 PostgreSQL을 따라가고 승격하는 것을 확인했다(platform ADR 0042 상황). 차트가 그린 결과로 같은 시나리오를 다시 돌리는 것은 아직 하지 않았다.

## 검토한 대안

- 온보딩 전용 차트나 수동 manifest: 차트 하나로 두면 전환 뒤 같은 Application이 그대로 일반 앱이 된다. 별도 차트는 전환 때 Application을 바꿔야 해서 제외했다.
- Deployment 전체를 `ignoreDifferences`로 무시하거나 `managedFieldsManagers`로 엔진의 필드를 무시: 전체 무시는 이미지 배포까지 막는다. `managedFieldsManagers`는 엔진의 field manager 이름에 묶이고, 엔진이 merge patch로 바꾸면 manager가 실행 파일 이름이라 고정되지 않는다. 라벨로 고르는 `jqPathExpressions`는 이 ApplicationSet이 이미 Cluster와 Service에 쓰는 방식이라 그것을 택했다.
- SSM 문서의 스크립트를 `{{ param }}` 치환으로 받기: 매개변수가 명령 문자열에 들어가 주입이 생기고, 스크립트 안의 `{{`(docker `--format`)도 치환된다. 매개변수는 `interpolationType: ENV_VAR`로 `SSM_<이름>` 환경 변수로만 받고, 스크립트는 base64로 실어 SSM이 치환하지 않게 했다.
- GCP `roles/compute.viewer`: 디스크, 이미지, 네트워크 설정까지 읽는다. 인스턴스와 주소 조회만 하는 custom role로 했다.

## 결정과 이유

### 앱 차트 (`helm/charts/hopcharlie-app`)

gitops 값의 모양은 platform `internal/deploy/gitops.go`의 `PostgresValues` 주석을 그대로 따른다. 값이 없으면 기존 출력과 바이트 단위로 같다(`scripts/check-helm.py` 전체 렌더를 origin/main 차트와 비교해 확인).

- `values.postgres.parameters`는 Cluster `spec.postgresql.parameters`에 합친다. schema는 `max_connections`, `max_wal_senders`, `max_worker_processes`, `max_prepared_transactions`, `max_locks_per_transaction`만, 값은 숫자 문자열만 받는다. 차트가 두는 짝 DB의 `wal_retrieve_retry_interval`은 그대로다. 플랫폼은 온보딩한 앱의 replica가 원본보다 작지 않도록 `max_connections`, `max_wal_senders`, `max_worker_processes`, `max_prepared_transactions`, `max_locks_per_transaction`을 여기에 쓴다.
- `values.postgres.image`는 Cluster `imageName`, `database`와 `owner`는 bootstrap의 값(없으면 `app`), `storageSize`는 PVC 크기로 `profile.postgres.topologyStorageSize`와 `storageSize`보다 먼저 쓴다. 토폴로지가 없는 Cluster는 `database`나 `owner`가 있을 때만 `bootstrap.initdb`를 그린다.
- `values.dbTopology.external`은 primary 파일에만 쓸 수 있고(세 멤버와 함께 쓸 수 없다), `sealedSecrets.pgLegacy`와 `profile.dbNetwork.enabled`가 필요하다. 그러면 Cluster는 다음과 같다.
  - `bootstrap.pg_basebackup {source: legacy, database, owner, secret: <cluster>-app}`
  - `replica {self: <cluster>-<env>, primary: legacy, source: legacy}`
  - `externalClusters`는 `legacy`(호스트 `<cluster>-to-legacy.<ns>.svc.cluster.local`, `user`, `dbname: postgres`, `sslmode`, 비밀번호 Secret `<cluster>-legacy`의 `password`), 자기 자신, 짝(`<cluster>-<peer>`, 일반 짝과 같은 `verify-full`과 플랫폼 CA) 순서다.
  - `certificates`는 ADR 0063과 같다.
  - egress Service `<cluster>-to-legacy`(tailnet FQDN `<host>.<tailnetDomain>`, hostname `hop-db-<env>-<instance>-to-legacy`, 환경의 egress 태그와 ProxyClass)와, `profile.networkPolicy.enabled`일 때 CNPG Pod에서 그 프록시의 5432로만 나가는 NetworkPolicy `<cluster>-to-legacy`를 그린다.
  - `passwordSecret`은 `<cluster>-legacy`여야 하고, `name`은 `legacy`, `host`는 `hop-db-legacy-`로 시작해야 한다(schema).
- `peerPresent: false`이면 짝으로 가는 tailnet 객체는 그리지 않는다(ADR 0074). 짝의 `externalClusters` 항목은 처음부터 그린다. ApplicationSet이 그 필드를 나중에 다시 적용하지 않기 때문이다.
- `sealedSecrets.pgLegacy`가 있으면 `external`과 상관없이 SealedSecret `<cluster>-legacy`(`kubernetes.io/basic-auth`)를 그린다. 전환 뒤에도 live Cluster의 `externalClusters`에 `legacy`가 남기 때문이다.
- `values.onboardingCold: true`이면 모든 Deployment가 `replicas: 0`이고 Deployment 메타데이터에 `hopcharlie.dev/onboarding-cold: "true"`가 붙는다. Pod 템플릿과 selector에는 붙이지 않는다. 라벨을 뺄 때 Pod가 다시 만들어지지 않게 하려는 것이다.
- `replicas`는 값이 있으면 0도 그대로 쓴다(`hasKey`). schema의 최솟값을 0으로 낮췄다.

### ApplicationSet (`helm/charts/hopcharlie-apps`)

- Cluster의 `jqPathExpressions`에 `.spec.bootstrap`을 더한다. 외부 원본에서 태어난 Cluster는 전환 뒤 차트가 `initdb`를 그리므로, 무시하지 않으면 OutOfSync가 되고 selfHeal이 바꿀 수 없는 필드를 apply한다. 모든 Cluster의 bootstrap은 만든 뒤 의미가 없으므로 전체에 건다.
- Deployment에 `select(.metadata.labels["hopcharlie.dev/onboarding-cold"] == "true") | .spec.replicas`를 더한다. Argo CD는 각 식을 `del(<식>)`으로 gojq에 넘겨 live와 desired 양쪽을 정규화한다. 라벨이 없는 Deployment에서는 select가 경로를 내지 않아 아무것도 지우지 않는다. 이 ApplicationSet은 같은 방식(select로 고른 `jqPathExpressions`)을 Service와 세 멤버 Cluster에 이미 쓰고 있고, `RespectIgnoreDifferences=true`가 sync할 때 무시한 필드를 live 값으로 둔다(ADR 0050, 0056). 그래서 엔진이 cold 모드로 늘린 replicas를 selfHeal이 0으로 되돌리지 않는다. 처음 만들 때는 live가 없으므로 0으로 만들어진다.
- 전환 뒤 플랫폼이 라벨을 빼고 원래 replicas를 쓰면, 첫 sync는 아직 라벨이 있는 live 기준으로 replicas를 live 값으로 둔 채 라벨만 지운다. 그다음 비교에서 replicas 차이가 보여 selfHeal이 원래 값을 적용한다. 엔진이 띄우지 않은 서비스는 이 두 번째 sync에서 뜬다.

### 승인 정책

- 워크로드 `hopcharlie-platform-migration`(aws, gcp, onprem의 `hopcharlie-platform-access.yaml`): `replica.source`가 `legacy`인 동안 `primary`는 자기 이름으로만 바꿀 수 있다(승격만, 짝으로 강등하지 않는다). 또 `replica.source`가 `legacy`이고 이미 승격된(`primary == self`) Cluster에서, `source`만 `<metadata.name>-`로 시작하고 `externalClusters`에 있으며 `legacy`도 자기 자신도 아닌 이름으로 바꾸는 것을 더 허용한다. 다른 필드를 같이 바꾸면 거부한다.
- 관리 `management-app-routes.yaml`: 이름 `^db-legacy-`, 라벨 `hopcharlie.dev/db-route: legacy`, FQDN `hop-db-legacy-<app>.<tailnet>`(기존과 같이 `hop-` + 이름), hostname `hop-db-management-to-legacy-`를 받는다. 포트는 기존처럼 TCP 5432 하나다.

두 정책은 cel-go로 식을 평가해 허용과 거부 경우를 확인했다(저장소 밖 임시 하네스, 결과는 PR 본문).

### 플랫폼 AWS 권한과 SSM 문서 (`terraform/platform/aws/onboarding.tf`)

managed policy `hopcharlie-platform-onboarding`을 플랫폼 역할에 붙인다. inline 정책 합계 한도(10,240자)를 피하려는 것이다.

| 문 | 내용 |
| --- | --- |
| `Discover` | `ec2:DescribeInstances`, `DescribeAddresses`, `DescribeNetworkInterfaces`, `ssm:DescribeInstanceInformation`(SSM 관리 여부), `ssm:GetCommandInvocation`, 리소스 `*` |
| `OnlyTheHopCharlieDocuments` | `ssm:SendCommand`, 두 리전의 문서 `HopCharlie-Inspect`, `HopCharlie-Connect`, `HopCharlie-Disconnect`만 |
| `CommandsOnInstances` | `ssm:SendCommand`, 두 리전의 이 계정 인스턴스 |
| `OnlyTheOnboardingTag` | `ec2:CreateTags`, `ec2:DeleteTags`, 태그 키가 `hopcharlie-onboarding` 하나일 때만 |
| `StopTaggedInstances` | `ec2:StopInstances`, 온보딩 태그가 있는 인스턴스만 |
| `Never*` (Deny) | `eks:cluster-name`이나 `aws:autoscaling:groupName` 태그가 있는 인스턴스(`Null` 조건), 그리고 이 계정 스택의 인스턴스(`Component` 태그가 `access`(SSM bridge), `app-private-network`(Tailscale subnet router), `eks`)에 `CreateTags`, `DeleteTags`, `StopInstances`, `SendCommand` |

인스턴스 ARN은 두 리전(`region`, `onboarding_second_region`)으로만 쓴다.

- SendCommand는 문서와 인스턴스를 따로 인가하고, IAM은 둘을 묶지 못한다. Inspect가 모든 인스턴스를 써야 하므로 Connect와 Disconnect도 태그 없는 인스턴스에 닿을 수 있다. ADR 0042의 "Connect는 태그가 있을 때만" 문은 효과가 없어 두지 않았다. 플랫폼은 자기가 태그를 붙인 인스턴스에만 Connect와 Disconnect를 쓰고, IAM의 경계는 거부 문과 고정 문서다.
- `DeleteTags`(DELETE가 붙인 태그를 되돌린다)와 `DescribeInstanceInformation`(조사의 SSM 관리 여부)은 ADR 0042 권한 표에 없던 것이다.
- 문서 세 개는 `aws_ssm_document`(Command, schema 2.2, Linux)이고, 계정이 쓸 수 있는 두 리전(`region`, `onboarding_second_region` 기본 `ap-southeast-2`)에 같은 내용으로 둔다. 원본 VM이 어느 리전에 있어도 SendCommand가 그 리전의 문서를 쓰기 때문이다.
- 스크립트는 `terraform/platform/aws/onboarding/{inspect,connect,disconnect}.sh`(infra #146)이다. 문서는 스크립트를 base64로 싣는다. SSM이 스크립트 안의 `{{`를 치환하지 않게 하려는 것이다. 첫 단계가 그 명령의 디렉터리(단계 디렉터리의 부모, SSM이 명령 id마다 `/var/lib/amazon/ssm/.../orchestration/<command id>/` 아래에 만든다)에 풀고, 마지막 단계가 지운다. 같은 VM에서 두 명령이 겹쳐도 파일을 같이 쓰지 않는다. 그 디렉터리가 `/var/lib/amazon/ssm/` 아래가 아니면 단계가 실패한다(이 경로 배치는 SSM Agent 동작으로 본 가정이고 실제 VM에서 확인하지 않았다).
- 매개변수는 모두 String이고 `interpolationType: ENV_VAR`로 `SSM_<이름>` 환경 변수로만 받는다. 각 단계가 그 값을 스크립트의 `HC_*` 변수로 넘긴다. `allowedPattern`은 스크립트의 검사와 같다.

| 문서 | 매개변수 | 단계 |
| --- | --- | --- |
| Inspect | `publicKey`(일회용 RSA 공개키의 DER base64, 200~1200자), `maxChunk`(1000~24000, 기본 20000) | `inspect`(`HC_MODE=run`), `chunk1`~`chunk9`(`HC_MODE=chunk HC_CHUNK=<i>`), `cleanup`(`HC_MODE=clean`). 모두 `HC_PUBLIC_KEY`, `HC_MAX_CHUNK`, `HC_MAX_CHUNKS=10` |
| Connect | `dbContainer`, `network`, `subnet`, `tailscaleAuthKey`(`tskey-auth-`), `hostname`(`hop-db-legacy-<app>`), `publicKey`, `appLabel` | `connect`. `HC_DB_CONTAINER`, `HC_NETWORK`, `HC_SUBNET`, `HC_TS_AUTHKEY`, `HC_HOSTNAME`, `HC_PUBLIC_KEY`, `HC_APP` |
| Disconnect | `dbContainer`, `appLabel` | `disconnect`. `HC_DB_CONTAINER`, `HC_APP` |

- Inspect의 `inspect` 단계는 `onFailure: exit`라서 조사가 실패하면 chunk 단계를 돌지 않고, `cleanup`은 `finallyStep: true`라서 항상 돈다. Connect와 Disconnect는 단계가 하나이고 0이 아닌 종료 코드는 모두 문서 실패다(Connect 8은 설정 대기, Disconnect 4는 DB가 멈춤). 플랫폼은 `GetCommandInvocation`의 `ResponseCode`로 구별한다.

### 플랫폼 GCP 권한 (`terraform/integrations/gcp/platform-storage/onboarding.tf`)

custom role `charliePlatformOnboardingDiscovery`(`compute.instances.list`, `compute.instances.get`, `compute.addresses.list`)를 플랫폼 SA에 프로젝트 단위로 준다. `instances.aggregatedList`는 `compute.instances.list`로 인가된다.

### 플랫폼 설정과 hopai Job (`helm/charts/hopcharlie-platform`)

- `fetch-settings.sh`는 SSM `/hopcharlie/tailscale-onboard-oauth`(SecureString JSON `{clientId, clientSecret}`)가 있으면 `api.env`에 `PLATFORM_TAILSCALE_OAUTH_CLIENT_ID`와 `PLATFORM_TAILSCALE_OAUTH_CLIENT_SECRET`을 더한다. aws-cli 이미지에는 JSON 도구가 없으므로, 영문자, 숫자, `-`, `_`만 받는 sed 식으로 읽고 맞지 않으면 메시지를 남기고 넣지 않는다. 없으면 이전과 같다.
- `api.onboarding.enabled`(기본 false)가 켜지면 API에 `PLATFORM_ONBOARDING=1`을, `api.onboarding.awsRegions`가 있으면 `PLATFORM_ONBOARD_AWS_REGIONS`를 준다(비어 있으면 플랫폼 기본값 `ap-northeast-2,ap-southeast-2`).
- hopai Job 템플릿은 `KIND=onboard`이면 `hopai onboard`를 실행한다. 볼륨 `onboarding`(ConfigMap, 기본 이름 `hopai-onboarding`, optional)을 `/onboarding`에 읽기 전용으로 붙인다. 템플릿은 Job마다 다른 ConfigMap 이름을 쓸 수 없으므로 플랫폼이 다음을 한다(platform `internal/airun` 변경).
  1. 컨테이너 `hopai`에 `KIND=onboard`, `ONBOARDING`, `REPO`, `BRANCH`, `FACTS_FILE=/onboarding/facts.json`, 재시도 때 `CHECK_LOG_FILE=/onboarding/check.log`를 넣는다.
  2. 볼륨 `onboarding`의 `configMap.name`을 `hopai-<온보딩 id>`(예: `hopai-onb-3f9a1c2b7d4e`)로, `optional`을 false로 바꾼다.
  3. Job을 만든 뒤 그 ConfigMap을 Job을 owner로 해서 만든다. Pod는 ConfigMap이 생길 때까지 기다리고, Job이 지워지면 함께 지워진다.
- hopai Job은 플랫폼 ServiceAccount가 아니라 전용 `hopcharlie-hopai`로 돈다. 플랫폼 역할(SSM 명령, 인스턴스 중지, 버킷, IAM 사용자)을 모델이 움직이는 컨테이너가 갖지 않게 하려는 것이다. 그 Pod Identity 역할 `hopcharlie-hopai-pod`(`terraform/platform/aws/eks.tf`)는 `/hopcharlie/hopai/*`와 `/hopcharlie/platform-tokens`의 `ssm:GetParameter`와 SSM 경유 `kms:Decrypt`만 갖는다. ServiceAccount 이름은 템플릿에만 있고 platform `internal/airun`은 그대로 복사하므로 platform 변경은 없다. AI runner 정책은 이제 이 ServiceAccount만 받는다(`platform.aiRunner.serviceAccount`).
- RBAC `hopcharlie-ai-runner`에 Job `delete`와 ConfigMap `create`, `delete`를 더한다. RBAC는 이름을 미리 알 수 없으므로 `hopcharlie-apps`의 AI runner 정책이 좁힌다. Job의 ConfigMap 볼륨은 `hopcharlie-hopai-fetch`, `hopai-onboarding`, `^hopai-onb-[0-9a-f]{12}$`만 된다. 새 정책 `hopcharlie-platform-ai-runner-cleanup`은 플랫폼 namespace에서 `^hopai-onb-[0-9a-f]{12}$` ConfigMap의 생성과 삭제, 라벨 `hopcharlie.dev/ai-run=true`인 Job의 삭제만 받는다.
- 같은 Role에 Pod `get`, `list`와 `pods/log` `get`을 더한다. 플랫폼이 hopai onboard의 결과를 Pod 로그로 읽기 때문이다(platform #139). RBAC는 runner의 Pod로 좁힐 수 없어 플랫폼 namespace의 모든 Pod 명세와 로그를 읽을 수 있다. 플랫폼 설정 값은 `/config`(메모리 볼륨)에 있고 Pod 명세나 로그에는 없으므로 받아들인다.

## 영향과 후속 작업

- 기대 효과: 온보딩 값이 없는 앱의 출력과 권한은 그대로이고, 플랫폼 구현이 ADR 0042의 2번부터 5번까지를 이 차트와 권한으로 진행할 수 있다.
- 단점과 위험:
  - 차트가 그린 Cluster로 외부 원본 복제와 승격을 실제로 다시 돌리지 않았다. 첫 온보딩 전에 kind에서 렌더 결과로 다시 확인한다.
  - 전환 뒤 차트는 `initdb`를 그린다. Cluster가 지워지고 다시 만들어지면(네임스페이스나 Application 재생성) 원본이 아니라 빈 DB로 태어나므로, 그때는 백업(ADR 0025)에서 되살린다.
  - Cluster `bootstrap` 무시는 모든 앱에 걸린다. bootstrap을 고쳐도 기존 Cluster에는 적용되지 않는데, CNPG도 만든 뒤에는 쓰지 않으므로 바뀌는 동작은 없다.
  - Connect와 Disconnect를 태그가 있는 인스턴스로만 묶는 것은 IAM으로 강제되지 않는다(위).
  - `platform-tokens`에는 agent 말고 다른 역할의 토큰도 있다. hopai 역할이 그 파라미터 전체를 읽는다. agent 토큰만 따로 두는 것은 후속 작업이다.
  - `interpolationType: ENV_VAR`는 최근 SSM Agent에서만 동작한다. 오래된 agent에서는 환경 변수가 비고, 스크립트의 입력 검사가 실패한다.
  - 이 PR은 infra #146의 스크립트 파일을 읽으므로 #146이 먼저 머지되어야 한다.
  - Tailscale auth key는 SSM 명령 기록에 남는다(ADR 0042가 받아들인 위험, 일회용 1시간).
- 필요한 후속 작업 및 검증(머지 뒤, 사람이 승인하고 실행):
  1. `terraform/platform/aws`: `./tf.sh plan`으로 두 리전의 문서 세 개씩, managed policy와 attachment, hopai 역할과 Pod Identity association만 더해지는지 보고 apply한다. hopai Job의 ServiceAccount 변경(차트)은 이 apply 뒤에 반영돼야 Job의 fetch가 실패하지 않는다. 스크립트가 바뀌면 다시 apply해 문서 버전을 올린다.
  2. `terraform/integrations/gcp/platform-storage`: plan으로 custom role과 binding만 더해지는지 보고 apply한다.
  3. 워크로드 `hopcharlie-platform-access.yaml` 세 개를 ADR 0063의 방식(`scripts/put-platform-kubeconfig.py`, onprem은 수동)으로 다시 적용한다.
  4. 관리 `app-db-routes` 릴리스를 ADR 0064의 runbook대로 업그레이드한다.
  5. ApplicationSet, 앱 차트, 플랫폼 차트는 Argo CD가 main에서 반영한다. 플랫폼 이미지가 온보딩을 구현하기 전에는 `api.onboarding.enabled`를 켜지 않는다.
  6. tailnet 관리자가 `tag:hop-legacy`와 grant를 더하고 OAuth 클라이언트를 SSM `/hopcharlie/tailscale-onboard-oauth`에 넣는다(ADR 0042).

## 관련 자료

- [helm/charts/hopcharlie-app/templates/postgres.yaml](../../helm/charts/hopcharlie-app/templates/postgres.yaml), [dbnetwork.yaml](../../helm/charts/hopcharlie-app/templates/dbnetwork.yaml), [deployment.yaml](../../helm/charts/hopcharlie-app/templates/deployment.yaml), [sealedsecrets.yaml](../../helm/charts/hopcharlie-app/templates/sealedsecrets.yaml)
- [helm/charts/hopcharlie-apps/templates/applicationset.yaml](../../helm/charts/hopcharlie-apps/templates/applicationset.yaml), [ai-runner-policy.yaml](../../helm/charts/hopcharlie-apps/templates/ai-runner-policy.yaml)
- [helm/charts/hopcharlie-db-network/templates/management-app-routes.yaml](../../helm/charts/hopcharlie-db-network/templates/management-app-routes.yaml)
- [terraform/platform/aws/onboarding.tf](../../terraform/platform/aws/onboarding.tf), [terraform/integrations/gcp/platform-storage/onboarding.tf](../../terraform/integrations/gcp/platform-storage/onboarding.tf)
- [helm/charts/hopcharlie-platform/templates/hopai.yaml](../../helm/charts/hopcharlie-platform/templates/hopai.yaml), [settings.yaml](../../helm/charts/hopcharlie-platform/templates/settings.yaml)
- platform `docs/adr/0042-onboard-existing-compose-apps.md`, `internal/deploy/gitops.go`, `internal/onboard/onboard.go`, `internal/airun/airun.go`
- Argo CD [Diffing customization](https://argo-cd.readthedocs.io/en/stable/user-guide/diffing/), AWS [SSM document parameters (interpolationType)](https://docs.aws.amazon.com/systems-manager/latest/userguide/documents-syntax-data-elements-parameters.html)
