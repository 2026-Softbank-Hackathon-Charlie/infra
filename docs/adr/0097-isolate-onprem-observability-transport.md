# 0097. 온프레 관측 전송은 전용 태그와 기존 중앙 경로로 격리한다

- 상태: 채택(bootstrap 일부 적용, 수집기·전송·수신 미완료)
- 날짜: 2026-11-08

## 상황

사용자가 Langfuse·온프레 수집기·GPU 카나리의 실제 활성화를 요청했고 노트북을 다시 연결했다. 기존 노트북의 kube-system UID는 `83e340b0-a206-4cba-95d5-750354dced64`, 노드는 `desktop-c3itrvv`였다. 조회 시 Ready, allocatable CPU20/메모리7980932Ki/GPU1, 실제 메모리2320Mi(29%), nonterminal18개 Pod의 요청 합계 CPU1/메모리1996Mi였다. 기존 관측 수집기와 OTEL 앱 설정은 없었다.

## 결정

- ADR0092의 고정 Alloy/OTel/KPS 차트를 그대로 사용한다. 관리 중앙 OTLP auth Secret은 기존 키를 보존하고 `ONPREM_OTLP_AUTH_TOKEN`만 resourceVersion CAS로 추가한다. 로컬 CA365일·leaf90일·인증 Secret은 이 환경에 전용으로 생성하며 값은 출력·Git 저장하지 않는다.
- transport 차트에 onprem peer를 추가한다. onprem export→기존 중앙 TCP4318, management query→onprem query TCP443만 별도 태그·grant로 허용하는 정책 조각을 제공한다. 기존 중앙 ingress·GKE metrics Service/NetworkPolicy를 덮어쓰지 않는다. 중앙 query-only overlay는 기존 ingress 생성을 끈다.
- 정책 조각은 전체 tailnet 정책이 아니다. 관리자가 기존 tagOwners/grants에 보존 병합하고 기존 테스트를 유지해야 한다. 관측 태그를 발급할 기존 onprem operator의 identity는 `tag:hop-db-operator-onprem`이다. 기존 OAuth scope는 devices:core/auth_keys이고 policy 접근은 HTTP404였다. 따라서 현재 tagOwners/grants 내용을 읽거나 쓰지 못했고, 새 규칙이 이미 있는지도 단정하지 않는다. 전용 태그 발급·접속 성공을 실제로 확인하기 전에는 전송 연결 완료로 보지 않는다.
- 앱 등록·DB·AI 서비스의 기존 경로를 바꾸거나 GKE 태그를 온프레에 재사용하지 않는다. 공개 listener, TLS skip, 사용자 업무 로그 원문 저장은 추가하지 않는다.

## 검토한 대안

GKE 태그를 재사용하면 태그 이름과 실행 위치가 어긋나고 환경별 회수가 어렵다. 기존 중앙 ingress를 새 Application에서 다시 생성하면 소유권과 기존 수집 경로에 영향을 줄 수 있다. query-only overlay로 새 peer의 egress 자원만 추가하고 기존 중앙 수신은 공유한다.

## 결과와 실제 실행

명시 승인 범위에서 노트북에 observability namespace, monitoring CRD10개 Established, 인증/TLS Secret7개, 중앙 endpoint ConfigMap을 준비했다. management에는 query-client Secret1개와 중앙 auth의 onprem 키만 추가했다. 업무 namespace/DB/Tailscale/AI 경로는 변경하지 않았다. 43개 수집 자원의 전체 server dry-run이 성공했다(bundle SHA256 `e8e298efa1d3b96b07a926b36525ca2d562961d9b5b6b0be936d3ade3206b3f4`).

실제 `--execute`의 선행 재검사 중 노트북이 다시 offline이 됐다(Tailscale LastSeen 2026-11-08 02:30:00.1 UTC). 이 실행에서는 43개 collector 자원 apply에 도달하지 못했다. 수집기 Ready·실제 중앙 logs/traces/metrics·앱 SDK 계측·동등성 enforce는 아직 검증하지 않았다. `enabled` 기본 계약은 자동 설치가 아닌 준비용 상태를 유지한다.

기존 GKE 렌더8개 및 온프레 렌더4개 테스트가 통과했다. node-local queues·local-path PVC의 내구성 한계와 기존 ADR의 보존·회수 절차를 유지한다. 이후 재연결 시 Secret을 회전하지 않고 기존 자원을 확인한 뒤 UID 검증과 server dry-run부터 재개한다.
