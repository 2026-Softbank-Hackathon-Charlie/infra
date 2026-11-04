# ClickStack 구현·배포 계획

- 날짜: 2026-11-04
- 상태: A 로컬 구현·검증 승인 후 수행. B/C 실제 조회·배포 미승인/미실행
- 분류: 영향이 크거나 불확실한 작업. GitOps 계약, CRD/operator/RBAC, Secret, PVC, 앱 계측과 멀티클라우드 연결에 걸친다.
- 기준: [ADR 0040](../adr/0040-adopt-clickstack-for-hackathon-observability.md), [아키텍처 제안](../clickstack-architecture.md), [ADR 0018](../adr/0018-stage-minimal-management-monitoring.md), [ADR 0019](../adr/0019-use-chart-managed-prometheus-scrape-token.md)

## 1. 목표와 승인 단위

이번 주 개발 기준을 ClickStack으로 고정한다. HyperDX에서 ClickHouse 로그·트레이스를 조사하고 기존 Prometheus의 메트릭을 조회한다. MongoDB는 UI 상태를 보관한다. 비교용 Loki/Tempo/OpenSearch와 메트릭 저장소 이전은 하지 않는다.

이번 계획은 다음 단계로 승인을 분리한다.

- **A: 로컬 구현·검증.** 새 브랜치에서 chart·GitOps·값·검사·문서를 구현하고, 공개 패키지를 검증한다. Secret은 합성 fixture만 사용한다. 실제 자격 증명 생성, 클러스터 조회·배포·PR merge·CI 배포 실행은 포함하지 않는다.
- **B: 명시적 사전 조회.** A의 렌더링과 변경 요약을 제시한 뒤 대상·조회 권한·정보 범위를 승인받는다. 기존 승인과 겹치는 범위는 재요청하지 않지만 새 DB/operator·Pod 로그·exec·다른 클라우드는 이전 모니터링 승인으로 간주하지 않는다.
- **C: 대상별 실행.** 사전 조회와 실제 렌더링 결과로 Pod/PVC/권한/네트워크/비용을 확정하고, 자격 증명 생성과 수동 sync의 정확한 범위를 별도로 승인받는다.

사용자의 이번 요청은 계획과 리뷰다. A 승인 전 코드·설정 구현을 시작하지 않는다. B/C에는 별도의 실행 승인이 필요하며 이 문서의 계획 승인은 그 실행을 포함하지 않는다.

## 2. 현재 확인한 코드와 새 발견

- 이 저장소는 `helm/gitops/templates/applications.yaml`에서 외부 chart+고정 버전과 Git의 cluster values를 조합한다. `helm/addons/` 디렉터리는 없다. 로컬 chart는 `helm/charts/`에 둔다.
- 실제 클러스터의 앱은 과거 승인 작업에서 수동 상태다. 반면 저장소 GitOps 템플릿은 selfHeal 기본값이 있다. root 일괄 sync 없이 새 관측 앱만 명시적 수동 상태로 등록하며 기존 앱 정책을 바꾸지 않는다.
- `helm/versions.json`, `helm/images.lock.json`, GitOps schema·fixture와 `scripts/check-helm.py`가 chart·이미지·AppProject 계약을 함께 검사한다. 현재 검사는 operator가 생성하는 Pod 이미지나 CR 내부 image repository/tag 조합을 충분히 검증하지 못한다.
- OTel은 addon enum/버전에는 있지만 기본 활성 목록에 없다. management는 Loki/Prometheus 출력, workload는 charlie-apps만 filelog, traces null, 중앙 주소 placeholder다. charlie-platform 로그 수집·앱 trace 계측이 필요하다.
- 앱은 별도 플랫폼 저장소에서 빌드된다. 이 저장소에는 실행 바이너리 chart만 있어 SDK·context 전파 지원은 미확인이다. env만 넣어 trace가 생긴다고 가정하지 않는다.
- 공개 Helm 인덱스와 내려받은 chart archive SHA256을 대조했다. 실제 계정/클러스터 비공개 조회는 하지 않았다.

### 조사한 패키지 후보

| 구성 | 고정 후보 | 검증 결과 |
| --- | --- | --- |
| clickstack | chart 3.5.0 / app 2.39.1 | archive SHA256 a8a781cf8fb0c272351c39daf01ced7db85769eacd6f92fc18897b85f451513a |
| clickstack-operators | chart 1.1.0 | archive SHA256 6605addc21b3419733ee714538b534f0d2d9b5e6a4840488f9e038443c1f899e |
| 기존 agent chart | opentelemetry-collector 0.175.0 | 기존 pin 유지, 중앙 collector 내장 subchart와 동일 버전이라고 가정하지 않음 |

