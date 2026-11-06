# 0064. 관리 EKS의 앱 DB 경로를 플랫폼이 만들고 앱 파일은 플랫폼 신원으로 옮긴다

- 날짜: 2026-11-06
- 상태: 제안
- 관련 ADR: [0063](0063-born-paired-app-databases-for-two-environment-apps.md), [0048](0048-connect-cloud-databases-with-scoped-migration-access.md), [0035](0035-app-storage-and-secrets-infra.md), [0041](0041-keyless-gcp-access-for-platform-app-storage.md)

## 배경과 제약

- 관리 EKS의 API Pod는 앱 DB에 `db-<env>-<instance>.hopcharlie-db-network.svc.cluster.local`로 접속한다(ADR 0063, `PLATFORM_APP_DB_HOST_TEMPLATES`). 이 주소는 Tailscale egress Service다. 지금은 수동 Helm 릴리스 `db-network`의 정적 `egress.peers`(moimroom의 `db-eks`, `db-gke`)뿐이라 새 앱마다 사람이 값을 고치고 릴리스를 올려야 한다.
- 그 릴리스는 운영 중이다. 이번 변경으로 그 릴리스의 ProxyClass, Service, NetworkPolicy를 바꾸지 않는다.
- 등록 앱의 파일 이전은 꺼져 있다. 플랫폼 Pod 신원에는 앱 버킷 객체를 읽고 쓰는 권한이 없다.

## 검토한 대안

- 관리 ApplicationSet이 gitops에서 앱마다 egress Service를 그린다: Git이 원하는 상태가 되지만 새 ApplicationSet, AppProject가 필요하고 앱 AppProject가 이 namespace를 거부한다. operator가 바꾸는 `externalName` 무시 설정과 동기화 지연도 더해진다. 제외.
- `db-network` 릴리스에 앱별 peer를 계속 더한다: 앱마다 수동 작업이 남는다. 제외.
- 플랫폼이 앱 키를 평문으로 보관해 파일을 옮긴다: 새 비밀 저장소가 생기고 플랫폼 ADR 0030과 어긋난다. 제외.

## 결정과 이유

1. 플랫폼 API(SA `charlie-platform/hopcharlie-platform`)가 앱마다 `hopcharlie-db-network`에 ExternalName Service를 만들고 앱 삭제 때 지운다(플랫폼 `internal/dbroute`). Service 명세는 이름 `db-<env>-<instance>`, 라벨 `app.kubernetes.io/managed-by: hopcharlie-platform`, `hopcharlie.dev/app`, `hopcharlie.dev/db-route`, annotation `tailscale.com/tailnet-fqdn: hop-db-<env>-<instance>.tail567fe8.ts.net`, `tailscale.com/hostname: hop-db-management-to-<env>-<instance>`, `tailscale.com/tags: tag:hop-db-client-management`, `tailscale.com/proxy-class: hopcharlie-app-db-egress-management`, `externalName: placeholder`, 포트 5432 하나다.
2. 그에 필요한 관리 쪽 객체는 같은 차트(`hopcharlie-db-network`)의 `managementAppRoutes` 분기로 그리고, **별도 수동 Helm 릴리스 `app-db-routes`**로 설치한다. 이 릴리스는 `enabled: false`라 정적 프록시 객체를 그리지 않고, 두 값을 함께 켜면 렌더가 실패한다. 객체는 다음과 같다.
   - ProxyClass `hopcharlie-app-db-egress-management`: 프록시 Pod에 `hopcharlie.dev/db-proxy: egress` 라벨
   - NetworkPolicy `platform-app-db-egress-proxy`: API Pod에서 TCP 5432, UDP 41641 수신, 정적 `db-<peer>-proxy`와 같은 egress
   - Role, RoleBinding: services `get, list, watch, create, delete`. update와 patch는 주지 않는다(operator가 `externalName`과 finalizer를 바꾸므로 플랫폼은 지우고 다시 만든다).
   - ValidatingAdmissionPolicy 두 개: CREATE는 `object`만 보고 위 명세(라벨, 고정 tag와 proxy class, ExternalName placeholder, 5432, 이름과 같은 tailnet FQDN, 다른 `tailscale.com/` annotation 없음)를 강제한다. DELETE는 `oldObject`의 managed-by 라벨만 본다. 그래서 정적 `db-eks`, `db-gke`와 operator의 headless Service는 지울 수 없다.
