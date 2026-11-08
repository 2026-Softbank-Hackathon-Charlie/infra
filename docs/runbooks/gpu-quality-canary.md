# GPU 품질 카나리 활성화

대상은 `hopcharlie-ai/ai-gateway`의 `moimroom-ai` 전용 native inference 경로다. 앱 `moimroom-mig`의 HTTP 이전이나 DB 전환과 별도다. 요청을 복제하거나 모델을 재호출하지 않는다.

## 준비

1. 노트북 Ollama와 GCP Ollama의 `/api/tags`에서 실제 artifact digest를 확인한다. source와 target의 고정 revision을 정책에 작성한다. 노트북이 단절되었거나 artifact를 모르면 멈춘다.
2. 앱 관측 프로젝트와 분리된 Langfuse 평가 프로젝트를 만든다. 신뢰 평가 생산자만 점수를 발행한다. `hopcharlie-ai/ai-gateway-quality-auth` Secret의 `public-key`, `secret-key`는 RAM으로만 다루며 렌더러 입력·로그·Git에 넣지 않는다. gateway 컨테이너의 UID/GID65534가 0440 mount를 읽을 수 있어야 한다. evaluator는 별도 `ai-quality-evaluator-auth` Secret을 사용한다. OSS에서 project key의 읽기 전용 role을 제공하지 않으면 gateway key도 프로젝트 쓰기 권한을 가진다는 한계를 기록한다. 키 두 개를 발급했다는 이유로 IAM 수준의 read/write 분리를 보장했다고 표현하지 않는다. gateway 구현은 score 조회 GET만 사용한다.
3. 실제 평가 프로젝트 키로 `/api/public/projects`가 정확히 해당 프로젝트 하나를 반환하는지 확인한다. `/api/public/v3/scores`를 지원하는지 확인한다. 지원하지 않는 API를 빈 성공으로 처리하지 않는다.
4. 검토한 20~100개의 item과 모델/프롬프트/검색/평가자 revision을 포함한 완전한 정책을 작성한다. 계약은 platform `docs/llm-quality-canary.md`다. 원문·비밀값은 정책에 포함하지 않는다. safety는 minimum1, maxRegression0, hardFailure true이며 업무 품질 지표를 함께 둔다.

## 렌더·적용

```powershell
python scripts/render-gpu-canary-activation.py --policy-file <검토한-정책.json> --output <새-활성화.yaml>
```

이 명령은 API에 접근하지 않는다. ConfigMap1·1Gi PVC1·Deployment1·NetworkPolicy2만 출력한다. Secret·Service·IAM·RBAC는 만들지 않는다. 현재 management values의 검증된 immutable platform image를 사용한다.

운영 적용 전 확인한다:

- `/v1/state` writeBackend=gcp, 쓰기 hold=false, active inference0. 기존 follower 상태를 기록한다.
- 기존 `ai-gateway-canary-state` PVC가 있으면 파일·policy digest·stage를 확인한다. 무조건 새 파일로 덮어쓰거나 이전 emptyDir 상태를 복사하지 않는다.
- Deployment image와 현재 정책 source revision을 검토한다. `kubectl apply --dry-run=server`가 통과해야 한다.
- 실제 적용 시 follower가 제거되고 replica1/Recreate로 재시작한다. 전용 AI endpoint에 짧은 재시작 중단이 생길 수 있다. DB와 메인 트래픽 gateway는 변경하지 않는다.

적용 뒤 Ready1, source 건강, `/v1/ai/canary` GET200, targetPercent0, effectiveTargetPercent0을 확인한다. 평가가 없는 상태의 `unknown`은 정상적인 차단이며 성공 품질 평가가 아니다. 상태 PVC와 mounted key의 접근·프로젝트 binding을 확인한다.

## 5%부터 검증

실제 source/target 추론을 각각 수행한 20개 이상 paired item의 subject를 별도 평가 프로젝트에 기록한다. 업무에 맞게 검토한 독립 evaluator가 safety와 업무 점수를 계산해야 한다. 합성 점수, HTTP200만으로 만든 score, source 출력 복사는 금지한다. 원문이나 토큰은 운영 증거 파일에 기록하지 않는다.

완전하고 최신인 평가가 pass이면 현재 version으로 `POST /v1/ai/canary/advance`와 고유 Idempotency-Key를 사용하여 5%만 요청한다. 이후에도 각 단계마다 새 평가와 관측 시간, 실제 응답 표본·지연·비용을 검토한다. 자동 승격하지 않는다. 대상이 offline이면 effective0을 유지한다. 점수 만료·조회 실패는 source-only, 확인된 품질 회귀는 AI targetPercent0으로 복구한다. DB rollback을 하지 않는다.

## 현재 검증 범위

2026-11-08 최초 조사에서 source GCP는 건강했으나 원래 노트북 Tailscale peer가 offline이었다. 따라서 target artifact, 실제 paired 평가, 5% 트래픽은 아직 검증하지 않았다. 렌더러와 회귀 테스트 통과를 운영 활성화로 표현하지 않는다. 실제 적용 결과는 별도 실행 기록에 남긴다.
