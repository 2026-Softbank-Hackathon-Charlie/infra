# 0042. private 저장소의 GitHub Actions를 조직 self-hosted runner에서 실행한다

- 날짜: 2026-11-04
- 상태: 제안
- 관련 ADR: [ADR 0002](0002-require-approval-for-privileged-operations.md), [ADR 0024](0024-publish-images-from-github-actions.md), [ADR 0025](0025-pin-images-on-infra-main.md)

## 배경과 제약

- 2026-11-04부터 조직 private 저장소의 GitHub-hosted job이 모두 "The job was not started because recent account payments have failed or your spending limit needs to be increased"로 시작되지 않는다. 조직은 Free 플랜이라 private 저장소에 한 달 2,000분이 포함되는데, 11월 사용량이 3,164분이다. 한도는 12월 1일에 초기화되고 해커톤은 11월 8일에 끝난다.
- 3,164분 중 2,630분이 infra다. push 한 번에 job이 17개쯤 돌고, job마다 최소 1분으로 올려 과금한다.
- 저장소는 private으로 유지한다(사용자 결정).
- `runs-on: [self-hosted, <label>]` job은 실패하지 않고 runner를 기다리며 `queued`로 남는 것을 확인했다. 24시간 안에 runner가 잡지 않으면 실패한다. self-hosted runner의 실행 시간은 2026-11-04 현재 GitHub가 과금하지 않는다. GitHub가 private 저장소의 self-hosted 실행에도 분당 요금을 매기는 안을 발표했다가 미뤘다는 이야기가 있으나 확인하지 않았다.
- 조직 Default runner group은 모든 저장소에 열려 있고 공개 저장소는 허용하지 않는다(`allows_public_repositories: false`, 2026-11-04 확인). Free 플랜에는 runner group이 Default 하나뿐이다.
- 계정의 온디맨드 표준 vCPU 할당량은 32개이고 14개를 쓰고 있다(2026-11-04 확인). t3.xlarge는 4개를 더 쓴다.
- workflow는 GitHub-hosted 이미지에 있는 도구를 그대로 쓴다. setup 액션이 설치하지 않는 것은 docker와 buildx, aws, gh, jq, 시스템 python3, `-race`용 gcc, make, setup-terraform이 쓰는 unzip이고, infra `scripts` job의 `make ci-test`는 helm과 node도 쓴다. `sudo`를 쓰는 workflow는 없다.

## 검토한 대안

- 저장소를 public으로 바꾼다: GitHub-hosted runner를 무료로 쓸 수 있지만 사용자가 거절했다.
- 관리 EKS에 Actions Runner Controller를 올린다: 관리 노드에 남는 자리가 없고, docker build와 PostgreSQL 컨테이너를 쓰는 job 때문에 privileged Docker-in-Docker pod가 필요하다.
- 기존 `rin` EC2(t3.large)에 runner를 올린다: Rin 봇과 CPU, 메모리, 디스크를 나눠 써서 CI가 몰리면 봇이 느려지거나 멈춘다.
- CI를 로컬에서 돌린다: 사람이 매번 손으로 돌려야 하고, 이미지 게시와 infra 고정(ADR 0025)이 멈춘다.
- 같은 자원을 Terraform root로 만든다: 해커톤이 끝나면 바로 지울 자원인데 state와 plan 리뷰 절차가 늘어난다. 대신 스크립트가 이름과 태그로 자원을 찾아서, 다시 실행해도 같은 결과가 나오게 한다.

## 결정과 이유

