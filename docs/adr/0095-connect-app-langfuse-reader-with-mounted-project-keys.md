# 0095. 앱 Langfuse 조회는 API에만 전용 프로젝트 키 파일을 연결한다

- 상태: 채택 — 사용자의 관측 활성화 요청 범위, 실제 API 적용은 별도 실행 결과로 확인
- 날짜: 2026-11-08

## 상황

플랫폼의 앱별 LLM 관측 API는 `PLATFORM_LANGFUSE_CONFIG`와 mounted key 파일을 읽을 수 있지만 운영 차트에 연결이 없어 `langfuse_not_configured`였다. Hopai 관측과 GPU 품질 평가의 프로젝트를 앱 추론 기록으로 재사용하면 출처가 섞인다. `moimroom-mig` 전용 프로젝트와 기존 Secret `charlie-platform/moimroom-mig-langfuse-reader`는 별도로 준비되었다.

## 결정

실제 중앙 수집 경로에서 Pod selector만으로는 EKS Service VIP 연결이 시간 초과하는 것을 확인했다. reader는 같은 문제를 피하도록 기존 정확한 Pod selector에 운영자가 확인한 `langfuse-web`의 Service IP 한 개 `/32`, TCP3000만 보완한다. 현재 관리 서비스 CIDR `172.20.0.0/16`의 유효한 host IPv4만 렌더링하며 public IP나 넓은 대역을 받지 않는다. Service UID·IP·selector·포트와 reader 값의 일치는 실제 적용 직전 확인한다. 이 수동 확인은 admission enforcement가 아니며 Service 재생성과 check-to-sync 경합을 완전히 막지 못한다. API는 이미 기존 egress 정책으로 격리되어 있어 이 추가 정책은 기존 DNS·DB 경로를 좁히지 않는다.

기본 off인 `api.langfuse`를 추가하고 관리 환경의 `moimroom-mig`만 opt-in한다. 공개 ConfigMap에는 고정 내부 Langfuse 주소, 정확한 앱·프로젝트·metadata binding과 절대 파일 경로만 넣는다. 앱명과 프로젝트/metadata 식별자를 검증하고 key 원문·사용자 파일 경로·다른 프로젝트 Secret 참조는 렌더 단계에서 거부한다. Secret 이름은 `<app>-langfuse-reader`이며 API의 UID/GID10001만 읽도록 기존 fsGroup과 0440·readOnly mount를 사용한다.

키는 API main container에만 제공한다. init container, gateway, console, hopai Job에는 이 앱 키나 reader 설정을 전달하지 않는다. 이 변경은 Secret·IAM·RBAC·모델 호출을 만들지 않는다. API의 egress는 Langfuse namespace의 정확한 web Pod selector와 TCP3000만 추가한다. 목적지 ingress는 별도 중앙 수집기 연결 변경에서 같은 두 source를 명시한다.

API는 시작 시 설정과 파일을 읽으므로 공개 binding의 `checksum/langfuse`로 API만 재시작한다. 기존 parity/hopai/onprem checksum, 이미지·replica1·Recreate·PVC·DB·gateway 설정은 유지한다. 키 원문을 checksum 계산에 읽지 않으므로 Secret 회전 뒤에는 별도 검증·API 재시작이 필요하다.

Secret 파일의 읽기 전용 mount가 Langfuse API key 자체의 읽기 전용 권한을 뜻하지는 않는다. 현재 OSS 배포에서 project key의 세분화한 readonly role은 확인되지 않아 이 전용 키도 해당 프로젝트의 쓰기 권한을 가질 수 있다. 플랫폼 reader 구현은 고정 GET만 사용하고 프로젝트 범위를 별도로 검증한다. 앱·hopai·품질 평가 프로젝트는 분리한다.

## 검토한 대안

- Hopai 키 공유: 앱 기록과 플랫폼 에이전트 기록의 소유권이 섞여 거부했다.
- 브라우저가 Langfuse를 직접 조회: 프로젝트 키 노출과 앱 범위 통제를 피할 수 없어 서버만 조회한다.
- API 환경변수에 키 원문 삽입: mounted 파일을 사용하고 ConfigMap·Git·일반 Pod env에 원문을 남기지 않는다.

## 결과

렌더 경계 테스트는 default off, 공개 JSON 계약, API-only mount, key/path 거부, 한정 egress, 설정 변경 시 API-only checksum과 기존 workload 불변을 확인한다. 이는 운영 Langfuse 수신의 증거가 아니다.

main 병합은 자동 Argo sync로 API Recreate를 일으킨다. 실행 중인 이전·배포·온보딩·hopai Job이 없는지 직전 다시 확인하고, 새 업무가 시작될 수 있는 확인-동기화 사이의 경합을 고려한다. API가 Ready가 된 뒤 config/mount 접근·프로젝트 binding·관측 API를 확인한다. 오류 시 reader 활성화만 GitOps로 되돌려 API를 복구하며 같은 재시작 영향이 있다.

앱이 offline이거나 계측 이미지·중앙 exporter가 아직 연결되지 않았다면 결과는 configured/unobserved일 수 있다. 이를 AI 미사용·추론 0건 또는 관측 동등성 충족으로 표현하지 않는다. 실제 앱 generation 수신은 별도 실행 증거로 기록한다.

관련: [앱 reader 운영 절차](../runbooks/app-langfuse-reader.md), platform ADR0055, moimroom-mig ADR0010.