공식 chart 3.5.0은 DB/Collector가 켜지면 hyperdx.secrets=null을 금지한다. MONGO_URI를 ConfigMap에 템플릿하고 DEFAULT_CONNECTIONS에도 자격 증명을 넣는 기본 동작이 있다. 따라서 공식 통합 chart에 운영 비밀번호를 Git values로 넣어 직접 배포하는 경로는 사용하지 않는다. `useExistingConfigSecret`는 datasource 설정용이며 전체 DB 자격 증명 해결책이 아니다.

고정 Helm3.19.1/Kubernetes1.35의 기본 로컬 render도 확인했다. 앱 chart는 Deployment2·Secret2·ConfigMap2·Service2·ServiceAccount1과 DB CR3개를 생성하며, operator chart는 CRD9·Deployment2·ClusterRole6·ClusterRoleBinding5·Role4·RoleBinding4·ServiceAccount5·Service1을 생성한다. 이는 기본 구성의 조사 결과로, 최종 최소 권한/Pod 수가 아니다. ConfigMap의 MONGO_URI 위치만 확인했고 값은 보고서에 출력하지 않았다. operator가 나중에 생성하는 DB Pod/PVC와 내부 계정도 별도 수량·권한에 포함한다.

## 3. 구현 설계와 파일 범위

### 3.1 중앙 구성

공식 chart 3.5.0의 리소스 구조를 기준으로 **Secret 참조를 보완한 로컬 chart** `helm/charts/clickstack/`를 작성한다. HyperDX·공식 ClickStack Collector·ClickHouseCluster/KeeperCluster·MongoDBCommunity를 사용하며 DB operator는 공식 고정 chart를 별도 Application으로 설치한다. 임의 DB 제품이나 저장 엔진으로 바꾸지 않는다. 재사용한 upstream 파일의 라이선스·출처·archive hash와 변경 목록을 기록한다.

보완 범위는 다음으로 제한한다.

- 랜덤 비밀값 생성·Secret 리소스 생성은 chart에서 하지 않는다. 사전 생성 Secret의 이름/key만 참조한다. 합성 fixture가 없는 운영 렌더링은 참조 누락을 실패 처리한다.
- HyperDX의 MONGO_URI·조회 자격·session secret·ingest key는 Secret 참조로 주입한다. datasource 연결도 기존 config Secret을 참조하고 Deployment/ConfigMap/Application에 운영 비밀값을 넣지 않는다.
- MongoDBCommunity의 passwordSecretRef는 사전 생성 전용 Secret을 사용한다. ClickHouse 사용자 자격은 container env의 secretKeyRef와 users config의 from_env 등 고정 operator에서 지원하는 참조 방식으로 연결한다. operator 내부 계정 Secret과 앱 ingest/read 계정을 구분한다.
- 위 참조 방식의 CRD schema와 operator 호환성은 A의 첫 검증 관문이다. 검증되지 않으면 DB 배포로 진행하지 않는다. plaintext CR·Git values·Argo parameter로 우회하지 않는다. 다른 operator나 별도 Secret controller가 필요하면 변경 계획 리뷰·승인을 다시 받는다.
- A0는 패키지·소스·CRD schema·합성 렌더링으로 정적 계약을 확인한다. 실제 operator reconciliation·DB 인증·HyperDX 동작은 C1의 승인된 제한 배포에서 검증한다. 로컬 검사를 런타임 성공 보장으로 표현하지 않는다.
- chart가 생성할 CR과 operator가 생성할 Pod의 이미지까지 digest로 고정한다. dependency는 정확 버전+lock으로 고정하고 main/latest나 범위 버전 재해석을 허용하지 않는다.
- HyperDX의 외부 Prometheus 활성화는 고정 app 이미지에서 검증한다. runtime env로 프론트 기능이 켜지는지 확인하며 필요 시 동일 버전 소스의 build-time 설정을 반영한 재현 가능한 이미지 빌드를 별도 산출물로 만든다. 새 이미지 push/인증은 C 범위다.

설치 순서는 수동 operator/CRD → 준비 확인 → 사전 승인 Secret → 중앙 CR/HyperDX/Collector다. Argo sync wave만으로 독립 Application의 선행 완료를 보장한다고 가정하지 않는다. operator 범위는 observability를 우선하되 실제 필요한 cluster 권한과 watch 범위를 렌더링 보고서에 명시한다.

