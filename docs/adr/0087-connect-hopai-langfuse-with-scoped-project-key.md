# 0087. hopai의 실제 LLM·도구 실행을 전용 Langfuse 프로젝트로 전송한다

- 날짜: 2026-11-08
- 상태: 채택 — 초기 및 추가 실행 승인을 받음. 실제 저장 검증 통과, 클라이언트 활성화 배포는 확인 후 기록
- 관련 ADR: [0086 전용 cloud 소유권](0086-persist-langfuse-cloud-resource-ownership.md), [0082 Job 권한](0082-onboard-existing-compose-apps-infra.md)

## 배경과 제약

Langfuse에 합성 trace를 보내 저장·조회했으나 실제 hopai 작업의 자동 기록 연결은 없었다. ai-sandbox의 llm.run과 loggedTool, Anthropic SDK middleware에 전용 OTel provider를 연결한다. 기존 플랫폼 agentlog·터미널/ClickStack 기록과 전역 OTel 설정은 유지한다. 계측 실패는 업무 결과를 바꾸지 않는다.

## 검토한 대안

- agentlog 출력만 변환: 실제 Claude 요청 input과 개별 HTTP 시간·usage를 충분히 보존하지 못한다.
- 전역 OTel exporter 교체: 기존 ClickStack 수집에 영향을 주므로 제외.
- 전용 provider와 명시적 Job Secretrefs: 기존 실행 경로를 자동 관찰하고 한 프로젝트 키만 넘길 수 있어 선택.

## 결정과 이유

기존 Langfuse 프로젝트의 키를 charlie-platform/hopcharlie-langfuse-auth의 public-key/secret-key로 복사한다. 키 원문은 source·values·로그·URL에 넣지 않는다. 신규 API키·IAM·SSM 정책은 발급/변경하지 않는다. 앞으로 생기는 hopai 컨테이너에만 키 env와 LANGFUSE_BASE_URL, Job 이름을 제공한다.

기존 admission은 모든 valueFrom을 막으므로 관리 환경의 명시 opt-in에서만 정확 예외를 둔다. hopai 컨테이너의 LANGFUSE_PUBLIC_KEY/public-key, LANGFUSE_SECRET_KEY/secret-key는 위 Secret만 허용하고 HOPCHARLIE_RUN_ID는 metadata.labels['job-name']만 허용한다. envFrom·다른 Secret/fieldRef/컨테이너·Secretvolume는 계속 거부한다. namespace·주체·SA·이미지·기타 기존 제한은 유지한다. 정책을 먼저 main/Argo로 적용하고, platform SA의 server dry-run으로 실제 허용·해당 VAP의 거부를 확인한 뒤 클라이언트를 활성화한다.

hopai→Langfuse3000 egress는 HopCharlie chart가 관리한다. Langfuse ingress는 이 저장소의 독립 manifest를 기존 수동 지원 정책과 같은 방식으로 적용한다. 기존 Argo Project에 langfuse namespace를 추가하거나 RBAC를 확대하지 않는다. 실제 image는 CI에서 검증한 main Linux binary로 기존 image의 실행 파일만 바꾸고 digest로 고정한다.

## 영향과 후속 작업

실제 prompt·응답·도구 결과는 기존 mask와 알려진 키 제거 후 최대64KiB만 기록하며 잘린 JSON은 생략하고 truncated를 남긴다. AGENT/GENERATION/TOOL의 부모관계·모델·응답usage·시간·상태를 기록한다. exporter1초·root종료flush2초, 장애 시 fail-open이다. 강제 종료/전송 장애의 유실이 없다고 보장하지 않는다.

검증 Job1개는 같은 앱 llm.run/SDK를 사용하는 읽기 전용 telemetry-check로 실제 Claude HTTP최대4회·각1024출력토큰·180초 제한이다. 운영 이전·리허설·온보딩/외부PR는 실행하지 않는다. 도구를 실제 호출하지 않았다면 TOOL 실환경 검증을 주장하지 않는다. 기존 Job은 재실행하지 않는다. 롤백은 client opt-in off/기존image이며 새 Secret/trace는 자동 삭제·회전하지 않는다.

## 관련 파일