- 해커톤 AWS 계정(525091405886, ap-northeast-2)의 전용 EC2 한 대를 조직 단위 self-hosted runner 호스트로 쓴다. Ubuntu 24.04, t3.xlarge, 암호화한 80 GiB gp3다. runner 3개(`hopcharlie-runner-1`, `hopcharlie-runner-2`, `hopcharlie-runner-3`)가 job을 하나씩 동시에 실행하고, 모두 label `hopcharlie`와 Default runner group을 쓴다.
- [scripts/github-runner/up.py](../../scripts/github-runner/up.py)가 자원을 이름과 태그(`Name=hopcharlie-github-runner`)로 찾고 없으면 만든다. IAM 역할과 instance profile `hopcharlie-github-runner`(EC2 신뢰, SSM 에이전트용 인라인 정책 `ssm-agent`만. 아래 위험 항목 참고), 기본 VPC의 ingress 없는 보안 그룹, 기본 서브넷의 public IP다. 기본 VPC에는 NAT가 없어서 public IP로 GitHub, apt, SSM에 나간다. 접속은 SSM만 쓴다.
- 인스턴스 메타데이터는 IMDSv2만 받고 hop limit을 1로 둬서 job의 컨테이너가 인스턴스 역할에 닿지 못하게 한다. job의 step은 호스트에서 `runner` 사용자로 돌기 때문에, bootstrap이 `runner` 사용자의 메타데이터 요청도 iptables로 거부한다. SSM 에이전트(root)는 그대로 쓴다.
- `up.py`는 SSM이 인스턴스를 Online으로 보면 조직 registration token을 받아 [bootstrap.sh](../../scripts/github-runner/bootstrap.sh)를 SSM Run Command로 root로 실행한다. token은 출력하지 않고 명령줄에도 넣지 않는다. bootstrap은 다시 실행해도 안전하며 다음을 한다.
  - Ubuntu 패키지의 docker.io와 docker-buildx(2026-11-04 기준 29.1과 0.30, `--metadata-file`, `--provenance` 지원), AWS CLI v2(공식 zip), gh(공식 apt 저장소), git, curl, jq, unzip, zip, zstd, xz-utils, python3, python3-venv, python3-pip, build-essential을 설치한다.
  - Docker를 classic image store로 둔다. Docker 29는 새로 설치하면 containerd image store를 쓰는데, 그러면 frontend가 게시하는 이미지에 attestation이 붙어 지금 GitHub-hosted에서 만드는 단일 manifest와 달라진다.
  - `runner` 사용자를 docker 그룹에 넣는다. sudo 권한은 주지 않는다.
  - 8 GiB swap 파일을 만든다. 16 GiB 메모리를 job 3개가 나눠 쓰므로 OOM killer가 runner를 죽이지 않게 한다.
  - apt가 잠금을 600초까지 기다리게 하고, `apt-daily`와 `apt-daily-upgrade` 타이머를 끈다. 자동 업그레이드가 job 도중 dockerd를 재시작하지 않게 하려는 것이다. 다시 실행할 때도 이미 설치한 패키지는 올리지 않는다.
  - actions/runner 최신 release를 release 노트의 sha256으로 확인하고 `/opt/actions-runner/1`부터 `3`까지 설치해 systemd 서비스로 등록한다. token은 `ACTIONS_RUNNER_INPUT_TOKEN` 환경 변수로 넘겨 프로세스 목록에 나오지 않게 한다. 이미 설정한 runner는 건너뛴다.
  - 매일 04:00(KST)에 하루 지난 Docker 이미지와 빌드 캐시, 쓰지 않는 볼륨을 지운다.
- infra, platform, frontend, cli, rin의 모든 job을 `runs-on: [self-hosted, hopcharlie]`로 바꾼다. 공개 저장소 moimroom은 GitHub-hosted에 그대로 둔다. 여러 job이 한 호스트를 함께 쓰므로 workflow는 다음을 지킨다. `$RUNNER_TEMP`는 job이 끝나면 비워진다.
  - 토큰을 넣는 git 설정은 `GIT_CONFIG_GLOBAL`을 job의 `$RUNNER_TEMP` 안 파일로 바꿔서 쓴다. runner 사용자의 `~/.gitconfig`에 남기지 않는다.
  - `docker login`은 `DOCKER_CONFIG`를 job의 `$RUNNER_TEMP` 아래로 바꿔서 한다. buildx 플러그인은 시스템 경로(`/usr/libexec/docker/cli-plugins`)에 있어서 그대로 찾는다.
  - 고정 이름과 포트를 쓰지 않는다. platform CI의 PostgreSQL은 run마다 다른 컨테이너 이름과 127.0.0.1의 임의 포트로 띄우고, job 끝에 `if: always()`로 지운다.
  - 호스트에 없는 도구는 workflow가 설치한다. infra `scripts` job에 helm(helm job과 같은 v3.19.1)과 node 22 setup 액션을 넣는다.
  - platform과 cli의 Go 캐시 복원과 저장(`actions/cache`)은 GitHub-hosted runner에서만 한다(`runner.environment == 'github-hosted'`). 호스트의 `~/go`와 `~/.cache/go-build`가 job 사이에 남아 캐시 역할을 하고, 그 위에 tar로 복원하면 동시에 도는 다른 job의 go와 부딪친다. 이미 있는 모듈 폴더는 읽기 전용이라 tar가 "Cannot open: File exists"로 실패한다(로컬 tar로 확인).
- [scripts/github-runner/down.py](../../scripts/github-runner/down.py)는 기본으로 지울 것만 출력하고, `--apply`를 주면 SSM으로 runner 등록을 해제하고(`config.sh remove`), GitHub에 남은 runner를 API로 지우고, 인스턴스, 보안 그룹, instance profile, 역할을 지운다.

## 영향과 후속 작업

