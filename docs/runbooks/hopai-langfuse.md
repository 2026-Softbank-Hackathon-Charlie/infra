# hopai Langfuse 계측 활성화 기록

- 날짜: 2026-11-08
- 상태: 실제 저장 검증 통과. 활성화 배포 결과는 아래에 기록.
- 결정: [ADR0087](../adr/0087-connect-hopai-langfuse-with-scoped-project-key.md)

## 적용 범위

ai-sandbox#50 계측, infra#160 한정 admission 정책, 전용 프로젝트 Secret 복사와 양쪽 selector/3000 정책은 승인 후 수행했다. VAP 양성1/음성4건을 확인했다. 새 API키·IAM·SSM정책·DB schema 변경은 없다. 최신 운영 aa8d4f4 이미지는 이미 계측 커밋 c7094f1과 후속 추천 기능을 포함하므로 유지한다. 별도 발행한 c7094f1 이미지는 검증에만 사용했다.

API는 Job YAML을 시작 때 캐시하며 기존 checksum/hopai 변경이 replicas1/Recreate 재시작을 유발한다. 사용자는 추가 상태집계 조회·활성화/재시작·접속 중단·마지막 검사와 자동 Argo sync 사이 새 업무 중단 위험 및 실패 시 GitOps revert를 승인했다. 새 API checksum은 추가하지 않는다.

## 검증 결과

초기 Job hopai-langfuse-check-1794093660은 모델HTTP2/tool1이 성공했지만 export 실패로 span0건이었다. 추가 무자격 진단 Job hopai-langfuse-net-1794102356도 정책 수정 전5초 timeout이었다. EKS PolicyEndpoint에는 Web Pod IP만 있었으며 Service VIP가 없었다. egress를 실제 Service selector app=web/name=langfuse/instance=langfuse와 맞추자 같은 Web/3000 대상에 Service VIP가 추가됐다. namespace/포트·네트워크 권한 범위를 늘리지 않았다.

추가 허용된 실제 검증 Job hopai-langfuse-recheck-1794103272 안에 고정 ready 요청1회를 선행해 HTTP200일 때만 Claude를 호출했다. init는 기존 Claude SSM키 하나만 읽고 kubeconfig/platform token을 읽지 않았다. 출력은 marker/status와 호출 건수뿐이다. 운영 이전·온보딩·리허설·외부PR 도구는 없고 메모리 진단 도구만 실행했다.

- ready HTTP200, Job exit0, 모델HTTP2/도구1, exporter 오류 없음.
- trace: `4b2c0d539ef6cb9f65aa6f2f4fff40e2`.
- API v2: AGENT1/GENERATION2/TOOL1, 실제 부모·Job·전체input/output 확인.
- 응답모델: claude-sonnet-5-5, input1526/output67 토큰.
- ClickHouse events_core FINAL4건; parent/model/usage와 API IO의 core미리보기 일치.
- Helm 계약: opt-out 시 env/정책 제거, 키는 hopai만 참조, 정확한 Web Service selector, 기존 API checksum이 image/opt-in/baseURL 변경에 반응하며 나머지 API spec은 불변.

첫 실패 후 진단1+모델1 Job만 추가했다. 모델 cap은 추가4HTTP/각1024출력토큰이었고 실제2HTTP를 사용했다. 로컬 DB/합성 컨테이너/노트북 전체 Go compile은 실행하지 않았다.

## 활성화·복구 관문

PG charlie에서 BEGIN READ ONLY/statement_timeout5s로 상태별 count만 조회한다. runs는 succeeded/failed/aborted/halted/interrupted/rolled_back, deployments는 healthy/failed/superseded, onboardings.state는 done만 비활성으로 취급하고 다른 값/NULL/오류는 차단한다. charlie-platform의 part-of=hopcharlie/component=hopai Job 중 Complete=True/Failed=True가 없는 Pending 포함 전부 차단한다. 실제 payload·사용자·token은 조회하지 않는다.

검증 후 병합 직전에 다시 집계한다. 활성/unknown/조회 실패가 있으면 병합하지 않는다. 이후 자동 sync까지 새 업무 발생은 차단하지 못한다. 배포 후 Argo revision, Job ConfigMap env/image, API observed generation·checksum·Ready·endpoint를 확인한다. 오류면 활성화 커밋을 GitOps revert하고 다시 Ready를 확인한다. 기존 실행 중 Job/Secret/trace는 삭제하지 않는다. 검증 Job은 TTL로 정리한다.
