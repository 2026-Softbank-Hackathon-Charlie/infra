# 0076. 레지던시 정책과 동등성 파일을 SSM에서 선택적으로 받아 플랫폼 검사를 켠다

- 날짜: 2026-11-07
- 상태: 제안
- 관련 ADR: 없음 (platform 레포 ADR 0013 레지던시 시작 게이트, ADR 0022 동등성 기록 모드)

## 배경과 제약

- 플랫폼 API는 `PLATFORM_RESIDENCY_POLICY_FILE`이 있으면 새 run을 시작하기 전에 레지던시를 검사해 위반이면 막고, `PLATFORM_PARITY_FILE`이 있으면 대상의 동등성을 검사해 감사 로그에 기록만 한다. 두 값이 가리키는 파일을 읽지 못하면 API가 시작하지 않는다(`platform/cmd/api/main.go`).
- 지금 차트(`helm/charts/hopcharlie-platform`)는 두 환경 변수도, 두 파일도 API pod에 넣지 않는다. 2026-11-07 운영 감사 로그의 이전 시작 기록에는 `residency: not_configured`, `parity.outcome: not_configured`가 남아 있다.
- API pod의 설정은 `fetch-settings.sh`가 SSM Parameter Store의 `<prefix>/` 아래에서 `/config`로 받는다. Kubernetes Secret이나 Git에 설정 값을 두지 않는다.
- 정책 내용(테이블 분류, 버킷, 대상 Prometheus 주소 등)은 아직 정하지 않았다. 내용이 정해지기 전에도 이 변경을 배포할 수 있어야 한다.

## 검토한 대안

- 대안 A: `api.yaml`의 env에 두 변수를 고정으로 넣고, 파라미터를 반드시 읽게 한다. 파라미터가 없으면 init이 실패하거나 API가 시작하지 않으므로, 정책 내용을 정하기 전에는 배포할 수 없다. 제외했다.
- 대안 B: 정책 파일을 ConfigMap으로 차트에 넣는다. 설정을 SSM에서 받는 지금 방식과 달라지고, 정책을 바꿀 때마다 차트 값을 고쳐야 한다. 제외했다.
- 대안 C: `app-secrets-key`처럼 `get-parameters`로 읽어, 파라미터가 있을 때만 파일을 쓰고 `api.env`에 환경 변수를 넣는다. 파라미터가 없으면 지금과 같다. 선택했다.

## 결정과 이유

- `fetch-settings.sh`의 `api` 분기가 `<prefix>/host/residency-policy.yaml`과 `<prefix>/host/parity.yaml`을 `get-parameters`로 읽는다. 파라미터 이름은 `host/envs.yaml`과 같은 규칙을 따른다.
- 값이 있으면 `/config/residency-policy.yaml`, `/config/parity.yaml`로 쓰고, `api.env`에 `PLATFORM_RESIDENCY_POLICY_FILE`, `PLATFORM_PARITY_FILE` 줄을 넣는다. 값이 없으면 파일도 줄도 만들지 않는다.
- 접근 오류와 네트워크 오류는 `get-parameters`가 실패로 끝나므로 지금처럼 init에서 멈춘다. 없는 파라미터만 `InvalidParameters`로 넘어간다.

## 영향과 후속 작업

- 기대 효과: 파라미터를 올리고 API pod를 다시 띄우면 두 검사가 켜진다. 파라미터를 지우고 다시 띄우면 꺼진다.
- 단점과 위험: 레지던시 정책을 올리면 위반하는 이전이 409 `residency_denied`로 막힌다. 특히 파일을 옮기는 Plan은 정책에 `inspectionEnvironment`, 버킷별 `expect`, 이미지 파일의 `reviewedFiles`가 없으면 거부된다(platform ADR 0031). 정책 파일 형식이 틀리거나 `envs.yaml`에 없는 환경을 쓰면 API가 시작하지 않는다.
- 필요한 후속 작업 및 검증:
  1. 정책 내용을 정해 파라미터로 올린다. 올리는 방법(Terraform `host_files` 또는 `aws ssm put-parameter`)은 내용과 함께 정한다.
  2. API pod를 다시 띄운 뒤 로그에서 `residency gate enabled`, `parity recording enabled`를 확인한다.
  3. 이전을 한 번 시작해 감사 로그의 `residency`, `parity`가 `not_configured`가 아닌지 확인한다.
  4. 동등성의 RBAC 검사는 플랫폼 kubeconfig 계정이 대상 클러스터에서 SubjectAccessReview를 만들 수 있어야 하고, Prometheus 주소는 EKS의 API pod에서 닿아야 한다. 둘 다 아직 확인하지 않았다.

## 관련 자료

- [helm/charts/hopcharlie-platform/templates/settings.yaml](../../helm/charts/hopcharlie-platform/templates/settings.yaml)
- platform 레포 `README.md`의 `PLATFORM_RESIDENCY_POLICY_FILE`, `PLATFORM_PARITY_FILE` 설명과 `examples/residency-policy.yaml`, `examples/parity.yaml`
