# 0078. 세 멤버 DB의 연결 목록은 GitOps가 관리하고 이전 엔진은 primary의 source만 준비한다

- 날짜: 2026-11-07
- 상태: 제안
- 관련 ADR: [0050](0050-let-migrations-own-app-database-topology.md), [0063](0063-born-paired-app-databases-for-two-environment-apps.md), [0074](0074-born-paired-app-database-before-its-peer-exists.md). platform ADR 0044가 등록·봉인·배포 계약을 다룬다.

## 배경과 제약

앱 DB는 기존에 `primary`, `peer`의 두 환경을 전제한다. `moimroom-mig`를 EKS→GKE로 전환한 뒤 GKE에서 복제하는 onprem을 추가하려면 기존 두 환경의 Cluster에도 세 번째 `externalClusters` 항목과 egress Service가 필요하다.

ApplicationSet은 현재 모든 Cluster의 `replica`, `externalClusters`, `certificates`를 무시한다. 세 번째 경로를 Git에 써도 `RespectIgnoreDifferences=true`가 살아 있는 Cluster에 반영하지 않는다. 반대로 `replica` 무시까지 제거하면 selfHeal이 이전 엔진의 승격·강등을 되돌려 두 primary를 만들 수 있다.

플랫폼 서비스 계정의 VAP는 `replica.primary`, `promotionToken` 변경만 허용한다. 세 멤버의 현재 primary가 최초 peer 이외의 대상을 강등 후 따라가려면, 강등 전에 `replica.source`를 그 대상으로 준비해야 한다. 이 변경은 현재 primary인 동안 복제 대상 자체를 바꾸지 않지만, replica에서의 임의 source 변경은 별도 위험이다.

## 검토한 대안

- 세 필드 전체를 계속 무시하고 운영자가 경로를 patch: 등록의 GitOps 계약이 완성되지 않고 누락을 자동 복구할 수 없다.
- 세 필드 전체를 GitOps가 관리: 승격·강등을 되돌리는 기존 split-brain 위험이 생긴다.
- 세 멤버만 연결 목록을 GitOps가 관리하고, source 준비 patch는 현재 primary에서만 허용: 선택한다. 두 환경의 기존 수동 준비를 유지하며 세 번째 멤버의 경로를 선언할 수 있다.

## 결정과 이유

- app 차트에 선택적 `values.dbTopology.members`를 추가한다. 2~3개 고유 환경이며 `primary`, 서로 다른 `peer`, 현재 `env`를 포함해야 한다. `peerPresent: false`와는 함께 사용할 수 없다. `members`가 없으면 기존 출력의 의미를 유지한다.
- 모든 nonprimary는 선언된 primary에서 `pg_basebackup`하고 `replica.source`도 primary를 가리킨다. primary의 초기 source는 기존 peer다. 각 Cluster는 자신과 다른 모든 멤버의 externalCluster, 다른 멤버마다 egress Service와 복제 NetworkPolicy 규칙을 갖는다.
- 명시한 멤버 수를 Namespace와 Cluster의 `hopcharlie.dev/db-topology-members` 레이블에 기록한다. ApplicationSet은 이 값이 `"3"`인 Cluster의 `externalClusters`를 더 이상 무시하지 않는다. `replica`, `certificates` 무시와 `RespectIgnoreDifferences=true`는 유지한다. 이 부분은 ADR 0050의 전체 연결 목록 무시를 대체한다.
- AWS·GCP 워크로드 VAP의 기존 primary/promotionToken 분기는 유지한다. 별도 분기로 기존 Cluster의 멤버 레이블이 `"3"`이고 `replica.primary == replica.self`일 때만 source 변경을 허용한다. 변경 대상 source는 기존 `externalClusters`에 있으며 self와 달라야 한다. 이 분기는 source 외의 replica 필드 변경·삭제, primary/self/token의 동시 변경을 허용하지 않는다. 다른 spec·metadata 변경 제한도 유지한다.
- 엔진은 source 변경에 UID/resourceVersion/self/primary 조건부 patch를 사용한다. 해당 엔진 구현은 별도 저장소의 변경이며 이 infra 변경만으로 구현되거나 배포되었다고 보지 않는다.

## 영향과 후속 작업

- 기대 효과: 기존 두 환경의 이전 중 소유권을 유지하면서 onprem 복제 경로를 선언하고, source 준비에 필요한 최소 변경만 허용한다.
- 단점과 위험: 세 멤버에는 환경마다 ingress 한 개와 egress 두 개가 필요하다. 실제 프록시·ACL·CNPG·인증서 검증 없이 연결 성공이나 무손실 이전을 주장할 수 없다. Git이 연결 목록을 소유하므로 세 멤버 Cluster의 수동 externalCluster 변경은 복구될 수 있다.
- 적용 전 검증: chart/schema의 세 환경 렌더, 기존 두 환경·단일 환경 대기 출력 비교, 잘못된 멤버 거부, ApplicationSet 렌더, VAP YAML·동일 정책 확인을 수행한다. 로컬 렌더는 Kubernetes API의 CEL 타입 검사나 실제 admission 동작을 증명하지 않는다.
- 운영 적용은 별도 승인 범위다. 먼저 ApplicationSet과 접근 정책을 준비하고 EKS→GKE 전환을 검증한다. onprem 추가 후 모든 기존 Cluster의 멤버 레이블과 `externalClusters` 세 항목, 각 egress Service를 확인해야 한다. 레이블 변경과 ignore 조건 전환의 첫 sync에서 목록이 남으면 다음 sync를 확인하며, 경로가 준비되기 전에 이전을 시작하지 않는다.
- k3s의 플랫폼 접근 RBAC/VAP, Tailscale ACL, DB Service·MinIO·이미지 pull·AI 경로는 별도 인프라 준비다. 이 저장소에는 현재 onprem 워크로드 접근 manifest가 없다. 임의 spoke 간 자동 rewire는 지원 범위가 아니며 대상이 현재 primary를 따르지 않으면 엔진이 거부해야 한다.

## 관련 자료

- [app 차트](../../helm/charts/hopcharlie-app/README.md)
- [ApplicationSet](../../helm/charts/hopcharlie-apps/templates/applicationset.yaml)
- [AWS 접근 정책](../../clusters/aws-dev-workload/hopcharlie-platform-access.yaml)
- [GCP 접근 정책](../../clusters/gcp-dev-workload/hopcharlie-platform-access.yaml)
- [렌더 계약 검사](../../scripts/check-helm.py)
