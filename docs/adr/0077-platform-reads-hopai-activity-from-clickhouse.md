# 0077. 플랫폼 API가 hopai 활동을 관측 ClickHouse에서 읽는다

- 날짜: 2026-11-07
- 상태: 제안
- 근거: 사용자가 콘솔 이주 지도에 hopai의 실제 호출을 보이도록 HyperDX 데이터를 쓰라고 요청했다.

## 배경과 제약

이주 지도는 단계별로 고정된 문구로 AI 활동을 그린다. 실제로 hopai가 어떤 모델을 몇 번 호출했고
어떤 읽기 도구를 요청했는지는 관측 스택(ClickStack, HyperDX)의 ClickHouse `default.otel_logs`에
서비스 `charlie-platform-hopai`로 쌓여 있다. 브라우저의 콘솔이 ClickHouse에 직접 닿을 수 없고
HyperDX를 공개하지 않았다(ClusterIP만, `docs/clickstack-architecture.md`).

ClickHouse에는 이미 읽기 전용 사용자 `app`이 있다(`helm/charts/clickstack/values.yaml`:
profile `hyperdx-read`, `GRANT SELECT ON default.*`, `system.*`). HyperDX가 이 사용자로 읽는다.

## 결정과 이유

platform API(`GET /v1/ai-activity`, platform PR #117)가 ClickHouse HTTP(8123)를 읽고, hopai 로그 줄을
파싱한 필드만 콘솔에 돌려준다. 원문은 돌려주지 않는다.

- 사용자: 기존 읽기 전용 `app`을 쓴다. 새 사용자를 만들면 ClickHouse 사용자 설정(`extraUsersConfig`)이
  바뀌고 ClickHouse가 재설정되는데, 시연 직전에 관측 저장소를 건드리는 위험이 이득보다 크다고 판단했다.
- 비밀번호: Kubernetes Secret을 새로 만들지 않고, 이 저장소의 다른 비밀처럼 SSM SecureString
  `/hopcharlie/clickhouse-read-password`에 두고 API의 init container가 메모리 볼륨(`/config`)으로 받는다.
  `scripts/put-clickhouse-read-password.sh`가 관측 Secret에서 SSM으로 값을 옮기며 값은 출력하거나 명령줄에 두지 않는다.
  사용자가 직접 실행한다.
- 네트워크: `hopcharlie-aiobs-egress`가 API Pod에서 `observability` 네임스페이스의 ClickHouse Pod(
  `app.kubernetes.io/name=clickhouse-server`) 8123으로만 나가는 길을 연다. observability 네임스페이스에는
  게이트웨이 ingress 정책 하나뿐이어서 ClickHouse 쪽 ingress는 이미 열려 있다(2026-11-07 조회).
- 켜기: `api.aiobs.enabled`(기본 꺼짐)와 `url`, `user`, `table`. 관리 환경 values에서만 켠다.
  SSM 값이 없으면 init container는 실패하지 않고 빈 비밀번호 파일을 만들고, 엔드포인트가 502를 답한다.

## 검토한 대안

- 콘솔이 HyperDX API를 직접 호출: HyperDX 공개와 사용자별 API 키가 필요해 채택하지 않는다.
- 새 읽기 전용 ClickHouse 사용자: 권한 분리는 더 낫지만 ClickHouse 설정 변경 위험 때문에 후속으로 미룬다.
- 플랫폼의 기존 에이전트 세션 이벤트(`/v1/agent/sessions/{id}/events`) 사용: 인프라 변경이 없는 더 단순한
  길이지만, 모델 호출의 토큰과 모델 이름이 거기에 들어 있는지 확인하지 못했다(미확인).

## 영향과 위험

- `api.aiobs.enabled`를 켜면 API Deployment가 바뀌어 Recreate 전략으로 재시작된다. 그 동안 관리 API가 잠시 끊긴다.
  실행 중인 이전이 없을 때 적용한다.
- `app`을 platform과 HyperDX가 같이 쓴다. 한쪽의 비밀번호를 바꾸면(관측 Secret의 `read-password`) 이 SSM 값도 다시 넣어야 한다.
  `app`의 profile은 `readonly: 2`라 요청에서 `readonly` 설정을 바꿀 수 없다(platform은 이를 보내지 않는다).
- 로그 줄 문구는 hopai(ai-sandbox)가 정하고 platform의 파서가 그 형식에 기댄다. 문구가 바뀌면 줄이 조용히 버려진다.
- 미확인: API Pod에서 ClickHouse 8123에 지금 실제로 닿는지, `default.otel_logs`의 열 이름이 기본 스키마와 같은지.
  Helm 렌더링은 이 환경에 helm이 없어 하지 않았다.

## 후속 작업

- 사용자가 `scripts/put-clickhouse-read-password.sh`를 실행하고 API를 재시작한 뒤 `GET /v1/ai-activity`가 200을 주는지 확인한다.
- 전용 읽기 전용 사용자로 분리할지 결정한다.
- 콘솔 이주 지도를 이 엔드포인트에 연결한다(frontend PR).

## 관련 파일

- [helm/charts/hopcharlie-platform/templates/api.yaml](../../helm/charts/hopcharlie-platform/templates/api.yaml)
- [helm/charts/hopcharlie-platform/templates/network.yaml](../../helm/charts/hopcharlie-platform/templates/network.yaml)
- [helm/charts/hopcharlie-platform/templates/settings.yaml](../../helm/charts/hopcharlie-platform/templates/settings.yaml)
- [clusters/aws-dev-management/values/hopcharlie-platform.yaml](../../clusters/aws-dev-management/values/hopcharlie-platform.yaml)
- [scripts/put-clickhouse-read-password.sh](../../scripts/put-clickhouse-read-password.sh)
