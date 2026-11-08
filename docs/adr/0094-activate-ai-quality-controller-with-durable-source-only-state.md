# 0094. 전용 AI 카나리는 평가 프로젝트와 영속 상태를 연결한 원본 100%에서 활성화한다

- 상태: 채택 — 활성화 요청을 받음. 노트북 단절과 평가 프로젝트 선행 조건으로 실제 적용은 아직 하지 않음
- 날짜: 2026-11-08
- 관련: platform ADR 0056, infra [0091](0091-opt-in-same-node-gateway-pod-failover.md)

## 상황

`hopcharlie-ai/ai-gateway`는 수동 관리하는 별도 Deployment다. 현재 이미지 `52c4508`에는 품질 통제가 없고, `moimroom-mig`의 위치를 따라가는 `-ai-follow-*`와 emptyDir 상태를 사용한다. 품질 카나리는 고정 source `gcp`를 요구하며 follower와 함께 시작할 수 없다. emptyDir에서는 재배포 때 카나리 stage와 CAS 이력이 유실된다.

2026-11-08 10:51 KST 실제 조회에서 source는 `gcp` 100%, in-flight 0, hold false였고, 노트북 연결 실패로 follower가 GCP로 대체 중이었다. GCP Ollama는 `qwen2.5:1.5b-instruct-q4_K_M`, artifact digest `65ec06548149b04c096a120e4a6da9d4017ea809c91734ea5631e89f96ddc57b`였다. 이는 대상 노트북에 같은 artifact가 있다는 증거가 아니다.

## 결정

별도 렌더러는 완전한 운영자 정책 없이는 결과물을 만들지 않는다. 두 모델의 SHA256 고정 revision, 검토한 데이터셋·프롬프트·평가자 revision, 서로 다른 20~100개 item, 필수 safety hard gate와 업무 지표, 별도 평가 프로젝트를 요구한다. 렌더러는 자격증명을 받지 않고 고정된 Secret 파일 참조만 만든다.

활성화는 관리 환경에서 이미 검증한 플랫폼 이미지의 gateway binary, follower 제거, source `gcp`, 읽기 전용 정책·키 mount, source-only로 시작하는 품질 controller, Langfuse web TCP3000 전용 NetworkPolicy로 한정한다. 기존 inference Service나 Tailscale ExternalName Service를 재적용하지 않는다.

카나리 상태는 새 `ai-gateway-canary-state` 1Gi gp3 RWO PVC에 저장한다. replica 1·Recreate를 유지한다. 최초 전환 전에 기존 source, hold, active inference, 기존 persisted state를 확인하고 12초 inference 종료 시간을 둔다. 아직 존재하지 않는 정책의 stage를 이전 emptyDir에서 복사하거나 기존 state 파일을 덮어쓰지 않는다. 이후 설정과 저장된 policy digest가 달라지면 gateway가 시작을 거부하는 계약을 유지한다.

평가 전용 프로젝트는 앱 관측·hopai 프로젝트와 분리한다. 실제 trace/observation에 연결한 신뢰 생산자의 완전한 최신 paired batch가 없으면 `unknown`, effective target 0%다. 5%는 실제 평가와 대상 건강 상태를 확인한 뒤 관리 API로 한 단계만 승인한다. 이 렌더러는 평가·모델 호출·트래픽 증가를 수행하지 않는다.

## 검토한 대안

- follower와 카나리 동시 활성화: 품질 gate를 우회해 writeBackend가 움직이므로 거부한다.
- 앱 관측 키·프로젝트를 평가용으로 공유: 앱이 품질 승인 증거를 작성할 수 있으므로 거부한다.
- 기존 emptyDir 유지: 재배포 시 stage/CAS 이력이 사라지므로 전용 PVC를 사용한다.
- 연결되지 않은 노트북 대신 GCE를 온프레로 간주: 사용자가 선택한 대상과 달라 거부한다.

## 결과

준비 결과와 실제 운영 활성화를 분리한다. 렌더러의 성공은 모델 품질, 키의 프로젝트 소유권, 실제 span 저장이나 노트북 접속을 증명하지 않는다. `--dry-run=server`, 키의 `/projects` 결과, 대상 artifact·health, 기존 route/active state, 실제 `/v1/ai/canary` 응답을 추가 확인해야 한다.

새 compute/GPU나 유료 judge 호출은 없다. 적용하면 gp3 저장소 1Gi 비용이 추가된다. [AWS 공식 gp3 단가 예시](https://aws.amazon.com/ebs/pricing/)를 참고해 월 USD0.1 수준의 예산을 두되 정확한 서울 리전 청구액은 미측정이다. 중단 시 먼저 카나리를 0%로 내리고 관측한다. PVC에 품질 stage가 생긴 뒤에는 예전 binary나 follower를 단순 복원하지 않는다. PVC 제거는 state 보존 여부를 확인한 별도 운영 작업이며 렌더러가 자동 삭제하지 않는다.

관련 파일: [렌더러](../../scripts/render-gpu-canary-activation.py), [검증](../../scripts/tests/test-gpu-canary-activation.py), [운영 절차](../runbooks/gpu-quality-canary.md).
