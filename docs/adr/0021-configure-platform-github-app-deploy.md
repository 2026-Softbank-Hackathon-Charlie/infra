# 0021. 플랫폼 차트에 GitHub 앱 배포 설정을 넣는다

- 날짜: 2026-11-03
- 상태: 제안
- 관련 ADR: [ADR 0016](0016-run-hopcharlie-platform-on-management-cluster.md), platform 저장소 ADR 0011(GitHub 앱 배포), ADR 0012(게이트웨이 호스트 라우팅)

## 배경과 제약

- platform #30~#36이 GitHub 저장소로 앱을 등록하고 push마다 배포하는 기능을 추가했다. 운영 플랫폼에서 이 기능을 쓰려면 API에 GitHub App, 빌더, 앱 도메인 설정을, 게이트웨이에 앱 도메인을 넣어야 한다.
- GitHub App 변수가 없으면 앱 등록이 503 `github_not_configured`로 실패하고 webhook도 503으로 답한다. builder 변수가 없으면 배포가 `queued`에서 멈춘다.
- 배포 실행기는 Argo CD에 refresh를 요청하고 Application 상태를 읽는다. `PLATFORM_ARGOCD_KUBECONTEXT`가 비어 있으면 in-cluster 설정을 쓰므로 API pod에 ServiceAccount 토큰이 있어야 한다. 지금은 어느 pod도 토큰을 마운트하지 않는다.
- 비밀값은 SSM `/hopcharlie/*`에만 둔다(ADR 0016). Pod Identity 역할 `hopcharlie-platform-pod`는 이미 이 경로 전체를 읽을 수 있다(2026-11-03 IAM 조회로 확인).
- GitHub App private key와 webhook secret은 GitHub에서 만든 값이라 Terraform이 생성할 수 없다.

## 검토한 대안

- private key를 Kubernetes Secret이나 Sealed Secret으로 두기: 차트 검사가 플랫폼 Secret을 금지하고(ADR 0016), 비밀값의 위치가 둘로 나뉜다.
- `terraform/platform/aws`의 `team_secret`에 추가해 빈 값만 만들고 실제 값은 put-parameter로 넣기: 기존 방식과 같지만 관리 EC2까지 가진 root를 apply해야 한다. 빈 값이 남은 채 차트가 배포되면 API가 키를 읽지 못해 시작하지 않는다.
- 별도 kubeconfig(SSM `kube/<이름>`)로 Argo CD에 접근하기: 장기 토큰을 하나 더 발급해 SSM에 넣어야 한다.

## 결정과 이유

- 차트 값 `appDomain`, `github.appId`, `github.installationId`, `deploy.builder`, `deploy.codebuildProject`, `deploy.imageRegistry`를 추가한다. 모두 비어 있으면 지금과 같은 manifest를 렌더링한다.
- `github.appId`가 있으면 API의 init 컨테이너가 SSM `github-app-private-key`를 `/config/github-app.pem`(메모리 볼륨)에 쓰고, `github-webhook-secret`을 `api.env`의 `PLATFORM_GITHUB_WEBHOOK_SECRET`으로 쓴다. 두 파라미터는 `tailscale-authkey`처럼 사람이 `aws ssm put-parameter`로 만들고 Terraform은 관리하지 않는다. 별도 apply가 필요 없고, 값이 Terraform state에 남지 않는다.
- `deploy.builder`가 있으면 API pod만 ServiceAccount 토큰을 마운트한다. 이 토큰의 권한은 argocd 네임스페이스의 Role이 정하며, Role은 앱 ApplicationSet을 추가하는 후속 PR에서 Application get, patch와 ApplicationSet patch만 준다. 게이트웨이, 콘솔, PostgreSQL pod는 계속 토큰을 마운트하지 않는다.
- `appDomain` 하나를 API의 `PLATFORM_APP_DOMAIN`과 게이트웨이의 `GATEWAY_APP_DOMAIN`에 함께 넣는다. 게이트웨이는 `app.hopcharlie.dev`를 지금처럼 기본 앱으로 보내고, 도메인 아래의 모르는 이름에는 404로 답한다.
- 관리 클러스터 값은 App ID `5589608`, 설치 ID `178050051`, 빌더 `codebuild`, CodeBuild 프로젝트 `hopcharlie-app-build`, 레지스트리 `525091405886.dkr.ecr.ap-northeast-2.amazonaws.com`이다. 프로젝트 이름은 이 ADR에서 정하며, 프로젝트는 아직 없다.
- `deploy.builder`가 있는데 `github.appId`나 레지스트리가 없으면 플랫폼이 시작하지 않으므로, 차트 렌더링 단계에서 실패시킨다.

## 영향과 후속 작업

- 기대 효과: 이미지 `2aea035` 이상과 이 설정이 함께 배포되면 앱 등록과 push webhook이 동작한다.
- 단점과 위험:
  - 두 SSM 파라미터가 없는 상태에서 이 설정이 배포되면 API의 init 컨테이너가 실패한다. API는 Recreate 전략이라 그동안 API가 내려간다. 파라미터를 먼저 만든다.
  - API pod가 토큰을 가지므로 API가 탈취되면 argocd 네임스페이스의 Application을 patch할 수 있다. Role의 대상과 동사를 최소로 둔다.
  - CodeBuild 프로젝트와 플랫폼 역할의 `codebuild:StartBuild`, `codebuild:BatchGetBuilds`, `logs:GetLogEvents` 권한이 생기기 전까지 배포는 빌드 단계에서 실패한다.
- 필요한 후속 작업 및 검증:
  - SSM `/hopcharlie/github-app-private-key`, `/hopcharlie/github-webhook-secret` 생성([절차](../../terraform/platform/aws/README.md#설정-바꾸기))
  - CodeBuild 프로젝트 `hopcharlie-app-build`, 프로젝트 역할(ECR push, `hopcharlie/apps/*` 저장소 생성), 플랫폼 역할의 빌드 권한
  - argocd 네임스페이스의 Role, 앱 ApplicationSet과 `hopcharlie-app` 차트(후속 PR)
  - 운영 반영: 플랫폼은 아직 Helm 릴리스이므로 `helm upgrade` 또는 Argo CD sync가 필요하다. 반영 뒤 API 로그에 `github_not_configured` 경고가 없는지, GitHub App webhook 전송이 2xx인지 확인한다.

## 관련 자료

- [helm/charts/hopcharlie-platform/values.yaml](../../helm/charts/hopcharlie-platform/values.yaml)
- [helm/charts/hopcharlie-platform/templates/api.yaml](../../helm/charts/hopcharlie-platform/templates/api.yaml)
- [helm/charts/hopcharlie-platform/templates/settings.yaml](../../helm/charts/hopcharlie-platform/templates/settings.yaml)
- [clusters/aws-dev-management/values/hopcharlie-platform.yaml](../../clusters/aws-dev-management/values/hopcharlie-platform.yaml)
- platform 저장소 README의 "GitHub App 설정", "배포 실행기" 절
