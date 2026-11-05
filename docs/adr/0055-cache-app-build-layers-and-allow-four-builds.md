# 0055. 앱 CodeBuild에 로컬 Docker layer 캐시를 쓰고 동시 빌드를 4개로 늘린다

- 날짜: 2026-11-05
- 상태: 제안
- 관련 ADR: [ADR 0022](0022-platform-app-build.md)의 동시 빌드 수를 바꾸고 캐시를 추가한다

## 배경과 제약

ADR 0022는 `hopcharlie-app-build`의 동시 빌드를 1개로 정했다. 플랫폼은 배포 작업자 2개(`PLATFORM_DEPLOY_WORKERS` 기본값, 차트에서 바꾸지 않음)로 배포를 동시에 처리하고, 배포마다 `StartBuild`를 한 번 부른다. CodeBuild는 프로젝트의 동시 빌드 한도에 닿으면 새 빌드를 대기열에 넣지 않고 실행하지 않는다. 플랫폼은 이 오류를 재시도하지 않으므로(`internal/deploy/codebuild.go`의 `StartBuild` 호출), 두 앱을 동시에 배포하면 두 번째 배포가 빌드 시작 단계에서 실패한다.

작업자 수보다 많은 빌드가 동시에 돌 수 있는 경로도 있다. platform `c4cceba` 기준으로 확인했다.

- 새 배포가 이전 배포를 대체하면 플랫폼은 이전 작업의 context만 취소한다(`internal/deploy/deploy.go`의 `stop`). CodeBuild 빌드는 `StopBuild` 없이 끝까지 돌고, 비워진 작업자는 바로 다음 빌드를 시작한다.
- API가 재시작되면 이미지가 없는 배포는 처음부터 다시 빌드한다(`internal/deploy/job.go`의 `build`). 저장한 `BuildID`를 이어서 따라가지 않으므로 이전 빌드와 새 빌드가 함께 돈다.

그 밖의 제약은 다음과 같다.

- 계정의 Linux/Small 동시 빌드 quota(`L-9D07B6EF`)는 15다(기본값은 1). 2026-11-05에 service-quotas로 확인했다.
- 지금은 캐시가 없어 빌드마다 base image를 받고 모든 layer를 다시 만든다. 최근 빌드 6개는 54초에서 111초가 걸렸고 대부분이 BUILD 단계다. 빌드는 BuildKit으로 돈다.
- 플랫폼 buildspec은 소스 없이(`NO_SOURCE`) 직접 `git fetch`를 한다. 로컬 소스 캐시는 CodeBuild가 받는 Git 소스에만 적용되므로 효과가 없다.
- 로컬 캐시는 VPC 설정이 있는 프로젝트에서 지원하지 않는다. 이 프로젝트는 VPC 밖에서 돈다.

## 검토한 대안

- 동시 빌드 1 유지: 플랫폼에 대기와 재시도를 넣어야 한다. platform 코드 변경이 필요해 이번에는 제외한다.
- 동시 빌드를 작업자 수와 같은 2로 설정: 위의 대체 배포나 재시작 경로에서 남은 빌드가 한도를 차지해 다시 거절된다.
- 동시 빌드를 작업자 수의 두 배인 4로 설정: 작업자마다 남은 빌드 1개와 새 빌드 1개를 함께 받아 준다. quota 15 안이고, 요금은 빌드 시간에만 붙으므로 한도를 올려도 비용이 늘지 않는다. 이 값을 선택한다.
- S3 캐시: 호스트와 상관없이 남지만 buildspec에 적은 경로만 저장한다. Docker layer를 담으려면 buildspec에서 이미지를 저장하고 불러와야 해서 제외한다.
- ECR registry 캐시(`--cache-to type=registry`): 호스트와 상관없이 맞지만 platform buildspec을 바꿔야 한다. 후속 대안으로 남긴다.
- 로컬 Docker layer 캐시: 프로젝트 설정만 바꾸면 된다. 이 방법을 선택한다.

## 결정과 이유

