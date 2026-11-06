# 0063. 두 환경에 등록한 앱의 DB는 처음부터 CNPG 분산 토폴로지의 짝으로 만든다

- 날짜: 2026-11-06
- 상태: 제안 (차트, 프로필, 권한 매니페스트 준비. db-network 업그레이드, 권한 적용, 프로필 켜기는 별도 승인 대상)
- 관련 ADR: [0048](0048-connect-cloud-databases-with-scoped-migration-access.md), [0050](0050-let-migrations-own-app-database-topology.md), [0052](0052-allow-db-network-dns-through-node-local-cache.md), [0058](0058-share-one-app-entry-per-workload-cluster.md), [0060](0060-allow-live-moimroom-db-in-protected-namespace-for-migration-access.md). platform 쪽 설계는 platform 저장소의 앱 DB 토폴로지 ADR(초안)이 다룬다.

## 배경과 제약

확인된 사실:

- 이전 엔진의 precheck는 CNPG 분산 토폴로지(`spec.replica.self`, `primary`, `source`)를 요구한다. 원본은 `primary == self`인 primary이고 대상은 원본을 따라가는 replica여야 한다. primary로 태어난 DB는 나중에 replica로 바꿀 수 없다(system id가 다르다).
- 앱 차트 `hopcharlie-app`의 CNPG Cluster는 `bootstrap`, `replica`, `externalClusters`, `certificates`를 쓰지 않는다. 두 환경에 등록한 앱(`moimroom-mig`)은 환경마다 독립 primary가 생겼다.
- 운영 moimroom의 짝은 상대 클러스터의 CNPG CA와 `-replication` Secret을 손으로 복사해 만들었다. 경로는 수동 Helm 릴리스 `hopcharlie-db-network`의 DB 하나(`localDatabase`)뿐이다.
- `hopcharlie-db-network` 네임스페이스에는 `db-network-default-deny`가 있어 새 프록시 파드는 허용 정책 없이는 통신하지 못한다. Tailscale operator는 다른 네임스페이스의 Service도 처리하고, 프록시 파드는 operator 네임스페이스에 `tailscale.com/parent-resource=<Service>`, `parent-resource-ns=<네임스페이스>` 레이블로 생긴다(2026-11-06 EKS 조회).
- operator는 egress Service의 `spec.externalName`을 자기 프록시 주소로 바꾼다.
- 플랫폼 계정 `charlie-system/hopcharlie-platform`의 상시 CNPG 권한은 `list`뿐이다. 이전 직전에 `scripts/db-migration-access.py`(ADR 0048, 0060)가 Cluster 하나의 `get`, `patch`를 손으로 준다.
- 앱 차트는 모든 앱 네임스페이스에 `hopcharlie.dev/app`을 단다. EKS 운영 `moimroom` 네임스페이스에도 이 레이블이 있다(2026-11-06 `kubectl get ns --show-labels`). GKE `charlie-apps`는 `clusters/gcp-dev-workload/namespaces.yaml`의 baseline 네임스페이스이고 앱 차트가 만들지 않는다.

가정과 미확인:

- GKE 네임스페이스 레이블은 이번에 조회하지 못했다(터널이 닫혀 있었다). `charlie-apps`에 `hopcharlie.dev/db-topology`가 없다는 것은 매니페스트로만 확인했다.
- ValidatingAdmissionPolicy의 CEL은 cel-go v0.26.1로 같은 식을 맵 값에 평가해 확인했다. 실제 API 서버에서는 아직 시험하지 않았다.
- CNPG가 사용자 CA(`ca.key` 포함)로 서버 인증서와 `streaming_replica` 인증서를 만들고 갱신한다는 것은 CNPG 소스와 문서로 확인했고 live 시험은 아직이다.

## 검토한 대안