- [admission policy](../../helm/charts/hopcharlie-apps/templates/ai-runner-policy.yaml)
- [Langfuse ingress](../../clusters/aws-dev-management/manifests/langfuse-hopai-ingress.yaml)
- [Job template](../../helm/charts/hopcharlie-platform/templates/hopai.yaml)
- [ai-sandbox](https://github.com/2026-Softbank-Hackathon-Charlie/ai-sandbox)

## 2026-11-08 적용·검증 상태

정책 PR infra#160과 계측 PR ai-sandbox#50을 main에 반영했다. 플랫폼 SA의 live server dry-run에서 전용 Secretrefs/Job 이름은 허용하고 다른 Secret·key·fieldRef·envFrom은 해당 VAP가 거부했다. CI workflow_dispatch 42390883630의 c7094f1a967c050116a2037d3bbd607e011b1874 Linux 바이너리를 기존 고정 이미지의 실행 파일만 교체해 발행했다.

승인된 범위에서 기존 프로젝트 키를 전용 client Secret으로 복사하고 양쪽 3000 정책을 적용했다. 검증 Job hopai-langfuse-check-1794093660은 exit0, 실제 모델 HTTP2회·메모리 도구1회를 실행했다. 그러나 exporter 오류가 발생했고 이 Job의 span은 API v2 및 ClickHouse에서 0건이었다. 따라서 실제 자동 수집 검증은 실패이며 활성화 PR을 병합하지 않는다. 빈 JSON/protobuf OTLP 요청은200, 기존 동일 합성 trace의 재전송은200/약0.2초였다. 이것으로 hopai Pod의 전송 성공을 주장하지 않는다.

Job image/env/egress 활성화안은 별도 브랜치에서 렌더 검증했으며 미병합이다. platform은 Job YAML을 시작할 때만 캐시하고 API는 replica1/Recreate이므로 ConfigMap 변경만으로 다음 Job이 갱신되지 않는다. API checksum 재로딩·상태 집계 조회·중단/잔여 경합 수용은 추가 계획으로 독립 리뷰를 통과했지만 사용자 추가 승인 전이며 구현/실행하지 않았다. 이 제안은 기존 승인의 일부라고 간주하지 않는다.

추가 운영 검증·승인 범위와 증거는 [운영 기록](../runbooks/hopai-langfuse.md)에 남긴다. 실제 모델 응답의 model/usage/입출력/부모관계는 저장되지 않았으므로 확인했다고 기록하지 않는다.

## 연결 실패 수정과 추가 승인 후 검증

사용자가 추가 진단1/모델 검증1 Job, 기존 PG의 읽기 전용 상태 집계, 활성 작업이 없을 때 API 재로딩을 포함한 활성화 및 실패 시 GitOps revert를 승인했다. 짧은 API 중단과 마지막 상태 조회부터 Argo sync까지 새 업무가 시작돼 중단될 잔여 경합을 명시했다.

최초 egress는 Web Pod 라벨 component=web을 사용했지만 Langfuse Service selector는 app=web이었다. EKS의 파생 PolicyEndpoint에 Pod IP만 있고 Service VIP가 없었다. 동일 namespace/Web/3000 범위에서 Service selector(app=web/name=langfuse/instance=langfuse)로 맞춘 뒤 Pod IP와 Service VIP가 함께 생성되는 것을 확인했다. cluster-wide 정책이나 IAM/RBAC는 바꾸지 않았다.

추가 진단 Job은 정책 수정 전5초 timeout으로 실패했다. 허용된 실제 재검증 Job 하나에 동일 고정 ready HTTP 단일 요청을 선행시켜200일 때만 모델을 호출했다. 모델/SDK의 exporter timeout·retry는 바꾸지 않았다. Job hopai-langfuse-recheck-1794103272는 ready200·모델HTTP2·메모리TOOL1·exit0이었다. trace 4b2c0d539ef6cb9f65aa6f2f4fff40e2에서 API v2 AGENT1/GENERATION2/TOOL1과 부모/Job 연결·전체입출력·응답모델claude-sonnet-5-5·input1526/output67 토큰을 확인했다. ClickHouse events_core FINAL4건과 IO미리보기/부모/모델/usage를 대조했다. 전체IO는 API가 제공하며 ClickHousecore의200자 미리보기와 혼동하지 않는다.

최신 main은 계측 커밋의 후속 추천 기능을 포함하는 aa8d4f4 이미지를 이미 사용한다. 이를 그대로 유지해 c7094f1 검증용 이미지로 되돌리지 않는다. telemetry 패키지와 검증 명령은 두 revision에서 동일하다. 또한 API chart에 checksum/hopai가 이미 있어 새 checksum은 추가하지 않는다. image/opt-in/baseURL 변경 시 기존 checksum이 바뀌고 API image/Pod spec·replicas1/Recreate·다른 annotation은 유지되는 계약을 검증한다. 기존 checksum이 설정 캐시를 재로딩하므로 승인된 활성화 시 API 재시작이 발생한다.