### 3.2 수집과 앱 연계

- 관리·워크로드 노드 agent는 namespace 허용 목록과 filelog 체크포인트를 사용한다. 실제 플랫폼 로그는 charlie-platform, workload 데모는 승인한 charlie-apps로 제한한다. 중앙 DB/operator/Collector 자체 로그의 무한 재수집을 피한다.
- 앱 OTLP는 클러스터 gateway를 거쳐 중앙 Collector로 보낸다. exporter를 소유하는 계층에 bounded persistent queue와 retry를 둔다. filelog 체크포인트는 파일을 읽는 노드에 둔다.
- 기본 로그는 stdout 한 경로로 수집하고 trace는 SDK로 전송한다. 앱·서비스·버전·cluster·deployment/run/build ID와 trace_id를 연결한다. 민감 필드는 로컬에서 제거한다.
- K8s Events는 클러스터당 단일 수집 주체로 제한해 DaemonSet replicas 중복을 피한다. CI/Argo 결과는 전송 원천/스키마를 별도 adapter로 명시한다. audit 로그 전체 조회는 제외한다.
- 플랫폼 SDK 작업은 별도 저장소의 소유 코드·빌드 경로를 읽고 변경 계획을 확정한 뒤 수행한다. 이 계획의 인프라 코드 승인만으로 앱 저장소 수정·배포를 승인받았다고 표현하지 않는다. 합성 OTLP 테스트와 실제 앱 trace 완료를 분리 보고한다.

### 3.3 네트워크·메트릭·GitOps

- 1차 중앙 UI는 localhost port-forward로 확인하고 Prometheus는 같은 관측 namespace의 ClusterIP로 조회한다. 중앙 UI와 API·OpAMP endpoint의 포트/URL을 구분한다.
- 1차 AWS 두 클러스터 수집은 기존 VPC의 도달성 확인 후 내부 TLS/auth 경로를 사용한다. 기존 앱 NetworkPolicy가 cross-namespace OTLP를 허용하는지 확인하고 필요한 selector/port만 추가한다. 광범위 egress 기본값 확대는 하지 않는다.
- GCP/온프레미스는 outbound OTLP/HTTPS로 중앙 수집기 접근, HyperDX→remote Prometheus는 별도의 인증된 조회 경로다. 기존 사설 연결을 우선한다. 신규 public ingress/ALB/DNS/인증서/SG/VPN은 현재 A나 1차 C에 포함하지 않고 대상 확정 후 별도 실행 범위를 승인받는다.
- 여러 Prometheus source는 source별 조회다. 전역 집계·메트릭 ClickHouse 복사·workload Prometheus 일괄 설치를 포함하지 않는다. 새 클러스터 Prometheus/Alertmanager가 필요하면 별도 대상 단계로 구체화한다.
- 기본 addon 목록에서 미설치 Loki를 새 기본 구성에서 제외하되 기존 live Application을 삭제/prune하지 않는다. 새 operator/중앙 앱과 OTel만 명시적 수동 sync로 추가한다. 기존 앱 자동화는 불변이며 root sync는 하지 않는다.
- Loki bucket/Terraform/state·기존 Loki chart/image 기록은 제거하지 않는다. 이미지 잠금은 사용하지 않는 기존 항목을 보존하고 새 항목만 추가한다.

### 변경 후보 파일

| 경로 | 변경 목적 |
| --- | --- |
| helm/charts/clickstack/ | Secret 참조를 보완한 중앙 chart, schema, CI fixture, upstream 출처 |
| helm/gitops/templates/applications.yaml | 공식 operator Application·로컬 중앙 Application·신규 관측 앱 수동 sync |
| helm/gitops/templates/projects.yaml | chart repo 허용, 최소 CRD/operator destination·리소스 권한 |
| helm/gitops/values.yaml / values.schema.json / ci/targets-values.yaml | addon 구분·선택 활성화·Loki 미설치 기본 제외·검사 fixture |
| helm/versions.json / images.lock.json | archive/정확 버전·이미지 digest 고정 |
| clusters/aws-dev-management/values/clickstack.yaml / clickstack-operators.yaml | 자원·영속 저장·참조 이름·중앙 배포 값 |
| 두 클러스터 values/opentelemetry-collector.yaml | agent/gateway 역할·수집 범위·traces·중앙 경로·queue |
| helm/charts/hopcharlie-platform/templates/network.yaml 등 | 필요한 OTLP 대상에 한정한 네트워크 정책. 앱 계측 연계 승인 후 적용 |
| scripts/check-helm.py / 관련 tests | 수동 앱 정책·Secret 누출·CR/managed 이미지·중복·금지 노출 검증 |
| README / ADR0020 / architecture / 이 계획 | 상태·실제 결과·접근·복구 범위 기록 |