[app-build.tf](../../terraform/platform/aws/app-build.tf)의 `concurrent_build_limit`를 4(배포 작업자 2개의 두 배)로 올리고 `cache { type = "LOCAL", modes = ["LOCAL_DOCKER_LAYER_CACHE"] }`를 추가한다. 컴퓨팅 크기(SMALL), 빌드 및 대기 timeout 15분, privileged 모드는 그대로 둔다. Docker layer 캐시는 privileged 모드가 필요한데 이미 켜져 있다. `PLATFORM_DEPLOY_WORKERS`를 올리면 이 한도도 같이 올린다.

## 영향과 후속 작업

- 기대 효과: 두 앱을 동시에 배포하거나 배포가 대체되어도 빌드가 거절되지 않는다. 빌드가 직전 빌드와 같은 호스트에 잡히면 base image pull과 바뀌지 않은 layer를 건너뛴다.
- 단점과 위험: 로컬 캐시는 빌드가 짧은 간격으로 같은 호스트에 잡힐 때만 맞는다. 간격이 길거나 호스트가 바뀌면 효과가 없다.
- 캐시 공유: 같은 호스트에 잡힌 빌드는 앱과 상관없이 Docker 상태를 함께 쓴다. `RUN --mount=type=cache` 디렉터리는 경로가 같으면 다른 앱의 빌드와 공유되고, `FROM`은 다른 빌드가 받아 둔 base image를 그대로 쓸 수 있다. 그래서 앱 사이의 격리를 보장하지 않는다. 지금은 운영자만 배포하고, GitHub App을 설치한 팀 저장소만 빌드하므로 노출 범위가 작다. 외부 사용자의 저장소를 빌드하게 되면 캐시를 끄거나 앱마다 프로젝트를 나누는 것을 다시 검토한다.
- 이 root(`terraform/platform/aws`)는 CI가 자동 apply하지 않는다. merge 후 운영자가 `./tf.sh plan`에서 CodeBuild 프로젝트 1개 변경만 나오는지 확인하고 `./tf.sh apply`한다.
- 검증: 같은 앱을 연달아 두 번 배포해 두 번째 빌드 로그에 `CACHED`가 보이고 BUILD 단계가 짧아졌는지 본다. 두 앱을 동시에 배포해 두 빌드가 모두 성공하는지 본다.
- 프로젝트를 VPC 안으로 옮기면 로컬 캐시를 쓸 수 없으므로 이 결정을 다시 검토한다.

플랫폼 후속 작업(platform 저장소, 이번 변경에 포함하지 않음):

- 재시작 후 이어 갈 때 저장한 `BuildID`가 아직 돌고 있으면 새로 시작하지 않고 그 빌드를 따라간다.
- 대체되거나 취소된 배포의 빌드는 `StopBuild`로 멈춘다. 플랫폼 pod 역할에 이 프로젝트의 `codebuild:StopBuild` 권한을 추가해야 한다.
- `StartBuild`가 `AccountLimitExceededException`으로 거절되면 잠시 기다렸다가 재시도한다.
- 빌드가 이미지 digest를 `docker inspect`의 `RepoDigests` 첫 항목에서 읽는다. 캐시로 호스트의 이미지 기록이 남으면 같은 이미지가 다른 저장소의 항목을 먼저 가질 수 있으므로, push 결과나 해당 저장소 이름으로 digest를 고른다.

## 관련 자료

- [app-build.tf](../../terraform/platform/aws/app-build.tf), [mock 검사](../../terraform/platform/aws/tests/app-build.tftest.hcl)
- [platform 빌드 호출](https://github.com/2026-Softbank-Hackathon-Charlie/platform/blob/main/internal/deploy/codebuild.go), [배포 작업자](https://github.com/2026-Softbank-Hackathon-Charlie/platform/blob/main/internal/deploy/deploy.go), [배포 단계](https://github.com/2026-Softbank-Hackathon-Charlie/platform/blob/main/internal/deploy/job.go)
- [CodeBuild 로컬 캐시](https://docs.aws.amazon.com/codebuild/latest/userguide/caching-local.html)
- [CodeBuild 프로젝트 동시 빌드 한도](https://docs.aws.amazon.com/codebuild/latest/userguide/create-project.html#enable-concurrent-build-limit.console)
