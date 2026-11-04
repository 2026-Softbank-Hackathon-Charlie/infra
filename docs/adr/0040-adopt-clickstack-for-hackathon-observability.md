# 0040. 해커톤의 멀티클라우드 관측 플랫폼으로 ClickStack 사용

- 날짜: 2026-11-04
- 상태: 채택 — ClickStack 개발 방향과 A 로컬 구현·검증 승인. 실제 클러스터 조회·자격 증명 생성·배포는 미승인/미실행
- 관련 ADR: [0018 기본 모니터링 설치](0018-stage-minimal-management-monitoring.md), [0019 Prometheus 토큰](0019-use-chart-managed-prometheus-scrape-token.md)

## 배경과 제약

이번 프로젝트는 이번 주 안에 동작하는 결과를 만드는 해커톤이다. AWS·GCP·온프레미스 Kubernetes에 배포되는 앱의 배포·실행 오류를 중앙에서 조사하고, 향후 AI 기반 원인 설명에 연결한다. OpenTelemetry로 수집 경계를 표준화하고 자체 호스팅한다.

사용자는 비교를 계속하기보다 ClickStack으로 개발하는 방향과 HyperDX에서 Prometheus까지 조회하는 구성을 요청했다. 이번 개발 기준은 ClickStack으로 고정한다. LGTM 비교 PoC와 Loki 복귀를 기본 개발 절차로 두지 않는다. 기존 Grafana 설치 비용은 선정 근거가 아니다. 작업 중 LGTM을 권고한 초안은 미채택 제안이었다. 최신 main의 ADR 0020은 별도 결정에 사용되어 새 번호 0040으로 기록한다.

확인한 기존 결과는 2026-11-03 관리 클러스터 Prometheus/Grafana 메트릭 수집이다. 이번 문서 작성에서는 실환경을 재점검하지 않았다. GCP·온프레미스 연결·수집 완료는 미확인이다. 저장소 OTel 설정의 관리 출력은 Loki/Prometheus이고 workload 로그 범위는 charlie-apps이며 traces는 양쪽 모두 null이다. ClickStack·앱 계측·전 클라우드 수집이 이미 구현되었다고 간주하지 않는다.

## 검토한 대안

| 대안 | 장점 | 이번 판단 |
| --- | --- | --- |
| Grafana + Prometheus + Loki + Tempo | Kubernetes 메트릭과 로그·트레이스 연계 | 유효하지만 이번 주 병행 구축·비교에서 제외. Loki의 시간 필터·ID 검색이 독점 기능은 아님 |
| OpenSearch 기반 관측 | 문서·텍스트 검색과 색인 기반 분석 | 전문 검색이 핵심이라는 요구가 없어 제외 |
| Grafana + ClickHouse | 같은 DB의 로그·트레이스 조회와 대시보드·알림 | HyperDX 탐색 기능을 우선 재사용하며 Grafana 연결을 새로 개발하지 않음 |
| HyperDX 기반 ClickStack + 독립 Prometheus | 로그·트레이스 UI와 SQL 분석을 활용하고 메트릭 운영 기능 유지 | 이번 주 개발 기준으로 선택 |

## 결정과 이유

1. **ClickHouse에 로그·트레이스와 필요한 빌드/배포 이벤트를 저장한다.** 클라우드·클러스터·서비스·배포·실행 속성으로 검색·집계하고 후속 AI 조사에 활용한다.
2. **HyperDX를 주 콘솔로 한다.** 검색·트레이스 탐색 기능을 활용하고 팀은 수집·데이터 연결·오류 분석에 집중한다. SQL의 장점과 UI의 선택 이유를 구분한다.
3. **ClickStack OSS의 MongoDB를 함께 운영한다.** 사용자·대시보드·알림 설정 등 앱 상태를 저장한다. 로그·트레이스 중복 저장소가 아니다.
4. **Prometheus/Alertmanager에 메트릭 수집·저장·규칙·알림을 유지한다.** 데이터 종류가 달라서가 아니라 이번 주에 PromQL·규칙·알림까지 이전하는 작업을 제외하기 위해서다. 신규 클러스터의 수집·권한은 별도 준비하며 기존 관리 토큰을 다른 클러스터에 재사용하지 않는다.
5. **HyperDX에서 외부 Prometheus를 조회한다.** 데이터를 ClickHouse에 복사하지 않고 PromQL 요청을 외부 endpoint에 전달한다. 실험적 기능이므로 고정 버전에서 API의 ENABLE_PROMQL과 앱의 NEXT_PUBLIC_ENABLE_PROMQL 활성화·인증·차트를 검증한다. ClickHouse TimeSeries 엔진의 자체 PromQL 실행과 메트릭 저장소 이전은 제외한다.
6. **기존 Grafana는 개발 확인용으로 보존한다.** 신규 대시보드 구축·제거·이전을 하지 않는다. 임시 Grafana 사용을 HyperDX 메트릭 통합 성공으로 계산하지 않는다.
7. **멀티클라우드 수집 계약을 표준화한다.** 클러스터별 OTel 수집 계층에서 인증된 TLS로 중앙에 전송한다. 중앙 저장소는 우선 AWS 관리 클러스터에 두고 ClickHouse를 WAN에 걸쳐 복제하지 않는다. 상세 경계는 [아키텍처 제안](../clickstack-architecture.md)에 기록한다.

