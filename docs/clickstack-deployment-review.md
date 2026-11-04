# ClickStack A 단계 구현 결과와 배포 전 확인 사항

- 날짜: 2026-11-04
- 브랜치: feat/clickstack-observability
- 기준 main: 68123c6
- 승인: 새 브랜치에서 A 로컬 구현·검증 진행. 실제 클러스터 조회·Secret 생성·배포는 미승인/미실행.
- 결정: [ADR 0040](adr/0040-adopt-clickstack-for-hackathon-observability.md)
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

이전 작업 트리의 ADR 0020 번호는 main의 GKE/Headlamp 문서가 이미 사용하므로 0040을 새로 사용했다. 기존 문서는 덮어쓰지 않았다. GKE 앱 namespace 권한/Admission/ApplicationSet도 수정하지 않았다.

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

로컬 Docker daemon을 사용할 수 없어 컨테이너 실행 기반의 Collector config 검증은 하지 못했다. 실제 TLS/인증·OTTL·schema migration·DB 기동·MongoDB8 호환성·HyperDX 로그인/source/차트·영속 큐 복구는 C의 제한 배포 관문이다. Secret 값을 발급하거나 API server에 접근해서 검증하지 않았다.

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
- 운영 자격 증명 생성·클러스터 접근·컨테이너 기동·실제 배포는 하지 않았다. Docker daemon 미가용으로 런타임 검증은 남아 있다.
