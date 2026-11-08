# hopai Langfuse 계측 활성화 기록

- 날짜: 2026-11-08
- 상태: 최초 전송 실패 뒤 최신 실행기로 재검증 성공. client 활성화 배포 준비.
- 결정: [ADR0087](../adr/0087-connect-hopai-langfuse-with-scoped-project-key.md)

## 승인된 범위와 수행 결과

ai-sandbox#50 계측 구현·CI·main 병합과 Linux release artifact를 발행했다. infra#160의 한정 admission 정책을 먼저 배포했고, 플랫폼 SA server dry-run에서 양성1/음성4건을 확인했다. 신규 키·IAM·SSM 변경 없이 기존 프로젝트 키를 전용 Secret에 복사하고 selector 제한 3000 정책2개를 적용했다.

새 이미지: `525091405886.dkr.ecr.ap-northeast-2.amazonaws.com/hopcharlie/hopai:langfuse-c7094f1a967c@sha256:d047328d955e61fa1cb43f42b8291858bfe0d0b172dd9269a6708763a3aeb73c`.

Job `hopai-langfuse-check-1794093660`은 동일 llm.run/Anthropic SDK/loggedTool 경로에서 실제 모델 HTTP2회·도구1회 후 성공했다. 도구는 메모리 표식만 읽었으며 이전/온보딩/리허설/외부PR를 수행하지 않았다. exporter 오류가 있어 해당 Job의 API v2 observation0건/ClickHouse0건을 확인했다. 재실행하지 않았고 새 유료 호출은 승인 대기다. 기존 동일 합성 trace 재전송은 약0.2초/200이며, Pod 전송 경로가 정상이라는 증거는 아니다.

## 미완료와 다음 관문

1. 추가 읽기 전용 네트워크 진단 Job1개(모델/SSM/프로젝트 키 접근 없음)로 실제 hopai Pod→Langfuse ready 경로의 DNS/HTTP 상태를 확인한다. 필요시 동일 namespace/selector/port 범위 안에서만 원인을 고친다. 원격 Web 로그/정책 확장/새 인프라는 별도 승인 없이 하지 않는다.
2. 진단 성공 후 동일 제한의 실제 모델 검증 Job1개(추가 HTTP최대4·각1024출력토큰·180초)로 재검증한다. 키는 기존 한정 SSM 및 전용 프로젝트 Secret만 사용한다. trace 전부의 타입/부모/IO/응답모델/usage가 API/CH에 저장되어야 성공이다. 현재 원인은 미확정이며 전송 timeout 조정을 임의 적용하지 않는다.
3. API checksum 재로딩은 별도 추가 승인 후 구현한다. READ ONLY/statement_timeout5s 상태 집계에서 runs의 succeeded/failed/aborted/halted/interrupted/rolled_back, deployments의 healthy/failed/superseded, onboardings.state=done 외 상태는 차단한다. charlie-platform의 part-of=hopcharlie/component=hopai Job은 Complete/Failed 조건이 없는 Pending 포함 모두 차단한다. 오류/NULL/unknown도 차단한다.
4. 병합 직전 다시 확인하고 차단되면 병합하지 않는다. 이후 자동 Argo sync까지 새 업무 발생을 막지는 못하며 API Recreate가 새 업무를 중단할 잔여 경합을 사용자에게 명시한다. 중단 시간은 PVC/스케줄링/init/startup에 따라 길어질 수 있다.
5. 렌더에서 checksum 변화/기존 annotation 공존/API image·replicas·Recreate·PVC·PG·gateway 불변을 확인한다. 배포 후 Argo revision, ConfigMap image/env, API observed generation·새 Pod checksum·Ready·endpoint를 확인한다. 오류면 GitOps revert로 복구하며 재시작이 다시 발생한다. 실제 업무 Job을 검증 목적으로 실행하지 않는다.

추가 진단/모델 Job과 DB 집계/API 재로딩은 현재 미승인·미실행이다. 임시 검증 Job은 TTL로 정리하고 기존 Secret/trace/실행 중 업무는 삭제하지 않는다. 로컬 DB·합성 컨테이너·노트북 전체 Go compile은 실행하지 않았다.

## 후속 활성화 요청과 실제 재검증

