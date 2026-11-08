# 앱 Langfuse 조회 활성화

대상은 관리 클러스터 `charlie-platform/hopcharlie-api`와 앱 `moimroom-mig`다. 기존 전용 프로젝트 `cmw76sgdx000bzk08z3ez9xsq`의 reader Secret을 API에만 mount한다. 공개 JSON과 파일 경로는 차트가 생성한다. 키 값을 values·명령행·로그에 넣지 않는다.

## 적용 전

- `charlie-platform/moimroom-mig-langfuse-reader`에 `public-key`, `secret-key`가 있는지 값 출력 없이 확인한다. 실제 키의 `/api/public/projects` 결과가 앱 전용 프로젝트인지 RAM에서 검증한다.
- API→Langfuse web TCP3000 egress와 목적지의 동일 source ingress를 모두 검토한다. 앱·Hopai·GPU 평가 키를 서로 바꾸지 않는다.
- EKS에서 Service DNAT 전 정책 평가가 관측되어 Pod selector와 함께 현재 Service VIP `172.20.238.122/32`의 TCP3000만 허용한다. 공개 IP·서브넷·잘못된 IP는 렌더 단계에서 거부한다. 적용 직전 중앙 `check-langfuse-service-binding.py`로 `langfuse/langfuse-web`의 UID `89a4cb40-56b9-4ad3-858c-cdec8c20d494`, IP, selector, 포트를 확인하고 reader의 `api.langfuse.serviceClusterIP`가 그 IP와 같은지 다시 비교한다. Service 재생성 시 기존 허용값을 그대로 재사용하지 않는다. 확인과 적용 사이의 경합은 남는다.
- 공개 binding과 env `PLATFORM_LANGFUSE_CONFIG=/etc/langfuse/config.json`, mounted key 경로, image 불변을 로컬 Helm 렌더로 확인한다. `scripts/tests/test-app-langfuse-reader.py`는 키나 API를 읽지 않는다.
- 병합 직전 실행 중인 이전·배포·온보딩과 Pending/Running hopai Job이 없는지 확인한다. API replica1/Recreate 때문에 짧은 중단이 발생할 수 있다. 확인 후 Argo sync까지 새 업무가 시작될 수 있는 경합은 남는다.

## 적용 후

Argo revision/Synced/Healthy, API observed generation와 새 checksum, Ready1을 확인한다. API main container만 reader Secret에 접근하며 init/gateway/console/hopai에는 mount가 없어야 한다. 현재 OSS의 project key가 HTTP 쓰기 권한도 가질 수 있다는 한계와 파일 readOnly mount를 구분한다.

기존 운영자 인증으로 `GET /v1/apps/moimroom-mig/llm-observability`를 조회해 source reason을 확인한다. project 또는 버전 검증 오류는 미연동/조회 실패로 남기고 빈 성공으로 치환하지 않는다. generation이 없으면 unobserved로 표시한다. 모니터링 화면 연결과 실제 앱 LLM 기록 수신은 별도다.

실제 앱의 계측 이미지와 HTTPS OTel 경로, 중앙 collector의 앱 전용 Langfuse exporter가 연결된 뒤 실제 AI 안내 요청을 1회 수행한다. generation의 `hopcharlie.app=moimroom-mig`, 실제 모델, 제공된 토큰, 시간 범위를 API에서 다시 검증한다. 프롬프트·응답 본문·키·raw trace는 실행 기록에 남기지 않는다. 합성 mapping probe를 실제 앱 호출 수신의 증거로 사용하지 않는다.

## 복구·회전

오류 시 management values의 `api.langfuse.enabled=false`를 검토·병합하면 ConfigMap/mount/egress가 제거되고 API가 다시 시작한다. 앱·DB·gateway는 바꾸지 않는다. 기존 Secret이나 Langfuse 기록을 자동 삭제하지 않는다. Secret 키 회전 시 공개 ConfigMap checksum은 바뀌지 않으므로, 별도 승인 범위에서 새 키 binding 검증과 API 재시작을 수행해야 한다.