기존 사용자 변경은 보존한다. 최신 main과 문서 변경을 분리해 확인하고, 구현은 새 `feat/clickstack-observability` 브랜치/격리 worktree에서 한다. 작업 중 확인한 main 차이는 무단 덮어쓰기 없이 반영한다.

커밋은 ① Secret 참조 chart·계약 검사 ② GitOps·버전·이미지 잠금 ③ Collector·수집 계약 ④ 문서·검증 결과로 구분한다. 앱 저장소 변경은 별도 커밋/PR과 승인 범위로 관리한다.

## 4. 자원·보존·위험

초기 제안은 ClickHouse shard1/replica1 + Keeper1 + Mongo member1 + HyperDX1 + 중앙 Collector1 + 필요한 operator다. agent는 실제 Linux 노드 수에 따른다. HA를 제공하지 않는다. 실제 렌더링 및 operator 생성 Pod 수를 설치 전 보고한다.

용량 예산 초안은 ClickHouse20Gi + Keeper5Gi + MongoDB5Gi + 중앙 queue2Gi = 중앙 최소32Gi, 로컬 gateway queue는 활성 클러스터당2Gi다. Mongo operator가 별도 logs PVC 등을 만들면 합계에 추가해야 하며 최소값을 최종 비용으로 제시하지 않는다. 기존 PVC는 확장·삭제·교체하지 않는다. agent 체크포인트 hostPath·권한도 별도 보고한다.

로그/이벤트7일·trace3일·기존 Prometheus7일을 제안한다. TTL은 디스크 상한이 아니며 DB system log TTL·보존과 비상 여유를 포함한다. 중앙 전체 requests는 우선 CPU2코어·메모리5Gi 이내를 목표로 렌더링하되 최소 운영 요구라고 보장하지 않는다. 실제 관리 클러스터의 여유/N+1 영향과 합계 requests·limits를 조회 후 결정한다. 부족하면 다른 서비스를 밀어내거나 노드를 자동 증설하지 않고 자원 변경을 별도 승인받는다.

주요 위험은 실험적 Prometheus UI, Secret 참조 호환성, operator의 Secret 읽기 범위·생성 이미지, 플랫폼 앱 계측 미확인, 원격 연결 미준비, 중앙 장애·큐 초과·단일 PVC 복구다. 후속 단계의 미완료를 성공으로 계산하지 않는다.

## 5. 단계와 완료 기준

| 단계 | 수행 | 완료 관문 |
| --- | --- | --- |
| A0 | upstream 패키지/CRD·Secret 참조·프론트 플래그 계약 조사 | 기본 암호/URI 누출 없이 렌더링 가능한 설계. 불가하면 변경 계획 리뷰 |
| A1 | 새 브랜치에서 chart·GitOps·값·체크 구현 | Helm lint/template·schema·digest·계약 검사 통과, 운영 Secret 미생성 |
| A2 | 실제 변경·렌더링 Pod/PVC/RBAC/Network·남은 앱/클라우드 의존성 보고 | 사전 조회/실행 승인 요청에 필요한 구체적 산출물 완성 |
| B | 승인한 클러스터·대상 metadata/용량 점검 | 충돌·권한·스토리지·스케줄링·기존 앱 보존 기준 확보 |
| C1 | 승인한 Secret·operator/CRD·중앙 스택 수동 설치 | CRD Established, DB/HyperDX/Collector Ready, PVC Bound, 로그인·영속성 |
| C2 | 관리 앱 로그·합성 OTLP·HyperDX의 관리 Prometheus 조회 | logs/trace/source 동작. 합성 trace와 실제 앱 trace를 구분 |
| C3 | 별도 승인한 앱 계측·Events/CI/Argo 연계 | 실제 호출 로그↔trace, 배포 실패 ID로 원천 오류 조회 |
| C4 | 실제 두 번째 클라우드/클러스터 수집·query 연결 | 허용한 경로만 열고 cloud/cluster별 검색·source 조회 |
| C5 | 재시작/일시 전송 단절·복구·데모 | 데이터/설정 유지, 누락·중복·지연 및 로컬 메트릭/알림 독립성 |