3. 플랫폼 차트는 `api.appDbTopology.route.{tailnet,proxyClass,tag}`를 `PLATFORM_APP_DB_ROUTE_*`로, `api.appFiles.enabled`를 `PLATFORM_APP_FILES=1`로 넘긴다. 둘 다 기본 꺼짐이고 운영 값(`clusters/aws-dev-management/values/hopcharlie-platform.yaml`)은 이번에 바꾸지 않는다.
4. 파일 이전은 플랫폼 Pod 신원으로 한다.
   - AWS 플랫폼 Pod 역할: `hopcharlie-eks-*`에 `s3:ListBucket`, 객체에 `s3:GetObject`, `s3:PutObject`(기존 `s3:ResourceAccount` 조건). 백업 버킷 `hopcharlie-eks-*---pg/*`에는 `GetObject`, `PutObject`, `DeleteObject`, `DeleteObjectVersion`을 명시적으로 Deny한다. Allow에 `NotResource`를 쓰면 다른 버킷까지 열리므로 쓰지 않는다.
   - GCP 플랫폼 SA 역할 `charliePlatformAppBuckets`: `storage.objects.get`, `create`, `update`를 더한다. 조건 `resource.name.startsWith("projects/_/buckets/hopcharlie-gke-")`는 그대로라 객체도 앱 버킷 안으로 제한된다.

tag는 operator만 붙일 수 있고(tagOwners) tailnet grant는 DB tag의 TCP 5432만 연다. VAP는 다른 장치를 가리키거나 정적 Service를 지우는 실수를 막는 이중 방어다.

## 영향과 후속 작업

- 기대 효과: 새 앱을 등록하면 관리 경로와 파일 이전에 사람 손이 들지 않는다.
- 단점과 위험
  - 등록 앱마다 관리 EKS에 프록시 Pod 1개와 tailnet 장치 1개가 환경 수만큼 생긴다(요청 50m, 128Mi).
  - 플랫폼 SA가 이 namespace의 Service를 만들고 지울 수 있다. VAP가 내용을 좁히지만, VAP가 빠진 상태(릴리스 일부 적용)에서는 RBAC만 남는다.
  - 플랫폼 신원이 앱 버킷 객체를 읽고 쓸 수 있다. 이미 비우고 지울 수 있었으므로 범위는 크게 늘지 않는다.
  - VAP 식은 kind(Kubernetes 1.34)에서 C1 Service 허용과 위반 거부를 확인했다. 관리 EKS(1.35)에서는 적용 뒤 dry-run으로 다시 확인한다.
- 후속 작업과 검증(별도 적용 승인)
  1. `terraform/platform/aws`(tf.sh)와 `terraform/integrations/gcp/platform-storage` apply. 이 두 루트는 main 병합 때 자동 apply되지 않는다.
  2. 관리 EKS에 `app-db-routes` 릴리스 설치([runbook](../runbooks/aws-gcp-db-migration.md) 7절).
  3. 플랫폼 운영 값에 route와 `appFiles.enabled`를 켜는 별도 PR.

## 관련 자료

- [helm/charts/hopcharlie-db-network/templates/management-app-routes.yaml](../../helm/charts/hopcharlie-db-network/templates/management-app-routes.yaml)
- [helm/db-network/management-app-routes.yaml](../../helm/db-network/management-app-routes.yaml)
- [helm/charts/hopcharlie-platform/templates/api.yaml](../../helm/charts/hopcharlie-platform/templates/api.yaml)
- [terraform/platform/aws/app-storage.tf](../../terraform/platform/aws/app-storage.tf)
- [terraform/integrations/gcp/platform-storage/main.tf](../../terraform/integrations/gcp/platform-storage/main.tf)
- [scripts/tests/test-db-network.py](../../scripts/tests/test-db-network.py)