- 앱 차트에 앱마다 Role과 RoleBinding을 그린다: 범위가 Cluster 하나로 가장 좁다. 그러나 AppProject에 RBAC 객체를 허용해야 하고, gitops 파일을 쓰는 쪽이 권한 객체를 그리게 된다. 리뷰에서 제외했다.
- `hopcharlie-platform-access.yaml`에 모든 Cluster `get`, `patch`만 준다: 한 줄이지만 운영 `moimroom`, `charlie-apps`의 DB까지 patch할 수 있다. ADR 0060의 보호와 맞지 않는다.
- ClusterRole에 `get`, `patch`를 주고 ValidatingAdmissionPolicy로 patch를 좁힌다: 새 앱마다 손으로 권한을 줄 필요가 없고 운영 DB는 patch할 수 없다. `get`은 admission을 거치지 않아 좁힐 수 없다. 선택한다.
- 프록시 Service를 앱마다 `hopcharlie-db-network`에 둔다: AppProject가 그 네임스페이스를 막고, 앱마다 수동 릴리스를 고쳐야 한다. 제외한다.
- 앱 네임스페이스에 Service를 두고 프록시 파드를 ProxyClass 레이블로 공용 정책에 묶는다: 클러스터마다 한 번만 손으로 적용한다. 선택한다.

## 결정과 이유

### 앱 차트와 gitops 값

플랫폼이 gitops 파일에 다음을 쓰면 차트가 Cluster를 토폴로지의 반쪽으로 그린다. 없으면 출력은 지금과 바이트 단위로 같다.

```yaml
values:
  dbTopology:
    primary: gke   # 태어날 때 primary인 환경
    peer: eks      # 파일의 env에서 본 다른 환경
  sealedSecrets:
    pgApp: {username, password, dbname, host, port, uri, jdbc-uri}   # 봉인값
    pgCA: {ca.crt, ca.key}                                           # 봉인값
```

- `other`는 파일의 env가 아닌 쪽이다(`env == primary`면 `peer`, 아니면 `primary`). 차트는 `peer`가 `other` 또는 env이고 `other`가 env와 다를 때만 그린다.
- primary 쪽은 `bootstrap.initdb`, 다른 쪽은 `bootstrap.pg_basebackup`(source `<cluster>-<primary>`)로 태어난다. 둘 다 앱 계정 Secret `<cluster>-app`(봉인, `kubernetes.io/basic-auth`)을 쓰므로 CNPG가 app Secret을 만들지 않고 서비스의 DB 변수는 그대로다.
- `certificates.serverCASecret`과 `clientCASecret`은 둘 다 `app-pg-ca`다. 두 DB가 같은 CA를 쓰므로 각자의 `-replication` 인증서가 상대에서도 통과하고, 클러스터 사이에 Secret을 복사하지 않는다. `serverAltDNSNames`는 상대가 부르는 `<cluster>-to-<env>.<ns>.svc[.cluster.local]`이다.
- `replica`는 `self: <cluster>-<env>`, `primary: <cluster>-<primary>`, `source: <cluster>-<other>`이다. `externalClusters`는 자기(`<cluster>-rw`)와 상대(`<cluster>-to-<other>.<ns>.svc.cluster.local`)이고 둘 다 `sslmode: verify-full`이다.
- 디스크는 두 환경에서 같아야 한다. 프로필의 `postgres.topologyStorageSize`(eks, gke 모두 `4Gi`)를 쓴다.
- 네임스페이스에 `hopcharlie.dev/db-topology: "true"`를 단다. 권한과 공용 프록시 정책이 이 레이블을 본다.
- 프로필 `dbNetwork.enabled`가 켜지면 Tailscale ingress Service `<cluster>-ts`(primary 선택, `hop-db-<env>-<instance>`), egress Service `<cluster>-to-<other>`(`hop-db-<other>-<instance>.<tailnetDomain>`로), 복제용 NetworkPolicy `<cluster>-replication`을 그린다. NetworkPolicy의 상대는 operator 네임스페이스(`proxyNamespace`)의 해당 프록시 파드다. 이 정책은 `profile.networkPolicy.enabled`일 때만 그린다. 혼자 있으면 DB 파드를 격리하기 때문이다.
- 차트는 Role이나 RoleBinding을 그리지 않고 AppProject도 바꾸지 않는다.

