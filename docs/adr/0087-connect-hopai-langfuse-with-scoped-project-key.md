# 0087. hopai의 실제 LLM·도구 실행을 전용 Langfuse 프로젝트로 전송한다

- 날짜: 2026-11-08
- 상태: 채택 — 계획·구체적 실행 승인을 받음. 정책을 먼저 적용하고 클라이언트 활성화·실제 검증 결과는 별도 기록
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