배포 후 기능 목표 초안은 합성 event id100개·trace10개 수집, 저부하에서 적재 지연 P95 30초 이내·기본 조회 P95 3초 이내, 2분 전송 차단을 queue 용량 내에서 복구하는 것이다. 보장 성능이 아니라 저부하 데모 검증값이며 실제 유입량·노드 여유를 기록한다. 임의 duplicate를 숨기지 않고 고유 ID로 누락·중복을 집계한다. 테스트용 namespace/필터로 차단하고 실제 서비스 전체 네트워크를 중단하지 않는다.

실제 앱은 하나의 오류 요청을 로그·trace로 연결하고, 배포 실패는 Pod가 뜨지 않는 경우의 Event/상태로 설명한다. HyperDX 대표 PromQL(CPU·메모리·Pod 상태)은 직접 Prometheus 결과와 시각/label을 맞춰 확인한다. Grafana 임시 조회를 HyperDX 통합 성공으로 계산하지 않는다.

AWS 두 클러스터만 확인하면 멀티클러스터 완료이며 GCP/온프레미스까지 완료라고 쓰지 않는다. CI/Argo adapter·SDK 또는 원격 연결이 미준비이면 인프라 단계만 완료하고 전체 데모 완료는 보류한다.

## 6. 검증 방법

A는 고정 Helm3.19.1와 Kubernetes1.35 render, make helm-check와 영향 있는 로컬 계약 tests를 사용한다. Secret 값은 합성 fixture만 쓰며 실제 secret·plan/state·인증 header를 출력하지 않는다. 다음 회귀를 명시적으로 검사한다.

- 기존 앱 수·release·destination·자동화 불변, 새 관측 앱 자동화 없음, root 자동화 변경 없음.
- live-secret 내용이나 비밀번호가 ConfigMap/CR/Deployment env.value/Application에 나타나지 않음. Git 값은 Secret 이름/key만 포함.
- NEXT_PUBLIC_*에는 공개 boolean 기능 플래그만 허용한다. 자격 증명·접속 URI·API key가 공개 환경변수나 프론트 번들에 포함되지 않는지 합성 값으로 검사하고 실제 번들 구성도 확인한다.
- 일반 Pod뿐 아니라 CR spec, operator의 operand 이미지까지 digest 및 lock 일치. operator가 런타임 결정하는 이미지가 정적으로 보이지 않으면 생성 Pod metadata 확인을 B/C 관문으로 남긴다.
- ClickHouseCluster/KeeperCluster/MongoDBCommunity schema·AppProject 허용·CRD 설치 순서와 관리 namespace 범위.
- TLS/auth·private service 기본, 인증 안 된 DB/Prometheus ingress 없음, OTel namespace 허용 범위와 큐/체크포인트·Events singleton.
- README inventory·상대 링크·문서 code fence. 현재 로컬 Argo tgz 캐시의 README 누락은 기존 실패로 별도 보고하고 새 파일 검사를 대체하지 않는다.

C는 승인한 합성 데이터·샘플 앱 로그·메트릭을 최소 범위로 조회한다. 제품 데이터·Secret 원문·감사 로그 전체 조회는 제외하며 필요한 Pod 로그/exec/HTTP source 설정 권한은 실행 승인에 구체적으로 명시한다. 서버 dry-run도 실제 API 접근이므로 승인 없이 실행하지 않는다.

## 7. 권한 요청 범위와 복구

A 결과에서 다음을 목록화해 사용자에게 실행 대상을 검토 가능하게 제시한다.

- 계정/region/cluster/namespace/Application 이름과 생성·수정할 CRD·RBAC·operator watch 범위.
- 새 Secret 종류·소유자·권한·보존/폐기, 로그인 관리자 계정 생성, 인증서/API key·DB 계정의 발급·저장 범위. 기존 SA token 조회·복사·추가 발급은 기본 제외.
- PVC·자원·네트워크 변경·대상 endpoint·비용·외부 이미지 push와 앱 롤아웃 여부.
- 사전 조회는 Node/Pod 요청량·namespace/StorageClass/PVC·관련 CRD/Service/RBAC·Application의 명시한 metadata부터 한정. Secret은 이름/type/소유자만 확인하고 값은 조회하지 않는다.
- 실제 배포는 해당 새 앱만 수동 sync. root/기타 애드온·Terraform/state·IAM/SG/DNS는 별도 범위가 없으면 제외한다.