### ApplicationSet

`ignoreDifferences`에 `type: ExternalName`이고 `tailscale.com/tailnet-fqdn` 어노테이션이 있는 Service의 `.spec.externalName`만 더한다. 기존 CNPG 필드 무시(ADR 0050)는 그대로다. 앱 Service는 ExternalName이 아니므로 영향이 없다.

### 환경 프로필

`clusters/aws-dev-management/values/hopcharlie-apps.yaml`의 eks, gke에 `dbNetwork`(tailnet `tail567fe8.ts.net`, 기존 태그 `tag:hop-db-<env>`, `tag:hop-db-client-<env>`, ProxyClass `hopcharlie-app-db-{ingress,egress}-<env>`, `hopcharlie-db-network`)와 `topologyStorageSize`를 더한다. `dbNetwork.enabled`는 `false`로 병합하고, 아래 db-network 업그레이드 뒤 별도 PR로 켠다.

### 워크로드의 공용 프록시 객체

`hopcharlie-db-network` 차트에 `platformApps.enabled`(기본 false)를 둔다. 켜면 ProxyClass 2개(파드 레이블 `hopcharlie.dev/db-proxy: ingress|egress`)와 NetworkPolicy `platform-app-db-egress-proxy`, `platform-app-db-ingress-proxy`를 만든다. DNS와 443은 `db-network.controlEgress`(GKE NodeLocal DNS 포함, ADR 0052)를 쓴다. 앱 쪽 상대는 `hopcharlie.dev/db-topology: "true"` 네임스페이스의 CNPG 파드다. 기존 `localDatabase` 객체와 `management` 프로필은 바꾸지 않는다. 관리 클러스터에서는 렌더링을 실패시킨다.

### 플랫폼 계정의 이전 권한

워크로드마다 `hopcharlie-platform-access.yaml`에 다음을 더한다.

- ClusterRole `hopcharlie-platform-migration`: `postgresql.cnpg.io` `clusters`의 `get`, `patch`. ClusterRoleBinding으로 `charlie-system/hopcharlie-platform`에 묶는다.
- ValidatingAdmissionPolicy `hopcharlie-platform-migration`(Binding은 `Deny`): 이 계정의 Cluster UPDATE는 네임스페이스에 `hopcharlie.dev/db-topology=true`가 있을 때만, `spec`에서는 `replica`만, `replica`에서는 `primary`와 `promotionToken`만 바뀔 때, 레이블과 어노테이션이 그대로일 때만 통과한다. 엔진의 patch(`SetPrimary`, merge patch로 이 두 필드)만 들어간다.
- `get`은 VAP가 막을 수 없어 클러스터 전체다. Cluster spec에는 비밀값이 없고 이 계정은 이미 모든 Cluster를 `list`할 수 있다.

ADR 0048, 0060과의 관계: 운영 moimroom(EKS `moimroom`, GKE `charlie-apps`)은 레이블이 없으므로 이 권한으로 patch할 수 없다. 운영 DB 이전은 지금처럼 `scripts/db-migration-access.py`의 임시 Role로 한다. 이 ADR은 새로 등록한 두 환경 앱에만 상시 권한을 준다. 범위는 레이블이 붙은 네임스페이스의 `spec.replica.primary`와 `promotionToken`이다.

### 남는 수동 작업

관리 EKS의 플랫폼 API에서 앱 DB로 가는 경로(management 프로필의 `egress.peers`)는 #99 영역이고 이 ADR은 손대지 않는다. 앱마다 관리 프로필에 `eks-<instance>`, `gke-<instance>` 두 항목(`hostname: hop-db-management-to-<env>-<instance>`, `tailnetFQDN: hop-db-<env>-<instance>.tail567fe8.ts.net`)을 더하고 관리 릴리스를 업그레이드하는 작업이 남는다. 이것이 없으면 복제와 토폴로지는 완성되지만 이전은 precheck의 원본 DB 접속에서 멈춘다.