- 기대 효과: private 저장소의 CI, 이미지 게시, infra 이미지 고정이 GitHub 과금과 상관없이 다시 돈다.
- 단점과 위험:
  - 호스트는 job 사이에 초기화되지 않는다. 작업 폴더(`_work`), Docker 이미지와 빌드 캐시, `runner` 사용자 홈(`~/go`, `~/.cache`, `~/.npm`)이 다음 job과 다른 runner에 남는다. 앞의 job이 남긴 파일이 다음 job에 영향을 줄 수 있다.
  - job끼리 격리되지 않는다. 세 runner가 같은 사용자로 돌고 docker 그룹은 사실상 root라서, 한 job이 동시에 도는 다른 job의 환경 변수(OIDC로 받은 AWS 자격 증명 등)를 읽을 수 있다. 저장소가 모두 private이고 팀원만 push한다는 전제로 받아들인다.
  - 인스턴스 역할에는 `AmazonSSMManagedInstanceCore` 대신, 그 정책에서 `ssm:GetParameter`와 `ssm:GetParameters`를 뺀 인라인 정책 `ssm-agent`만 둔다. job 코드가 hop limit과 iptables 규칙을 우회해(docker 그룹 권한으로 호스트 네트워크 컨테이너를 띄우는 식) 인스턴스 역할 자격 증명을 얻어도 `/hopcharlie/*` SSM 파라미터는 읽지 못한다. 남는 권한은 SSM 에이전트의 메시지 채널과 상태 보고뿐이다.
  - registration token은 SSM 명령 파라미터(명령 기록, 30일 보관)와 인스턴스의 SSM 작업 폴더에 남는다. token은 1시간 뒤 만료된다.
  - 자동 보안 업데이트를 끄므로 해커톤 동안 호스트 패키지는 설치한 시점의 버전으로 남는다. ingress가 없고 며칠만 쓰는 호스트라 받아들인다.
  - 매일 정리에서 `until=24h`는 이미지의 생성 시각을 보므로, 쓰지 않는 기반 이미지(postgres, caddy, node)도 매일 지워지고 다음 job이 다시 받는다.
  - 비용: t3.xlarge 온디맨드가 서울에서 시간당 0.208달러로 하루 약 5달러, 80 GiB gp3가 하루 약 0.24달러, public IPv4가 하루 약 0.12달러다. t3 unlimited 모드라 CPU 크레딧을 넘겨 쓰면 추가 요금이 붙는다. 11월 8일까지 약 25달러로 추정한다.
  - 호스트 한 대가 단일 장애점이다. 인스턴스가 멈추면 job은 `queued`로 남고 24시간 뒤 실패한다. 동시에 3개를 넘는 job도 기다리고, infra push 하나가 job 17개쯤을 줄 세운다.
  - platform CI는 PostgreSQL 포트가 run마다 달라서 DB를 쓰는 테스트 패키지는 go test 결과 캐시를 쓰지 못하고 매번 다시 돈다.
- 필요한 후속 작업 및 검증:
  - 사용자가 `gh auth refresh -h github.com -s admin:org`, `python scripts/github-runner/up.py --dry-run`, `python scripts/github-runner/up.py` 순서로 실행하고, 조직 Settings의 Actions Runners에서 `hopcharlie-runner-1`부터 `3`까지 Idle인지 확인한다.
  - 저장소별 workflow PR을 merge하고, run의 "Set up job" 로그에서 Runner name이 `hopcharlie-runner-N`인지 확인한다.
  - 해커톤이 끝나면 `python scripts/github-runner/down.py --apply`로 지우고, 해커톤 정리 목록에 넣는다. 12월 1일 이후 GitHub-hosted로 돌아가려면 `runs-on`을 되돌린다.

## 관련 자료

- [scripts/github-runner/up.py](../../scripts/github-runner/up.py), [down.py](../../scripts/github-runner/down.py), [bootstrap.sh](../../scripts/github-runner/bootstrap.sh)
- [.github/workflows/terraform-check.yml](../../.github/workflows/terraform-check.yml), [terraform-gcp.yml](../../.github/workflows/terraform-gcp.yml), [platform-terraform.yml](../../.github/workflows/platform-terraform.yml)
- platform `ci.yml`, `publish-image.yml`, frontend `deploy.yml`, cli `ci.yml`, rin `deploy.yml`
- GitHub 문서: [About self-hosted runners](https://docs.github.com/en/actions/hosting-your-own-runners/managing-self-hosted-runners/about-self-hosted-runners), [About billing for GitHub Actions](https://docs.github.com/en/billing/managing-billing-for-your-products/managing-billing-for-github-actions/about-billing-for-github-actions)
