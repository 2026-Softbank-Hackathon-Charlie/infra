# 0072. 관리 플랫폼 백엔드에서 관측 데이터를 조회한다

- 날짜: 2026-11-07
- 상태: 채택 — 사용자 지정 조회 아키텍처. 플랫폼 adapter/API·credential·접근 정책 구현 및 배포는 미완료다.
- 관련 ADR: [0050 ClickStack](0050-adopt-clickstack-for-hackathon-observability.md), [0061 사설 조회](0061-private-otlp-ingress-for-aws-workload.md), [0071 인증 수정](0071-patch-hyperdx-prometheus-basic-auth.md)

## 배경과 제약

사용자는 HyperDX 화면 외에 관리 플랫폼에서 모니터링 데이터를 표시하고, 관리 플랫폼이 요청을 보내 데이터를 가져오도록 결정했다. 로그·트레이스는 ClickHouse, 현재 Kubernetes/CNPG 메트릭은 Prometheus에 저장된다. HyperDX와 Prometheus의 조회 성공은 확인했지만, 플랫폼 백엔드의 별도 credential·네트워크·앱별 권한 연동은 구현되지 않았다.

## 검토한 대안

- 브라우저에서 HyperDX/Prometheus 직접 호출: 내부 주소 노출과 backend credential 유출, 플랫폼 앱 소유권 검사 분산 위험이 있어 선택하지 않는다.
- 플랫폼 백엔드가 ClickHouse에 직접 SQL 요청: 분석 자유도가 높지만 DB schema·SQL 권한·쿼리 비용 관리가 추가된다. 복잡한 SQL 분석이 필요해지면 별도 검토한다.
- 플랫폼 백엔드가 HyperDX 외부 API와 Prometheus HTTP API를 호출: 기존 source·검색 기능과 메트릭 저장소를 재사용하고 플랫폼 권한 검사를 한곳에서 수행할 수 있어 채택한다.

## 결정과 이유

프런트엔드는 플랫폼 API를 호출한다. 관리 플랫폼 백엔드는 사용자와 앱/배포 인스턴스의 권한을 검증한 뒤, 로그·트레이스는 HyperDX `/api/v2/sources` 및 `/api/v2/search`, 메트릭은 허용한 cluster의 Prometheus `/api/v1/query`·`/api/v1/query_range`를 조회한다. Workload 메트릭은 기존 사설 TLS/BasicAuth query proxy를 거친다.

credential은 백엔드에서만 사용하고 앱·namespace·cluster 범위와 조회 제한을 서버에서 강제한다. 내부 URL·source ID·임의 SQL/PromQL을 브라우저가 지정하도록 노출하지 않는다. 플랫폼용 credential 공급과 Pod 네트워크 허용 여부는 구현 시 별도로 검증한다. Personal key에 앱별 read-only 격리가 있다고 가정하지 않는다.

이 결정은 기존 HyperDX 분석 화면·수집기·저장소 결정을 대체하지 않고 플랫폼 조회 경로를 추가한다. 새 공개 DB endpoint나 수집 pipeline을 만들기로 결정한 것은 아니다. API route·초기 timeout/limit 수치는 [연동 문서](../operations/platform-observability-query.md)의 제안이며 구현 계획에서 확정한다.

## 영향과 후속 작업

- 기대 효과: 플랫폼 앱 권한과 UI를 일관되게 적용하고 HyperDX의 source 검색을 재사용한다.
- 단점과 위험: HyperDX 외부 API에 대한 의존, 로그와 메트릭의 별도 adapter, 넓은 권한을 가진 API key 보호, 필터 우회·비용 큰 쿼리·클러스터 오선택 위험이 있다.
- 후속 작업: adapter와 앱별 권한/필터, backend credential 공급, 사설 연결·CA 검증, 응답 정규화·요청 제한, UI와 실제 플랫폼 Pod 통합 검증을 구현한다. credential 발급·Secret·정책·배포 변경은 이 문서화 작업에서 실행하지 않는다.

## 관련 자료

- [관리 플랫폼의 모니터링 데이터 조회 연동](../operations/platform-observability-query.md)
- [기존 query proxy](../../helm/charts/observability-boundary/templates/_helpers.tpl)
- [ClickStack 공식 외부 API](https://clickhouse.com/docs/clickstack/api-reference)
- [Prometheus HTTP API](https://prometheus.io/docs/prometheus/latest/querying/api/)
