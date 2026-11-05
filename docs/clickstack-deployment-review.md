# ClickStack A 단계 구현 결과와 배포 전 확인 사항

- 날짜: 2026-11-04
- 브랜치: feat/clickstack-monitoring-preparation
- 기준 main: d56b207 (PR #63 머지, 원래 A 구현 기준 68123c6)
- 승인: A 로컬 구현·검증과 2026-11-05 관리 EKS의 한정 읽기 점검(B) 수행. Secret 값 조회·자격 증명 생성·클러스터 변경·배포(C)는 미승인/미실행.
- 결정: [ADR 0050](adr/0050-adopt-clickstack-for-hackathon-observability.md)
- 계획: [구현·배포 계획](plans/clickstack-implementation.md)

## 구현 결과

- 공식 ClickStack chart 3.5.0을 기준으로 Secret 생성 템플릿·접속 문자열 노출을 제거한 로컬 chart를 작성했다. 차이는 [UPSTREAM](../helm/charts/clickstack/UPSTREAM.md)에 기록했다.
- 중앙은 HyperDX·공식 Collector·ClickHouse/Keeper·MongoDBCommunity다. DB operator는 공식 chart 1.1.0을 별도 Application에서 사용한다.
- 공식 Collector의 standalone entrypoint를 직접 사용한다. 범용 Collector subchart의 relay.yaml 인수가 원본 파이프라인을 덮어쓰는 위험을 피했으며, 별도 중앙 dependency를 없앴다.
- 관리/워크로드 agent와 gateway 값, HTTPS·bearer·CA 검증·큐·로그 허용 목록을 작성했다. 앱 SDK·CI/Argo adapter와 Events 수집 RBAC는 아직 구현/배포하지 않았다.
- 새 AppProject와 수동 Application을 추가했다. 기본 observability.enabled=false다. 활성화도 GitOps 리소스 렌더링일 뿐 실제 배포 승인이 아니며 root를 일괄 sync하지 않는다. 기존 앱은 변경하지 않는다.
- 기존 Loki는 새 기본 addon 목록에서만 제외한다. live Application, bucket, Terraform state와 기존 잠금 항목은 삭제하지 않는다.
- 기본 dashboard source fixture는 로그·트레이스 상호 참조와 external Prometheus를 포함한다. 실제 UI 계정·source·Secret 생성은 하지 않았다.

## 최신 main과의 정합성

초기 작업의 ADR 번호는 최신 main에서 다른 결정에 사용하므로 미병합 ClickStack 결정을 0048로 옮겼다. 기존 문서는 덮어쓰지 않았다. GKE 앱 namespace 권한/Admission/ApplicationSet도 수정하지 않았다.

[ADR 0037](adr/0037-connect-aws-gateway-to-gke-apps-with-tailscale-routers.md)의 Tailscale 경로는 관리 AWS→GKE 앱 TCP80 범위다. OTLP4318·Prometheus9090의 연결이나 승인으로 간주하지 않으며 라우터·정책·Terraform을 변경하지 않았다. 실제 원격 관측 연결은 B/C에서 별도로 확정한다.

## 고정 패키지와 설정 계약

| 항목 | 선택/검증 |
| --- | --- |
| upstream 중앙 chart | 3.5.0, archive hash 대조 완료 |
| operator chart | 1.1.0, archive hash 대조 완료 |
| HyperDX/Collector | 2.39.1, 공개 manifest digest 잠금 |
| ClickHouse/Keeper | 26.8.36.3-alpine, 공개 manifest digest 잠금 |
| MongoDB | 신규 8.0.17-ubi9, 이미지 digest와 CR spec 고정. 실제 조합 동작 미검증 |
| 관리·워크로드 OTel | chart0.175.0 + contrib0.161.0, 기존 이미지 digest 유지 |
| OTel 이름 변환 | rewriteDeprecatedComponentNames=false. filelog/k8sattributes/otlphttp를 고정 이미지가 인식하는 이름으로 유지 |
| frontend PromQL | 고정 소스의 next-runtime-env와 refresh-env.js 확인. NEXT_PUBLIC_ENABLE_PROMQL=true 런타임 경로 사용 |
| API PromQL | ENABLE_PROMQL=true. 외부 Prometheus 조회이며 ClickHouse TimeSeries로 데이터를 옮기지 않음 |
| Secret 참조 | 고정 ClickHouse operator v0.0.7의 env secretKeyRef/defaultUserPassword/from_env 구현과 CRD 구조 확인 |

이미지 변경·패키지 갱신은 다시 lock과 schema를 검증한다. 소스 조사·Helm render는 operator reconciliation이나 실제 UI 정상 동작을 증명하지 않는다.

## 렌더링 자원과 권한

아래는 현재 값으로 로컬 render한 수량이다. operator가 이후 만드는 DB Pod·내부 Secret·PVC는 포함되지 않는다.

| 직접 생성 리소스 | 중앙 앱 | operator 앱 |
| --- | ---: | ---: |
| Deployment | 2 | 2 |
| ServiceAccount | 1 | 5 |
| Service | 2 | 1 |
| ConfigMap | 2 | 0 |
| PVC | 1 | 0 |
| DB CR | 3 | 0 |
| CRD | 0 | 9 |
| ClusterRole / ClusterRoleBinding | 0 / 0 | 2 / 1 |
| Role / RoleBinding | 0 / 0 | 4 / 4 |
| chart 생성 Secret | 0 | 0 |

- ClickHouse operator는 namespaced RBAC와 observability watch 목록을 사용한다. MongoDB operator도 observability를 감시하고 MongoDBCommunity만 reconcile한다. ClusterMongoDBRoles·PVC resize·webhook 등록은 껐다.
- operator에는 observability namespace의 Secret 조회/생성/변경/삭제 권한이 필요하다. 이것은 이전 Prometheus ServiceAccount의 Secret API 조회 금지와 다른 새 권한이며 실행 승인이 필요하다.
- 공식 operator chart가 설치하는 CRD9개에는 미사용 MongoDB API도 포함된다. 실제 API 소유권 충돌과 기존 operator 여부를 B에서 확인한다. CRD를 자동 제거하지 않는다.
- agent는 Pod·namespace metadata를 읽기 위한 chart RBAC와 호스트 로그/체크포인트 볼륨이 있다. 로그를 읽고 체크포인트를 쓰기 위해 UID0을 사용하되 privilege escalation을 금지하고 capability를 제거한다. namespace 필터는 수집 범위이며 호스트 로그 접근의 권한 경계는 아니다. gateway와 중앙 Collector/HyperDX는 ServiceAccount token을 마운트하지 않는다.
- AppProject는 Secret 직접 생성·Ingress·wildcard namespace 리소스를 허용하지 않는다. operator가 자체 SA로 생성하는 Secret 권한과 Argo AppProject 권한은 별개다.

### 저장소

중앙 데이터 볼륨 의도는 ClickHouse20Gi + Keeper5Gi + Mongo data5Gi/logs1Gi + 중앙 queue2Gi = 33Gi다. 관리 gateway queue2Gi를 합하면 35Gi이며, agent 체크포인트와 operator가 추가 생성할 저장 공간은 별도다. 기존 Prometheus/Grafana27Gi나 플랫폼 PVC는 바꾸지 않았다. 실제 Pod/PVC와 requests/limits·노드 배치는 B 조회와 C 생성 후 확인한다.

## Secret 참조 계약 — 값 생성 없음

| 참조 이름 | 필요한 내용 | 소비 주체 |
| --- | --- | --- |
| clickstack-runtime | MONGO_URI, EXPRESS_SESSION_SECRET, TOKEN_ENCRYPTION_KEY, HYPERDX_API_KEY | HyperDX |
| clickstack-clickhouse-auth | read-password, ingest-password, default-password | ClickHouse 컨테이너/기본 사용자 |
| clickstack-collector-auth | CLICKHOUSE_PASSWORD, OTLP_AUTH_TOKEN, 선택한 추가 클러스터 token key | 중앙 Collector |
| clickstack-mongodb-auth | password | MongoDB operator의 hyperdx 사용자 |
| clickstack-ui-config | connections.json, sources.json | HyperDX 초기 팀 설정 |
| clickstack-otlp-tls | tls.crt, tls.key | 중앙 수집 HTTPS |
| observability-client-auth | OTLP_AUTH_TOKEN | 해당 클러스터 agent/gateway |
| observability-client-ca | ca.crt | 해당 클러스터 agent/gateway |
| observability-gateway-tls | tls.crt, tls.key | 해당 클러스터 gateway |

B/C에서 인증서 SAN은 실제 service DNS와 일치하도록 하고, 각 URI는 실제 DB 사용자와 URL 인코딩된 자격 증명으로 구성한다. Collector ingest password와 DB ingest-password, client token과 중앙 허용 token을 일치시켜야 한다. TOKEN_ENCRYPTION_KEY는 고정 버전이 요구하는 32바이트 키 형식으로 생성한다. 비밀값은 Git·명령 인수·로그에 넣지 않는다. operator가 만드는 내부 인증 Secret은 별도 소유권 목록에 포함한다.

클러스터별 token을 추가할 때 중앙 additionalIngestTokenKeys와 해당 Secret key를 함께 준비한다. 이것은 취소 가능한 클러스터별 수집 자격이지 프로젝트별 조회 격리나 cluster 속성의 서명 검증은 아니다. 팀 내부 데모 범위를 넘는 멀티테넌트 환경에는 추가 인가 설계가 필요하다.

## 실행 전 검증과 미완료

로컬 계약 검사, Helm lint/render, 기본 Secret 값 거부, CRD 구조, digest, 수동 GitOps·기존 앱 불변, agent/gateway config와 합성 payload 연결을 검사한다. 전체 Helm 회귀 검사에는 기존 앱·GKE·CNPG 계약도 포함한다.

로컬 Docker daemon을 사용할 수 없어 컨테이너 실행 기반의 Collector config 검증은 하지 못했다. 실제 TLS/인증·OTTL·schema migration·DB 기동·MongoDB8 호환성·HyperDX 로그인/source/차트·영속 큐 복구는 C의 제한 배포 관문이다. A 단계에서는 Secret 값을 발급하거나 API server에 접근해 검증하지 않았다. B의 한정 읽기 점검 결과는 아래에 별도로 기록한다.

본문의 속성 키 제거와 credential 패턴 로그 제외는 보조 필터다. 모든 민감정보를 탐지한다는 보장은 없으며 검증된 데모 namespace/합성 데이터부터 사용한다. CI/Argo/Events adapter·실제 앱 SDK·GKE/온프레미스 연결은 후속 작업으로 남긴다. remote gateway endpoint는 ConfigMap 참조이며 REPLACE 같은 가짜 운영 DNS를 넣지 않았다.

## 확인 명령 — 로컬/합성

```sh
make clickstack-check
make helm-check
python3 scripts/observability-smoke.py
python3 scripts/observability-smoke.py --write-fixtures .generated/clickstack-smoke
```

`--send`를 주지 않으면 네트워크로 보내지 않는다. 승인된 실행 단계에서는 HTTPS endpoint·CA와 환경으로 공급한 OTLP_AUTH_TOKEN을 사용한다. Collector HTTP 수락은 저장 성공 증거가 아니므로 run_id/event_id별 누락·중복을 별도로 조회한다.

UI 확인은 승인 후 service/clickstack의 app3000/API8000 포트를 실제 고정 이미지 동작에 맞춰 연결한다. 기본 FRONTEND_URL은 localhost:3000이다. 공개 UI/ingest·원격 Prometheus 연결을 위한 LB·DNS·방화벽·인증서 변경은 이번 A 작업에 포함되지 않는다.

## 로컬 검증 결과

- ClickStack 계약 검사 15개 통과: Secret 거부/필수 참조, 고정 이미지와 CRD 구조, collector 인증·TLS·큐, external Prometheus fixture, 수동 GitOps·기존 앱 불변, 좁은 선택적 앱 egress와 합성 smoke payload.
- 전체 make helm-check 통과: 기존 bootstrap/GKE·기본 addon·플랫폼·ApplicationSet·CNPG 회귀 포함. 이후의 국소 설정/검사 변경은 ClickStack 15개 검사로 재검증했다.
- Helm strict lint와 git diff --check 통과. make inventory-check는 400개 문서화 파일로 통과했고 문서 상대 링크·Python 문법을 확인했다. 원래 작업 트리의 캐시 예외를 새 worktree 검증에서 제외할 필요가 없었다.
- 합성 smoke 명령 기본 실행은 payload만 구성하며 네트워크 전송을 하지 않는 것을 확인했다.
- A 단계에서 운영 자격 증명 생성·클러스터 접근·컨테이너 기동·실제 배포는 하지 않았다. Docker daemon 미가용으로 런타임 검증은 남아 있다.

## B 관리 EKS 사전점검 — 2026-11-05

사용자는 관리 EKS의 자원·스토리지·관련 CRD/operator·Prometheus 서비스 정보를 읽기 전용으로 점검하는 범위를 승인했다. Secret API 조회, Pod 로그/exec, 리소스 변경은 수행하지 않았다. 기존 AWS 로그인 갱신 후 account525091405886 / ap-northeast-2 / charlie-dev-management를 확인했다. 임시 SSM 연결과 Prometheus 포워딩만 사용했으며 점검 후 해당 연결을 정리한다.

원본 전체 API 응답은 보관하지 않았다. 필요한 자원/메타데이터만 추린 보고서는 Git 제외 `.generated/clickstack/management-preflight.json`에 권한0600으로 저장했다. API 조회 오류는 없었다. Secret 값·환경변수 값은 보고서에 포함하지 않는다.

### 실제 상태

| 항목 | 결과 |
| --- | --- |
| 노드 | m7i-flex.large / amd64 2대, 양쪽 Ready, taint·unschedulable 없음 |
| 현재 활성 Pod | 34개, Pending 없음 |
| allocatable CPU | 합계3.86코어 |
| 기존 CPU requests | 합계2.50코어 |
| CPU requests 기준 여유 | 합계1.36코어, 노드별0.69/0.67 |
| 메모리 requests 기준 여유 | 합계약10.05Gi |
| 실제 사용량 표본 | CPU 노드별100m/218m(5%/11%), 메모리2562Mi/2220Mi(36%/31%) |
| StorageClass | gp3: EBS CSI, 암호화, WaitForFirstConsumer, 확장 허용 |
| 기존 관측 PVC | Prometheus20Gi·Grafana5Gi·Alertmanager2Gi 모두 Bound |
| 기존 관련 CRD/operator | ClickHouse·MongoDB·OTel 관련 CRD 및 Deployment 없음 |
| 관측 workload | Prometheus·Grafana·Alertmanager·관련 exporter/operator 모두 Ready |
| Prometheus | monitoring-prometheus:9090 ClusterIP, ready endpoint1개, /-/ready HTTP200 |

Prometheus 서비스 DNS `monitoring-prometheus.observability.svc:9090`은 fixture와 일치한다. 확인한 HTTP 연결은 관리용 port-forward 경로이며, 아직 존재하지 않는 HyperDX Pod에서의 DNS·NetworkPolicy·로그인/source 동작까지 검증한 것은 아니다.

### CPU 배치 관문 — 현재 값 그대로 C로 진행하지 않음

중앙 앱·DB 선언·operator에 명시된 CPU requests는 약1.41코어, 관리 gateway0.10 및 노드 agent2개0.20을 더하면 약1.71코어다. 현재 여유1.36보다 **최소0.35코어 부족**하다. MongoDB 등의 operator 생성 기본값·init/probe 요청량이나 Pod-level admission 기본값은 추가될 수 있어, 이 계산은 최종 필요량의 상한이 아니다.

이는 CPU의 실제 사용률 과부하가 아니라 scheduler 예약량 부족이다. 메모리가 남더라도 CPU request 조건을 만족하지 못한 Pod는 Pending이 될 수 있다. 현재 코드의 리소스를 그대로 모두 배포 가능한 상태라고 판정하지 않는다.

다음 단계는 새 구성의 자원 요청 조정 또는 관리 클러스터 CPU 용량 확보안을 별도 계획으로 확정하는 것이다. 이번 B 승인만으로 기존 앱 requests 축소·Pod 이동·노드 증설·Terraform apply·새 Secret·부분 설치를 하지 않는다. 자원 조정 시 원본 권장값과 실제 동작·여유를 검증하며, CPU 사용률이 낮다는 이유만으로 requests를 무조건 낮추지 않는다.

### 스토리지·미확인 범위

35Gi는 새 볼륨 요청량 제안이다. EBS는 동적 생성하므로 StorageClass/PVC 상태만으로 계정 quota·실제 provision 성공·추가 비용까지 보장할 수 없다. 신규 PVC는 만들지 않았다.

참조 Secret 존재/값, 새 RBAC 이름 충돌·API 권한 결과, Argo Application 정책/연결, HyperDX runtime, workload/GCP/온프레미스는 이번 한정 조회에 포함하지 않았다. 기존 CRD·operator 없음은 확인했지만 이들 미확인 항목까지 완료한 것으로 확대하지 않는다. C 실행 요청 전 자원 방안과 정확한 변경·권한 범위를 다시 정리한다.

## 2026-11-05 재개 시 현재 상태

- PR #63 merge SHA: d56b2076264803ebe0d6559044cb40974a0eaa18. 최신 main의 증설 CI 코드는 포함됐지만 조회한 실행 이력에는 수동 증설 실행이 없다.
- 실제 2a 노드 그룹: ACTIVE, min/desired/max=1, health issue 없음. 따라서 노드 증설과 ClickStack 배포를 완료했다고 표현하지 않는다.
- 준비 브랜치는 최신 main의 플랫폼 이미지와 권한·네트워크 결정을 보존한다. 관측 구성은 계속 기본 비활성화 및 새 앱 수동 sync다.
- 실제 C 단계 전에: 지정 SHA로 management-scaleout CI plan/apply와 노드 3대 Ready·Pod별 배치 공간을 확인하고, 신규 operator/RBAC·Secret·PVC 및 Argo 앱 등록/sync 범위를 승인받아야 한다. 공개 endpoint나 다른 클라우드는 1차 설치에 포함하지 않는다.
- 최초 설치 후 DB 인증/마이그레이션, HyperDX 로그인과 Prometheus source, TLS/OTLP 수신, 로그↔trace 및 재시작 후 영속성을 실제 확인해야 한다. Helm render나 PR 검사 통과로 이 항목을 완료 처리하지 않는다.

### 재개 후 로컬 검증

- 최신 main과 합친 상태에서 실제 고정 operator/OTel chart를 사용한 ClickStack 계약 15개 통과.
- 전체 `make helm-check` 통과: 기존 bootstrap/GitOps/baseline/addon/플랫폼/ApplicationSet/CNPG 및 최신 system NetworkPolicy 검사 포함.
- Terraform fmt/config-check, inventory 435개, diff 공백 및 변경한 Python 구문 검사 통과. Terraform 코드 자체는 이 PR에서 수정하지 않았다.
- Docker daemon은 접근 가능한 실행 상태가 아니므로 DB/operator/HyperDX 컨테이너 런타임 검증은 수행하지 않았다. 기존 C 단계의 실제 로그인·수신·영속성 검증 관문을 유지한다.
- 승인 범위와 실제 수행: 로컬 구현·검증·PR 준비 및 기존 승인 범위의 관리 nodegroup metadata 조회만 수행. Secret 조회/생성, Pod 로그/exec, live dry-run, CI 수동 배포와 Argo sync는 하지 않았다.

## 2026-11-05 관리 노드 증설 완료

- CI plan [41991039778](https://github.com/2026-Softbank-Hackathon-Charlie/infra/actions/runs/41991039778), apply [41991786700](https://github.com/2026-Softbank-Hackathon-Charlie/infra/actions/runs/41991786700) 모두 성공했다. 실행 SHA는 159a34b30336ecc00b69debcc91890ec77d820eb이다.
- 08:16 UTC 확인에서 관리 2a 2대·2c 1대, 3대 모두 Ready이며 기존 workload Ready 부족/Pending이 없었다. CPU allocatable 합계5.79, requests2.68, 여유3.11코어다. 이전의 1.36코어 여유 수치는 증설 전 기록이다.
- 이 결과는 ClickStack 설치·Secret 생성 완료가 아니다. 신규 operator 생성 Pod/init 요청과 실제 배치·런타임 검증은 여전히 C 관문이다.
- 최신 main(919d7f3)의 다른 프로젝트 변경을 보존하며 미병합 ClickStack ADR을 0050으로 정리했다. 실제 CI 부트스트랩은 별도 [C 계획](plans/clickstack-management-deployment.md)과 제안 ADR0051의 승인 대상이다.