멀티클라우드·OTel 지원은 후보의 공통 충족 조건이다. 선택 이유는 오류 조사 목적, SQL 분석의 유연성, HyperDX 재사용과 일정이다. ClickStack이 LGTM보다 항상 빠르거나 저렴하거나 운영하기 쉽다고 주장하지 않는다.

## 영향과 후속 작업

- 기대 효과: 저장소·콘솔을 고정하고 배포/실행 오류를 수집부터 조사까지 연결한다. 새 클러스터는 공통 계약을 사용한다.
- 비용과 위험: ClickHouse·MongoDB·Collector의 영속 저장·보존·자원·복구를 운영한다. HyperDX 외부 Prometheus 연동은 실험적이며 공식 문서의 세밀한 UI RBAC는 Managed 전용이다. 실제 OSS 접근 경계를 검증한다.
- 멀티클라우드 제약: OTLP 전송과 HyperDX의 원격 Prometheus 조회는 다른 네트워크 경로다. 여러 source 연결은 전 클라우드의 단일 PromQL 집계를 뜻하지 않는다. 단절·egress·큐 초과·중복·중앙 장애가 남는다. 초기 구성은 비HA이며 로컬 메트릭·규칙·알림의 독립성을 목표로 한다.
- 범위 제외: Loki/Tempo/OpenSearch 설치, 비교 벤치마크, Prometheus 저장소 이전, 전역 메트릭 저장, 세션 재생, Kafka, DB WAN 복제, HA/DR 완성, AI 자동 복구.
- 완료 기준: 독립 클러스터의 데이터 식별, 빌드/배포 실패와 런타임 오류 근거 조회, 로그↔트레이스 연결, HyperDX Prometheus 조회, 로컬 알림 독립성과 재시작·제한된 단절 복구를 검증한다. AWS 두 클러스터만 확인하면 멀티클러스터 결과로 보고하며 멀티클라우드 완료라고 표현하지 않는다.
- 후속 절차: 고정 chart/image/CRD, 파일 변경, 네트워크·Secret·권한·용량·비용·검증·rollback을 포함한 구현 계획을 독립 리뷰하고 사용자 승인을 받는다. 실제 변경과 자격 증명 작업은 명시적인 실행 승인 범위에서만 수행한다.
- 재검토 조건: 배포 불가·핵심 기능 미충족처럼 개발을 막는 사실이 발견될 때만 대안을 다시 논의한다. 비교를 위한 대안 스택은 만들지 않는다.

## 구현 구체화

공식 chart 3.5.0 조사에서 DB 활성 시 전체 existing Secret 대체가 불가능하고 기본 MONGO_URI가 ConfigMap에 포함되는 제약을 확인했다. [구현 계획](../plans/clickstack-implementation.md)은 공식 구조를 기준으로 Secret 참조를 보완한 로컬 chart와 별도 고정 operator chart를 제안한다. 기존 Secret controller 추가나 비밀번호 Git 주입으로 우회하지 않는다. 실제 호환성과 자원·권한은 로컬 검증 후 실행 승인 전에 확정한다.

## 관련 자료

- [아키텍처와 구현 순서 제안](../clickstack-architecture.md)
- [ClickStack 구성과 MongoDB 역할](https://clickhouse.com/docs/clickstack/architecture)
- [외부 Prometheus 연결](https://clickhouse.com/blog/whats-new-in-clickstack-june-2026)
- [최신 PromQL 지원 상태](https://clickhouse.com/blog/whats-new-in-clickstack-october-2026)
- [공식 Helm 구성](https://clickhouse.com/docs/clickstack/deployment/helm)
- [UI RBAC 범위](https://clickhouse.com/docs/clickstack/managing/rbac)
- [MCP 기능](https://clickhouse.com/docs/clickstack/mcp)
- [Grafana ClickHouse 데이터 소스](https://grafana.com/docs/plugins/grafana-clickhouse-datasource/latest/)

## A 구현 결과

사용자는 새 브랜치에서 A 구현·검증 진행을 승인했다. 실제 결과와 변경점은 [배포 전 검토](../clickstack-deployment-review.md)에 기록한다. 중앙은 공식 Collector standalone entrypoint를 직접 사용해 범용 subchart의 설정 덮어쓰기를 피했고, Secret 참조와 기존 수동 배포 경계를 유지했다. 최신 main의 GKE 앱·Tailscale TCP80 경로를 변경하거나 관측 연결 승인으로 해석하지 않았다.
