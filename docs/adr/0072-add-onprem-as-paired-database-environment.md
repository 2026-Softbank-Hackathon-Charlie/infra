# 0072. 노트북 k3s(onprem)를 DB 짝 환경으로 추가

- 날짜: 2026-11-07
- 상태: 제안
- 관련 ADR: [0063](0063-born-paired-app-databases-for-two-environment-apps.md), [0064](0064-platform-creates-management-app-db-routes.md), [0048](0048-connect-cloud-databases-with-scoped-migration-access.md)

## 배경과 제약

플랫폼 이전 엔진으로 앱 DB를 GKE에서 노트북 WSL2 k3s(onprem)로 옮기려 한다. 두 환경 앱(ADR 0063)의 DB 경로는 앱 차트(`values.dbTopology`)와 플랫폼 코드(`internal/dbroute`, `internal/apps/registry.go`)가 환경 이름으로 만든다. 이 코드는 환경 이름을 일반화해 두었지만 infra의 db-network 차트 스키마(`clusterId`), 관리 EKS의 경로 VAP(`db-(eks|gke)-…`), 관리 플랫폼 값(`hostTemplates`), 앱 프로필(`environments`)에는 eks/gke만 있다.

onprem에는 정적 moimroom DB가 없다. 기존 워크로드 프로필은 `localDatabase`, 정적 ingress Service, 정확히 한 개의 정적 peer를 필수로 요구한다.

미확인: 노트북 k3s에서 Tailscale proxy Pod의 동작, tailnet 정책에서 onprem 태그 허용 여부.

## 검토한 대안

- 대안 A: onprem에 가짜 `localDatabase`·정적 peer를 넣어 기존 워크로드 프로필을 그대로 쓴다. 차트 변경은 없지만 존재하지 않는 장치(`hop-db-gke-moimroom`)를 가리키는 proxy가 생긴다. 제외.
- 대안 B: `clusterId: onprem`을 platformApps 전용 프로필로 둔다. 정적 경로 객체는 그리지 않고, 앱 DB proxy용 공유 ProxyClass와 NetworkPolicy만 그린다. 선택.

## 결정과 이유

- `hopcharlie-db-network` 스키마에 `onprem`을 추가한다. onprem은 `platformApps.enabled: true`와 `ingress.tag`/`egress.tag`가 필수다. `localDatabase`, `ingress.hostname`, 정적 peer는 둘 수 없다. 정적 ProxyClass, 정적 Service와 그 정책은 그리지 않는다. 운영 프로필은 [onprem.yaml](../../helm/db-network/onprem.yaml)이다(`enabled: false`).
- 관리 EKS 경로 VAP가 `db-onprem-<instance>`를 허용한다(`hop-db-onprem-…`, `hop-db-management-to-onprem-…`).
- 관리 플랫폼 `hostTemplates`에 `onprem=db-onprem-{instance}.hopcharlie-db-network.svc.cluster.local`을 더한다.
- 앱 프로필 `environments.onprem`을 추가한다. Argo 클러스터 `charlie-dev-onprem-workload`, `local-path`, 백업 끔, ingress 끔, ClusterIP, 태그 `tag:hop-db-onprem`/`tag:hop-db-client-onprem`을 쓴다.
- 플랫폼 코드는 바꾸지 않는다. 앱 차트도 바꾸지 않는다(환경 이름을 이미 일반화함).

## 영향과 후속 작업

- 기대 효과: gke+onprem 두 환경 앱이 기존 born-paired 경로로 DB 짝을 만든다.
- 단점과 위험: onprem의 앱 파일 버킷은 eks와 같은 접두사를 쓴다(envs.yaml의 기존 결정). onprem `gatewayFrom`은 비어 있어 게이트웨이 트래픽 경로는 아직 없다.
- 후속 작업(사용자 승인 필요): (1) onprem `operator-oauth` Secret과 tailscale-operator 설치, (2) `onprem.yaml`로 db-network 설치, (3) tailnet ACL에 onprem 태그와 5432 grant 추가, (4) 관리 EKS `app-db-routes` 릴리스 upgrade. VAP는 플랫폼 값이 반영되기 전에 갱신한다.

## 관련 자료

- [helm/charts/hopcharlie-db-network](../../helm/charts/hopcharlie-db-network)
- [helm/db-network/onprem.yaml](../../helm/db-network/onprem.yaml)
- [clusters/aws-dev-management/values/hopcharlie-platform.yaml](../../clusters/aws-dev-management/values/hopcharlie-platform.yaml)
- [clusters/aws-dev-management/values/hopcharlie-apps.yaml](../../clusters/aws-dev-management/values/hopcharlie-apps.yaml)
- [docs/runbooks/aws-gcp-db-migration.md](../runbooks/aws-gcp-db-migration.md)
