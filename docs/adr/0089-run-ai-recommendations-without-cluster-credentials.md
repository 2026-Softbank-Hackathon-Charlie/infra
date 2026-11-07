# 0089. 이전 추천 Job은 클러스터 자격 증명을 가져오지 않는다

- 날짜: 2026-11-08
- 상태: 채택 — 구현 결정, 실제 운영 적용·AI 실행은 별도 확인
- 관련 ADR: [0082 실행기 분리](0082-onboard-existing-compose-apps-infra.md), [0087 Langfuse 연결](0087-connect-hopai-langfuse-with-scoped-project-key.md)

## 배경과 제약

개요의 이전 추천은 플랫폼이 모은 읽기 전용 근거를 같은 hopai 모델로 평가한다. 이전 계획·리허설·온보딩을 실행하지 않으므로 기존 fetch가 항상 가져오는 EKS/GKE kubeconfig가 필요 없다. 기존 runner의 ServiceAccount·IAM·네트워크 권한을 늘리지 않고 분기한다. 이 변경이 기존 역할의 kubeconfig 조회 권한 자체를 제거하는 것은 아니다.

## 검토한 대안

- 기존 `hopai run`을 사용: 계획·클러스터 접근을 포함하므로 읽기 전용 추천과 맞지 않는다.
- 별도 ServiceAccount·IAM·Secret·네트워크를 신설: 더 강한 격리가 가능하지만 별도 승인과 운영 작업이 필요하므로 이 슬라이스에는 포함하지 않는다.
- 같은 Job 템플릿에 전용 실행 분기와 credential fetch 생략: 기존 경계를 유지하고 필요한 키만 읽는 동작을 실제 shell 검사로 확인할 수 있어 선택한다.

## 결정과 이유

Job 자체의 `metadata.annotations['hopcharlie.dev/recommendations']='v1'`을 플랫폼의 지원 확인으로 사용한다. Pod annotation이 아니다. 플랫폼이 추천 실행의 init `fetch`와 main `hopai`에 모두 `KIND=recommend`를 넣고, main에 `HOP_RECOMMENDATION_ID`, `HOP_RECOMMENDATION_INPUT`, `HOP_RECOMMENDATION_NONCE`를 고정 입력으로 더한다. 플랫폼은 이 annotation이 없으면 추천 실행을 미연결로 처리한다.

추천 init은 기존 SSM의 Anthropic key와 agent token을 읽으며 kube-eks/kube-gke를 조회하거나 파일로 만들지 않는다. main은 env 파일을 기존 방식으로 읽은 뒤 온보딩 분기보다 먼저 `exec hopai recommend`로 실행한다. KIND가 없거나 onboard이면 기존 kubeconfig 조회와 run/onboard 명령을 유지한다. token 조회가 실패하거나 agent가 없으면 main을 실행하지 않는다. 입력·nonce·키는 로그로 내보내지 않는다.

## 영향과 후속 작업

- 실제 렌더된 fetch와 main shell을 fake AWS/hopai로 실행해 추천의 정확히 두 파라미터 조회·kube 파일 미생성·전용 명령, 기존 run/onboard 동작, 실패 차단과 값 로그 부재를 확인한다.
- IAM·RBAC·Secret·네트워크·admission 정책과 ServiceAccount는 변경하지 않는다. 기존 역할에는 kubeconfig 조회 권한이 남는다. 추천이 임의의 실행 파일을 사용할 경우까지 막는 별도 IAM 격리를 증명하지 않는다.
- management image와 lock은 추천 CLI가 포함된 ai-sandbox main `21b5cb344594cdba121defc4511b341e112280c0`의 `21b5cb3@sha256:ddbd9cc74b4356f7459752301befe21af0b7b0241cb9b4dd8c50210451720994`로 함께 고정한다. 소스 CI [42400783996](https://github.com/2026-Softbank-Hackathon-Charlie/ai-sandbox/actions/runs/42400783996) 통과 후 기존 이미지 설정·레이어를 유지한 build/publish 결과를 루트 작업자가 전달했다. 이 슬라이스는 registry 자격 증명으로 별도 이미지를 만들거나 게시하지 않았다. 지원 annotation만 먼저 운영에 반영하지 않도록 pin과 템플릿을 함께 검토한다.
- 실제 클라우드 조회·키 발급/저장·이미지 게시·Helm 배포·운영 AI 호출을 이 로컬 검사에서 수행하지 않았다. 추천 API의 보안 필터와 900초 캐시는 플랫폼의 별도 계약이다.

## 관련 자료

- [Job 및 fetch 템플릿](../../helm/charts/hopcharlie-platform/templates/hopai.yaml)
- [shell 경계 회귀](../../scripts/tests/test-hopai-recommendations.py)
- [ai-sandbox #51](https://github.com/2026-Softbank-Hackathon-Charlie/ai-sandbox/pull/51)