실패 시 새 Application의 operation만 중단하고 직전 검증 revision/입력을 복원한다. Argo prune=false이므로 Git에서 제거해도 리소스가 삭제되지 않는다는 점을 확인한다. DB/CR/PVC 삭제·operator 제거로 해결하지 않는다. operator는 삭제가 아니라 scale0 등 조정도 별도 승인 범위에서 수행하며 CRD는 보존한다. 기존 Prometheus/Grafana와 플랫폼 PVC는 유지한다. Secret 회전/폐기·새 리소스 정리 목록은 별도 승인받고 데이터 잔존을 기록한다.

## 8. 의존성·미확정 사항·리뷰

A 첫 관문에서 Secret reference schema/runtime와 HyperDX 실험적 기능의 실제 고정 버전 계약을 검증한다. 이미지 digest·상세 render 수량·최소 자원·앱 저장소 지원·GCP/온프레미스 접근은 아직 미확정이다. 이것들이 확정되기 전 C 실행은 승인 요청하지 않는다. 범위/구성요소/권한이 확대되면 변경 계획을 리뷰받는다.

리뷰 결과는 아래에 기록한다. 구현 승인 전 중대한 지적을 해소한다.

### 독립 리뷰 결과 — 2026-11-04

- 새 plan-reviewer `review_clickstack_execution_plan` 판정: **GREEN — A 로컬 구현·검증 계획에 중대한 미해결 지적 없음. B/C 실행 준비 완료나 배포 승인은 아님.**
- 검토 범위: 요구·완전성·범위·실현 가능성·Secret 처리·검증·복구. 역할의 파일 도구 제약으로 파일 직접 열람 대신 대화에 제공된 계획 전체 본문과 반영 요약을 검토했다.
- 반영 사항: A0 정적 검사와 C1 런타임 검증 경계, NEXT_PUBLIC_* 및 프론트 번들의 Secret 누출 검사, 커밋 단위 분리를 추가했다.
- 남은 실행 전 관문: 이미지 digest와 실제 Pod/PVC/RBAC, Secret 참조의 정적/런타임 계약, HyperDX Prometheus 기능, 클러스터 용량과 앱·멀티클라우드 연결. A 산출물/승인된 B 조사 이후에만 C 실행 범위를 확정한다.
- 수행 결과: 문서 작성과 공개 패키지 checksum·기본 로컬 render만 수행했다. 구현 코드·설정·자격 증명·실제 인프라 변경은 하지 않았다.

## 관련 자료

- [공식 chart release 3.5.0](https://github.com/ClickHouse/ClickStack-helm-charts/releases/tag/clickstack-3.5.0)
- [공식 Helm 인덱스](https://clickhouse.github.io/ClickStack-helm-charts/index.yaml)
- [공식 Helm 구성](https://clickhouse.com/docs/clickstack/deployment/helm)
- [PromQL 지원 상태](https://clickhouse.com/blog/whats-new-in-clickstack-october-2026)
- [기존 Helm 검증](../../scripts/check-helm.py)
- [기존 GitOps Application](../../helm/gitops/templates/applications.yaml)

## A 수행 기록

사용자 승인: “새로운 브랜치 만들어서 진행해줘.” 최신 main 68123c6 기준 격리 worktree의 feat/clickstack-observability에서 수행했다. 이전 작업 트리의 변경은 보존했다. main의 번호 충돌로 ADR0040을 사용하며 기존 ADR0020을 덮어쓰지 않았다.

중앙 Collector는 공식 standalone 이미지의 설정 순서를 보존하기 위해 범용 subchart 대신 작은 Deployment로 작성했다. 기존 설정 이름과 고정 이미지의 호환성을 위해 agent/gateway의 rewriteDeprecatedComponentNames를 껐다. MongoDB는 신규 8.0.17 digest를 선택했고 실제 조합은 C에서 검증한다. 변경 결과·미완료·실행 전 관문은 [배포 전 검토](../clickstack-deployment-review.md)에 기록한다.

chart·GitOps·수집 검사 간 참조가 밀접하므로 구현은 검증 가능한 하나의 통합 변경으로 묶는다. 앱 저장소 변경은 하지 않았다. 운영 비밀값 생성·Secret 저장·클러스터 API 호출·이미지 push·인프라 배포는 수행하지 않았다.
