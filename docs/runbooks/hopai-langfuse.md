# hopai Langfuse 계측 활성화 기록

- 날짜: 2026-11-08
- 상태: 실제 모델/도구 실행 성공, 전송 실패. client 활성화 미병합.
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
