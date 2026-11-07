# 0080. 레지던시 정책을 SSM에서 선택적으로 받아 플랫폼의 시작 전 검사를 켠다

- 날짜: 2026-11-07
- 상태: 제안
- 관련 ADR: [0076](0076-enable-platform-parity-audit.md)(동등성 파일은 values로 넣는다). platform 레포 ADR 0013(레지던시 시작 게이트), ADR 0031(파일 이전의 레지던시 검사)

## 배경과 제약

- 플랫폼 API는 `PLATFORM_RESIDENCY_POLICY_FILE`이 있으면 새 run을 시작하기 전에 레지던시를 검사하고, 위반이면 409 `residency_denied`로 막는다. 파일을 읽지 못하거나 형식이 틀리면 API가 시작하지 않는다(platform `cmd/api/main.go`).
- 지금 차트는 이 변수도 파일도 API pod에 넣지 않는다. 2026-11-07 운영 감사 로그의 이전 시작 기록에 `residency: not_configured`가 남아 있다.
- 동등성 파일(`PLATFORM_PARITY_FILE`)은 이미 [ADR 0076](0076-enable-platform-parity-audit.md)에 따라 관리 환경 values의 `api.parity`가 ConfigMap으로 넣는다. 같은 변수를 두 곳에서 넣지 않도록 이 결정은 레지던시만 다룬다.
- API pod의 설정은 `fetch-settings.sh`가 SSM Parameter Store의 `<prefix>/` 아래에서 `/config`로 받는다.
- 정책 내용(테이블 분류, 버킷, 환경별 국가)은 아직 정하지 않았다. 내용을 정하기 전에도 이 변경을 배포할 수 있어야 한다.

## 검토한 대안

- 대안 A: `api.yaml`의 env에 변수를 고정으로 넣고 파라미터를 반드시 읽게 한다. 파라미터가 없으면 init이 실패하거나 API가 시작하지 않아, 정책을 정하기 전에는 배포할 수 없다. 제외했다.
- 대안 B: ADR 0076의 동등성처럼 values로 ConfigMap을 만든다. 정책을 바꿀 때마다 values PR이 필요하다. 레지던시 정책은 원본 테이블 목록이 바뀔 때마다 고쳐야 하고, 정책을 바로 끄고 켤 수 있어야 해서 이번에는 제외했다.
- 대안 C: `app-secrets-key`처럼 `get-parameters`로 읽어, 파라미터가 있을 때만 파일을 쓰고 `api.env`에 변수를 넣는다. 파라미터가 없으면 지금과 같다. 선택했다.

## 결정과 이유

- `fetch-settings.sh`의 `api` 분기가 `<prefix>/host/residency-policy.yaml`을 `get-parameters`로 읽는다. 파라미터 이름은 `host/envs.yaml`과 같은 규칙을 따른다.
- 값이 있으면 `/config/residency-policy.yaml`로 쓰고 `api.env`에 `PLATFORM_RESIDENCY_POLICY_FILE=/config/residency-policy.yaml`을 넣는다. 값이 없으면 파일도 줄도 만들지 않는다.
- 접근 오류와 네트워크 오류는 `get-parameters`가 실패로 끝나므로 지금처럼 init에서 멈춘다. 없는 파라미터만 `InvalidParameters`로 넘어간다.
- 플랫폼 pod 역할은 이미 `/hopcharlie/*`를 읽을 수 있어 IAM 변경은 없다.

## 영향과 후속 작업

- 기대 효과: 파라미터를 올리고 API pod를 다시 띄우면 레지던시 검사가 켜지고, 지우고 다시 띄우면 꺼진다.
- 단점과 위험: 정책을 올리면 위반하는 이전이 막힌다. 원본에 정책 분류표에 없는 테이블이 있으면 `unclassified_dataset`으로 거부된다. 파일을 옮기는 Plan은 정책에 `inspectionEnvironment`, 버킷별 `expect`가 없으면 거부된다(platform ADR 0031). 정책 형식이 틀리거나 `envs.yaml`에 없는 환경을 쓰면 API가 시작하지 않는다. SSM 표준 파라미터는 4KB까지라 정책이 크면 고급 파라미터가 필요하다.
- 필요한 후속 작업 및 검증 (모두 사람의 승인이 필요하다):
  1. 정책 내용을 정해 SecureString `/hopcharlie/host/residency-policy.yaml`로 올린다. 양식은 platform `examples/residency-policy.yaml`이다.
  2. API pod를 다시 띄운 뒤 로그에서 레지던시 게이트가 켜졌는지 확인한다.
  3. 이전을 한 번 시작해 감사 로그의 `residency`가 `not_configured`가 아닌지 확인한다.

## 관련 자료

- [helm/charts/hopcharlie-platform/templates/settings.yaml](../../helm/charts/hopcharlie-platform/templates/settings.yaml)
- [terraform/platform/aws/README.md](../../terraform/platform/aws/README.md)의 파라미터 표
- platform 레포 `README.md`의 `PLATFORM_RESIDENCY_POLICY_FILE` 설명, `examples/residency-policy.yaml`