2026-11-08 사용자가 Langfuse 관측 활성화를 명시적으로 요청했다. 앞 절은 최초 실패 시점의 기록이며 아래 결과가 후속 검증이다. 실행기 이미지는 추천 기능이 포함된 현재 운영 `aa8d4f4@sha256:db870c26b9564a231634bdea5f6b7ced7f860900f85b6b947239be0a9b19b87b`을 유지한다. 이전 `c7094f1` 이미지로 되돌리지 않는다. API의 기존 `checksum/hopai`가 template 변경을 반영하며 API는 여전히 1개/Recreate이므로 배포 전 실행 중 업무를 확인한다.

비밀·SSM·모델을 사용하지 않는 진단 Job `hopai-langfuse-net-1794102690`에서 DNS, Service HTTP GET, 직접 Pod IP HTTP GET이 모두 exit0이었다. 앞선 nc 기반 진단 실패만으로 네트워크 단절이나 exporter timeout 원인을 확정하지 않는다. 전송 timeout이나 네트워크 범위는 확대하지 않았다.

최신 이미지의 `hopai-langfuse-check-1794102725`는 동일 실제 agent/SDK 경로에서 모델 HTTP2회, 메모리 도구1회를 수행했다. Langfuse API v2에서 이 Job의 정확한 session/metadata로 고른 4개 관측(AGENT1, GENERATION2, TOOL1)이 저장됐다. 같은 trace의 AGENT가 나머지 세 관측의 부모다. 네 관측의 input/output 존재와 두 GENERATION의 실제 `claude-sonnet-5-5` 모델·usage를 확인했다. API v2 기본 응답은 model/usage/IO를 생략하므로 검증 조회에서 해당 fields를 명시했다. 비밀·원문 입출력은 결과 기록에 저장하지 않았다.

이는 hopai 관측 검증이다. `moimroom-mig` 앱의 실제 추론을 관측했다고 의미하지 않으며 해당 앱 generation을 위조하거나 hopai 관측에 앱 라벨을 붙이지 않는다. 앱의 Langfuse 연동은 정확한 앱 metadata를 발행하는 추론 계측과 별도 서버 설정이 필요하다. GPU 품질 평가는 관측 프로젝트와 분리된 전용 프로젝트·키를 사용한다.

## Service VIP 보완과 별도 실제 저장 대조

2026-11-08 추가 승인 범위에서 PolicyEndpoint를 확인했을 때 hopai egress는 Web Pod IP만 포함했다. 동일 namespace/Web/3000 대상의 selector를 실제 Service의 app=web/name=langfuse/instance=langfuse와 맞추자 Pod IP와 Service VIP가 함께 생성됐다. DNS/Pod 직접 접속의 다른 성공 기록도 있으므로 VIP 누락만으로 최초 exporter 오류의 유일 원인이라고 단정하지 않는다. SDK timeout/retry·namespace 범위·IAM/RBAC는 바꾸지 않았다. 이 보정은 API checksum이나 Pod spec을 바꾸지 않는다.

추가 승인된 재검증 Job hopai-langfuse-recheck-1794103272는 동일 고정 ready 요청1회가200일 때만 모델을 호출했다. 실제HTTP2/메모리도구1/exit0이고 exporter 오류는 없었다. trace 4b2c0d539ef6cb9f65aa6f2f4fff40e2는 API v2에서 AGENT1/GENERATION2/TOOL1, parent/Job연결·전체IO·실제claude-sonnet-5-5·input1526/output67 토큰을 확인했다. ClickHouse events_core FINAL4건과 parent/model/usage 및 IO미리보기를 대조했다. core의 IO는 미리보기이며 전체IO는 API로 확인했다. 이 추가 검증은 c7094f1 검증용 이미지이며 현재 운영 aa8d4f4 이미지의 별도 성공 기록을 대체하지 않는다.

PR170이 이미 main에 병합·Argo배포되고 API observed generation100/Ready1·Job env·현재aa8d4f4image·기존checksum/hopai·Service endpoint가 정상임을 확인했다. 추가 API 재시작은 하지 않았다. 재점검에서 nonterminal hopai Job1개를 확인했으며 중단/삭제/재실행하지 않았다. 이번 후속 수정은 같은 Web Service 정책과 문서만 반영한다.
