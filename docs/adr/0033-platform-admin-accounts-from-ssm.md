# 0033. 콘솔 관리자 계정을 SSM에서 플랫폼 API로 넘긴다

- 날짜: 2026-11-04
- 상태: 제안
- 관련 ADR: [ADR 0016](0016-run-hopcharlie-platform-on-management-cluster.md), [ADR 0021](0021-configure-platform-github-app-deploy.md), platform 저장소 ADR 0018(관리자 로그인과 CLI device code 로그인)

## 배경과 제약

- platform ADR 0018에 따라 콘솔은 정적 토큰을 붙여 넣는 대신 관리자 아이디와 비밀번호로 로그인한다. CLI는 `charlie login`이 보여 주는 코드를 관리자가 콘솔에 입력해 승인하는 방식으로 토큰을 받는다.
- 플랫폼 API는 관리자 계정을 환경 변수 `PLATFORM_ADMINS`(`이름:bcrypt 해시`를 쉼표로 구분)로 받는다. 서버는 비밀번호 원문을 받지 않는다.
- 비밀값은 SSM `/hopcharlie/*`에만 두고 API pod의 init 컨테이너가 읽는다(ADR 0016). Pod Identity 역할 `hopcharlie-platform-pod`는 이 경로 전체를 이미 읽을 수 있다(`terraform/platform/aws/main.tf`의 SSM 읽기 정책).
- 아이디와 비밀번호는 팀원이 직접 정한다. Terraform이 만들 수 있는 값이 아니다.
- 콘솔 주소는 플랫폼 기본값 `https://console.hopcharlie.dev`와 같으므로 `PLATFORM_CONSOLE_URL`은 넣지 않는다.

## 검토한 대안

- `terraform/platform/aws`의 `team_secret`에 넣어 빈 값만 만들기: 해시 형식이 아닌 자리 표시 값이 남으면 API가 시작하지 않는다. ADR 0021과 같은 이유로 제외했다.
- Kubernetes Secret으로 넣기: 차트 검사가 플랫폼 Secret을 금지하고 비밀값의 위치가 둘로 나뉜다.
- 비밀번호 원문을 SSM에 두고 서버가 비교하기: SSM 읽기 권한이 있는 사람이나 pod가 비밀번호를 그대로 볼 수 있다. 해시만 두면 이 위험이 줄어든다.

## 결정과 이유

- SSM SecureString `/hopcharlie/platform-admins`에 `이름:bcrypt 해시` 목록을 둔다. 사람이 `aws ssm put-parameter`로 만들고 Terraform은 관리하지 않는다. 값이 Terraform state에 남지 않는다.
- API의 init 컨테이너(`helm/charts/hopcharlie-platform/templates/settings.yaml`)가 이 파라미터를 읽어 `api.env`의 `PLATFORM_ADMINS`로 쓴다. API 컨테이너는 `api.env`를 셸 확장 없이 읽으므로 해시의 `$`가 그대로 전달된다.
- 해시는 platform 저장소의 `go run ./cmd/api -hash-password`로 만든다. 터미널에서는 비밀번호 입력이 가려지고 해시만 출력된다. Windows에서는 Git Bash(mintty)가 입력을 가리지 못하므로 PowerShell에서 실행한다.
- 정적 토큰 `/hopcharlie/platform-tokens`는 그대로 둔다. AI 플래너, 부하 생성기처럼 로그인할 수 없는 프로그램이 계속 쓴다.

## 영향과 후속 작업

- 기대 효과: 플랫폼 이미지에 ADR 0018 구현이 들어간 뒤 이 설정이 배포되면 콘솔 일반 모드 로그인과 CLI device code 로그인이 동작한다.
- 단점과 위험:
  - 파라미터가 없는 상태에서 이 설정이 배포되면 API의 init 컨테이너가 실패하고, Recreate 전략이라 그동안 API가 내려간다. 파라미터를 먼저 만든다.
  - 값이 잘못되어도(해시 형식 오류, 같은 이름 두 번, 관리자마다 다른 bcrypt cost) API가 시작하지 않는다. 정적 토큰을 쓰는 프로그램까지 함께 멈추므로, `-hash-password`로 만든 해시만 넣고 목록 전체를 확인한 뒤 바꾼다.
  - `put-parameter --overwrite`는 값을 통째로 바꾼다. 관리자를 추가할 때 지금 값을 읽지 않고 한 명만 넣으면 다른 관리자가 사라진다.
  - 관리자를 추가하거나 비밀번호를 바꾸면 `hopcharlie-api` Deployment를 다시 시작해야 반영된다. 이미 발급된 세션과 CLI 토큰은 만료되거나 로그아웃할 때까지 유효하다.
- 필요한 후속 작업 및 검증:
  - SSM `/hopcharlie/platform-admins` 생성([절차](../../terraform/platform/aws/README.md#설정-바꾸기))
  - 배포 뒤 `https://console.hopcharlie.dev`에서 일반 모드로 로그인하고, `charlie login`으로 받은 코드를 콘솔의 CLI 연결 화면에서 승인해 확인

## 관련 자료

- [helm/charts/hopcharlie-platform/templates/settings.yaml](../../helm/charts/hopcharlie-platform/templates/settings.yaml)
- [terraform/platform/aws/README.md](../../terraform/platform/aws/README.md)