## 영향과 후속 작업

- 기대 효과: 새 두 환경 앱은 등록할 때부터 이전 가능한 짝으로 태어나고, 앱마다 Secret 복사, 정책 추가, 권한 부여를 하지 않는다.
- 단점과 위험:
  - CA 키가 두 클러스터의 앱 네임스페이스 Secret에 있다. 한쪽 Secret을 읽을 수 있으면 그 앱의 복제 인증서를 만들 수 있다. 범위는 앱 하나다.
  - 공용 egress 프록시 정책은 레이블이 붙은 다른 앱의 CNPG 파드도 허용한다. 상대 DB에 들어가려면 그 앱의 CA로 서명한 인증서나 비밀번호가 필요하다.
  - VAP가 spec 전체를 비교하므로 CNPG 기본값 웹훅이 UPDATE 때 다른 필드를 바꾸면 엔진의 patch가 거부된다. 저장된 객체는 이미 기본값을 거쳤으므로 보통은 같다. 이전 중 승격 patch가 거부되면 두 DB가 모두 replica로 남는다. live 시험에서 확인한다.
  - 두 Application이 같은 커밋으로 sync되면 replica의 pg_basebackup이 primary와 프록시보다 먼저 시도해 실패할 수 있다. CNPG Job 재시도 한도는 live에서 잰다.
  - Tailscale operator가 멈추면 Service 파이널라이저 때문에 앱 삭제가 끝나지 않는다.
- 필요한 후속 작업 및 검증:
  1. 이 PR 병합(CI가 기존 앱 렌더가 같은지, 토폴로지 렌더와 거부 경우를 검사한다).
  2. 두 워크로드에 `platformApps.enabled=true`로 db-network 업그레이드, ProxyClass `ProxyClassValid` 확인, 접근 매니페스트 적용(PR 본문의 명령).
  3. 프로필 `dbNetwork.enabled: true` PR.
  4. platform의 `PLATFORM_APP_DB_TOPOLOGY`를 켜고 moimroom-mig를 다시 등록해 `pg_is_in_recovery`, streaming, `kubectl auth can-i`, VAP 거부를 확인한다.
  5. 관리 프로필의 앱별 peer 두 항목(#99 이후).

## 관련 자료

- [helm/charts/hopcharlie-app/templates/postgres.yaml](../../helm/charts/hopcharlie-app/templates/postgres.yaml)
- [helm/charts/hopcharlie-app/templates/dbnetwork.yaml](../../helm/charts/hopcharlie-app/templates/dbnetwork.yaml)
- [helm/charts/hopcharlie-app/templates/_helpers.tpl](../../helm/charts/hopcharlie-app/templates/_helpers.tpl)
- [helm/charts/hopcharlie-apps/templates/applicationset.yaml](../../helm/charts/hopcharlie-apps/templates/applicationset.yaml)
- [helm/charts/hopcharlie-db-network/templates/platform-apps.yaml](../../helm/charts/hopcharlie-db-network/templates/platform-apps.yaml)
- [clusters/aws-dev-workload/hopcharlie-platform-access.yaml](../../clusters/aws-dev-workload/hopcharlie-platform-access.yaml)
- [clusters/gcp-dev-workload/hopcharlie-platform-access.yaml](../../clusters/gcp-dev-workload/hopcharlie-platform-access.yaml)
- [clusters/aws-dev-management/values/hopcharlie-apps.yaml](../../clusters/aws-dev-management/values/hopcharlie-apps.yaml)
- migration-engine `pkg/cnpg/cnpg.go` `SetPrimary`, `pkg/engine/steps.go` precheck
