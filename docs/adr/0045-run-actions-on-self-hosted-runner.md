# 0045. private 저장소의 GitHub Actions를 조직 self-hosted runner에서 실행한다

- 날짜: 2026-11-04
- 상태: 제안
- 관련 ADR: [ADR 0002](0002-require-approval-for-privileged-operations.md), [ADR 0024](0024-publish-images-from-github-actions.md), [ADR 0025](0025-pin-images-on-infra-main.md)

이 문서는 처음에 0042 번호로 올렸으나 같은 날 merge된 [ADR 0042](0042-copy-app-images-to-artifact-registry-for-gke.md)와 번호가 겹쳐 0045로 옮겼다.

## 배경과 제약

- 2026-11-04부터 조직 private 저장소의 GitHub-hosted job이 모두 "The job was not started because recent account payments have failed or your spending limit needs to be increased"로 시작되지 않는다. 조직은 Free 플랜이라 private 저장소에 한 달 2,000분이 포함되는데, 11월 사용량이 3,164분이다. 한도는 12월 1일에 초기화되고 해커톤은 11월 8일에 끝난다.
- 3,164분 중 2,630분이 infra다. push 한 번에 job이 17개쯤 돌고, job마다 최소 1분으로 올려 과금한다.
- 저장소는 private으로 유지한다(사용자 결정).
- `runs-on: [self-hosted, <label>]` job은 실패하지 않고 runner를 기다리며 `queued`로 남는 것을 확인했다. 24시간 안에 runner가 잡지 않으면 실패한다. self-hosted runner의 실행 시간은 2026-11-04 현재 GitHub가 과금하지 않는다. GitHub가 private 저장소의 self-hosted 실행에도 분당 요금을 매기는 안을 발표했다가 미뤘다는 이야기가 있으나 확인하지 않았다.
- 조직 Default runner group은 모든 저장소에 열려 있고 공개 저장소는 허용하지 않는다(`allows_public_repositories: false`, 2026-11-04 확인). Free 플랜에는 runner group이 Default 하나뿐이다.
- 계정의 온디맨드 표준 vCPU 할당량은 32개이고 14개를 쓰고 있다(2026-11-04 확인). t3.xlarge는 4개를 더 쓴다.
- workflow는 GitHub-hosted 이미지에 있는 도구를 그대로 쓴다. setup 액션이 설치하지 않는 것은 docker와 buildx, aws, gh, jq, 시스템 python3, `-race`용 gcc, make, setup-terraform이 쓰는 unzip이고, infra `scripts` job의 `make ci-test`는 helm과 node도 쓴다. `sudo`를 쓰는 workflow는 없다.
- 2026-11-04에 AWS EC2 호스트로 runner를 올린 뒤, 사용자가 호스트를 GCP로 옮기기로 했다. 콘솔이 아니라 gcloud 스크립트로 만든다.
- GCP 프로젝트 `project-b47cdba3-db5b-42a2-9a6`(Softbank-Charlie, 번호 414068132732)는 결제가 켜져 있고 사용자 계정이 owner다. Compute, IAP, OS Login API가 켜져 있다. 프로젝트 vCPU 할당량(CPUS_ALL_REGIONS)은 24개 중 5개, asia-northeast3의 E2 vCPU(E2_CPUS)는 24개 중 0개, SSD 디스크(SSD_TOTAL_GB)는 400 GB 중 90 GB, 외부 IP는 8개 중 0개를 쓴다(2026-11-04 확인).
- GCP `default` 네트워크(auto mode, MTU 1460)에는 대상 태그 없이 우선순위 65534인 규칙이 있다. `default-allow-ssh`(tcp:22), `default-allow-rdp`(tcp:3389), `default-allow-icmp`가 0.0.0.0/0에, `default-allow-internal`이 10.128.0.0/9에 열려 있다. `default` 네트워크에는 Cloud Router와 Cloud NAT가 없다. GKE VPC `charlie-dev-gcp-workload-vpc`에는 GKE 노드와 앱 라우터가 있다(2026-11-04 확인).

## 검토한 대안

- 저장소를 public으로 바꾼다: GitHub-hosted runner를 무료로 쓸 수 있지만 사용자가 거절했다.
- 관리 EKS에 Actions Runner Controller를 올린다: 관리 노드에 남는 자리가 없고, docker build와 PostgreSQL 컨테이너를 쓰는 job 때문에 privileged Docker-in-Docker pod가 필요하다.
- 기존 `rin` EC2(t3.large)에 runner를 올린다: Rin 봇과 CPU, 메모리, 디스크를 나눠 써서 CI가 몰리면 봇이 느려지거나 멈춘다.
- CI를 로컬에서 돌린다: 사람이 매번 손으로 돌려야 하고, 이미지 게시와 infra 고정(ADR 0025)이 멈춘다.
- 같은 자원을 Terraform root로 만든다: 해커톤이 끝나면 바로 지울 자원인데 state와 plan 리뷰 절차가 늘어난다. 대신 스크립트가 이름과 태그로 자원을 찾아서, 다시 실행해도 같은 결과가 나오게 한다.
- 호스트를 AWS EC2에 그대로 둔다: 이미 돌고 있지만 사용자가 GCP로 옮기기로 했다. AWS 스크립트는 되돌아갈 경로로 남긴다.
- GCP 콘솔에서 손으로 만든다: 다시 만들거나 지울 때 설정을 빠뜨리기 쉽다. 사용자가 gcloud 스크립트를 골랐다.
- GKE VPC에 VM을 둔다: 기존 Cloud NAT로 외부 IP 없이 나갈 수 있지만, GKE 노드와 앱 라우터가 있는 네트워크에 CI 호스트가 들어가고 방화벽과 NAT를 함께 쓴다. 해커톤 뒤에 지울 때 GKE 쪽 자원과 얽히지 않도록 `default` 네트워크에 둔다.
- `default` 네트워크에 Cloud NAT를 만들고 외부 IP를 뺀다: Cloud Router와 NAT를 더 만들고 지워야 한다. ingress는 방화벽으로 막으므로 외부 IP가 더하는 것은 egress뿐이고, egress는 NAT를 써도 똑같이 열려 있다.
- VM에 서비스 계정을 붙인다: runner는 GCP API를 쓰지 않는다. 기본 Compute Engine 서비스 계정은 보통 프로젝트 Editor 역할을 받으므로(이 프로젝트에서는 확인하지 않았다), job 코드가 메타데이터 서버에서 토큰을 받아 프로젝트를 바꿀 수 있게 된다.

## 결정과 이유

- 조직 단위 self-hosted runner 호스트 한 대에서 runner 3개가 job을 하나씩 동시에 실행하고, 모두 label `hopcharlie`와 Default runner group을 쓴다. 처음에는 해커톤 AWS 계정의 EC2에 두었고, 2026-11-04에 사용자 결정으로 GCP VM으로 옮긴다. 두 호스트 모두 Ubuntu 24.04이고 같은 [bootstrap.sh](../../scripts/github-runner/bootstrap.sh)로 도구와 runner를 설치한다. workflow는 label만 보므로 옮겨도 바꾸지 않는다.

### AWS 호스트(처음 호스트, 되돌아갈 경로)

- 해커톤 AWS 계정(525091405886, ap-northeast-2)의 전용 EC2 한 대다. t3.xlarge, 암호화한 80 GiB gp3이고 runner 이름은 `hopcharlie-runner-1`, `hopcharlie-runner-2`, `hopcharlie-runner-3`이다.
- [scripts/github-runner/up.py](../../scripts/github-runner/up.py)가 자원을 이름과 태그(`Name=hopcharlie-github-runner`)로 찾고 없으면 만든다. IAM 역할과 instance profile `hopcharlie-github-runner`(EC2 신뢰, SSM 에이전트용 인라인 정책 `ssm-agent`만. 아래 위험 항목 참고), 기본 VPC의 ingress 없는 보안 그룹, 기본 서브넷의 public IP다. 기본 VPC에는 NAT가 없어서 public IP로 GitHub, apt, SSM에 나간다. 접속은 SSM만 쓴다.
- 인스턴스 메타데이터는 IMDSv2만 받고 hop limit을 1로 둬서 job의 컨테이너가 인스턴스 역할에 닿지 못하게 한다. job의 step은 호스트에서 `runner` 사용자로 돌기 때문에, bootstrap이 `runner` 사용자의 메타데이터 요청도 iptables로 거부한다. SSM 에이전트(root)는 그대로 쓴다.
- `up.py`는 SSM이 인스턴스를 Online으로 보면 조직 registration token을 받아 bootstrap.sh를 SSM Run Command로 root로 실행한다. token은 출력하지 않고 명령줄에도 넣지 않는다.
- [scripts/github-runner/down.py](../../scripts/github-runner/down.py)는 기본으로 지울 것만 출력하고, `--apply`를 주면 SSM으로 runner 등록을 해제하고(`config.sh remove`), GitHub에 남은 `hopcharlie-runner-1`부터 `3`까지를 API로 지우고, 인스턴스, 보안 그룹, instance profile, 역할을 지운다.

### GCP 호스트(옮긴 호스트)

- 프로젝트 `project-b47cdba3-db5b-42a2-9a6`, 영역 asia-northeast3-a(GKE `charlie-dev-gcp-workload`와 같은 영역)의 VM `hopcharlie-github-runner` 한 대다. e2-standard-4(vCPU 4개, 메모리 16 GB)로 t3.xlarge와 같은 크기이고, Ubuntu 24.04 LTS amd64(image family `ubuntu-2404-lts-amd64`)와 80 GB pd-balanced 부팅 디스크를 쓴다. runner 이름은 `hopcharlie-gcp-runner-1`, `hopcharlie-gcp-runner-2`, `hopcharlie-gcp-runner-3`이다. AWS runner와 이름이 달라서 `config.sh --replace`가 AWS 등록을 덮어쓰지 않고, 옮기는 동안 두 호스트가 함께 job을 받는다.
- [scripts/github-runner/gcp_up.py](../../scripts/github-runner/gcp_up.py)가 활성 gcloud 프로젝트가 위 프로젝트인지 확인한다. 프로젝트를 번호로 설정해 두어도 project ID로 비교하고, 다르면 멈춘다. 그다음 자원을 이름으로 찾고 없으면 만든다.
  - `default` 네트워크에 네트워크 태그 `hopcharlie-github-runner`에만 걸리는 INGRESS 규칙 2개를 VM보다 먼저 만든다. `hopcharlie-github-runner-allow-iap-ssh`는 IAP TCP 전달 대역 35.235.240.0/20의 tcp:22를 우선순위 800으로 허용하고, `hopcharlie-github-runner-deny-ingress`는 0.0.0.0/0의 모든 ingress를 우선순위 900으로 거부한다. 숫자가 작은 규칙이 먼저 맞으므로 우선순위 65534인 `default-allow-ssh`, `default-allow-rdp`, `default-allow-icmp`, `default-allow-internal`은 이 VM에 닿지 않는다.
  - VM은 서비스 계정 없이(`--no-service-account --no-scopes`) 만든다. job 코드가 메타데이터 서버에서 받을 수 있는 GCP 자격 증명이 없다.
  - Shielded VM(secure boot, vTPM, integrity monitoring)을 켠다.
  - 메타데이터 `enable-oslogin=TRUE`, `block-project-ssh-keys=TRUE`로 OS Login만 쓰고 프로젝트 SSH 키는 받지 않는다. 접속은 IAP를 거친 SSH만 쓴다.
  - 임시 외부 IP를 붙인다. `default` 네트워크에 Cloud NAT가 없어서 GitHub, apt, AWS CLI 내려받기에 나가는 길이 외부 IP뿐이다. ingress는 위 방화벽이 막는다.
  - 라벨은 `project=hopcharlie`, `component=github-runner`, `managed-by=infra-scripts-github-runner`다.
  - 이미 있는 VM에 서비스 계정이 붙어 있거나, 태그가 없거나, `default`가 아닌 네트워크에 있으면 다시 쓰지 않고 멈춘다. 같은 이름의 방화벽 규칙이 위 설정과 다를 때도 멈춘다. 멈춘 VM은 시작한다.
- VM이 RUNNING이 되고 IAP SSH가 되면 조직 registration token을 받는다. `gcloud compute scp --tunnel-through-iap`로 bootstrap.sh와 token 파일을 OS Login 사용자 홈의 모드 700 폴더 `hopcharlie-runner-bootstrap`에 보내고, `gcloud compute ssh --tunnel-through-iap --command 'sudo env RUNNER_TOKEN_FILE=... RUNNER_NAME_PREFIX=hopcharlie-gcp-runner ... bash .../bootstrap.sh'`로 실행한다.
  - token은 출력하지 않고 명령줄, VM 메타데이터, 로그에 넣지 않는다. 명령줄과 sudo 로그에는 파일 경로만 남는다. 로컬 token 파일은 임시 폴더에 0600으로 만들고 복사한 뒤 지우며, bootstrap.sh는 VM의 파일을 읽은 뒤 바로 지운다.
  - Windows의 gcloud는 PuTTY(plink, pscp)를 쓴다. `--quiet`이면 gcloud가 SSH 키를 암호 없이 만들고, gcloud 속성 `ssh/putty_force_connect`(기본값 true)에 따라 plink와 pscp의 호스트 키 질문에 스스로 답한다(SDK 592 소스로 확인). 그래서 사람 입력 없이 돈다.
  - Windows의 `gcloud.cmd`는 `where python`이 찾은 마지막 Python 3를 쓰는데, Git Bash에서는 MSYS2 python이 골라져 gcloud가 LayoutException으로 죽는다(2026-11-04 이 PC에서 확인). 그래서 `CLOUDSDK_PYTHON`이 비어 있으면 스크립트를 실행한 python으로 정한다.
- [scripts/github-runner/gcp_down.py](../../scripts/github-runner/gcp_down.py)는 기본으로 지울 것만 출력하고, `--apply`를 주면 VM을 디스크와 함께 지우고, 이름이 `hopcharlie-gcp-runner-`로 시작하는 조직 runner를 API로 지우고, 방화벽 규칙 2개를 지운다. AWS runner는 건드리지 않는다.
- GCP로 옮기는 이유는 다음과 같다.
  - 사용자가 GCP로 옮기기로 했다.
  - VM에 서비스 계정이 없어서 job 코드가 호스트에서 얻을 수 있는 클라우드 자격 증명이 없다. AWS 호스트에는 SSM 에이전트용 인스턴스 역할이 있다.
  - token을 IAP SSH로 파일로 보내므로 SSM 명령 기록(30일 보관) 같은 명령 기록에 남지 않는다.
  - GCP 프로젝트의 vCPU, 디스크, 외부 IP 할당량에 여유가 있다.
- 옮기는 순서는 다음과 같다.
  1. `python scripts/github-runner/gcp_up.py --dry-run`으로 만들 자원을 확인하고 `python scripts/github-runner/gcp_up.py`를 실행한다.
  2. 조직 runner 목록에서 `hopcharlie-gcp-runner-1`부터 `3`까지 `online`인지 확인하고, "Set up job" 로그의 Runner name이 `hopcharlie-gcp-runner-N`인 job이 성공하는지 본다. docker를 쓰는 job(platform CI, 이미지 게시)도 하나 이상 확인한다.
  3. `python scripts/github-runner/down.py`로 지울 AWS 자원을 확인하고 `python scripts/github-runner/down.py --apply`로 AWS 호스트와 `hopcharlie-runner-1`부터 `3`까지의 등록을 지운다. AWS runner가 그때 돌리던 job은 실패하므로 다시 실행한다.

### bootstrap.sh(두 호스트 공통)

- bootstrap.sh는 root로 돌고 다시 실행해도 안전하며 다음을 한다.
  - token은 `RUNNER_TOKEN` 환경 변수(AWS)나 `RUNNER_TOKEN_FILE`이 가리키는 파일(GCP)로 받는다. 파일은 읽은 뒤 지운다.
  - runner 이름은 `RUNNER_NAME_PREFIX`(기본값 `hopcharlie-runner`) 뒤에 번호를 붙인다. AWS 경로는 값을 넘기지 않아서 이름이 그대로다.
  - Ubuntu 패키지의 docker.io와 docker-buildx(2026-11-04 기준 29.1과 0.30, `--metadata-file`, `--provenance` 지원), AWS CLI v2(공식 zip), gh(공식 apt 저장소), git, curl, jq, unzip, zip, zstd, xz-utils, python3, python3-venv, python3-pip, build-essential을 설치한다.
  - Docker를 classic image store로 둔다. Docker 29는 새로 설치하면 containerd image store를 쓰는데, 그러면 frontend가 게시하는 이미지에 attestation이 붙어 지금 GitHub-hosted에서 만드는 단일 manifest와 달라진다.
  - `runner` 사용자를 docker 그룹에 넣는다. sudo 권한은 주지 않는다.
  - 8 GiB swap 파일을 만든다. 16 GiB 메모리를 job 3개가 나눠 쓰므로 OOM killer가 runner를 죽이지 않게 한다.
  - apt가 잠금을 600초까지 기다리게 하고, `apt-daily`와 `apt-daily-upgrade` 타이머를 끈다. 자동 업그레이드가 job 도중 dockerd를 재시작하지 않게 하려는 것이다. 다시 실행할 때도 이미 설치한 패키지는 올리지 않는다.
  - `runner` 사용자가 169.254.169.254로 보내는 요청을 iptables로 거부한다. AWS의 IMDS와 GCP의 메타데이터 서버가 같은 주소다. root(SSM 에이전트, Google guest agent)는 그대로 쓴다. 요약에 `runner` 사용자가 github.com 이름을 찾는지 출력한다. GCP 메타데이터 서버는 DNS 서버도 겸하기 때문이다.
  - actions/runner 최신 release를 release 노트의 sha256으로 확인하고 `/opt/actions-runner/1`부터 `3`까지 설치해 systemd 서비스로 등록한다. token은 `ACTIONS_RUNNER_INPUT_TOKEN` 환경 변수로 넘겨 프로세스 목록에 나오지 않게 한다. 이미 설정한 runner는 건너뛴다.
  - 매일 04:00(KST)에 하루 지난 Docker 이미지와 빌드 캐시, 쓰지 않는 볼륨을 지운다.

### workflow

- infra, platform, frontend, cli, rin의 모든 job을 `runs-on: [self-hosted, hopcharlie]`로 바꾼다. 공개 저장소 moimroom은 GitHub-hosted에 그대로 둔다. 여러 job이 한 호스트를 함께 쓰므로 workflow는 다음을 지킨다. `$RUNNER_TEMP`는 job이 끝나면 비워진다.
  - 토큰을 넣는 git 설정은 `GIT_CONFIG_GLOBAL`을 job의 `$RUNNER_TEMP` 안 파일로 바꿔서 쓴다. runner 사용자의 `~/.gitconfig`에 남기지 않는다.
  - `docker login`은 `DOCKER_CONFIG`를 job의 `$RUNNER_TEMP` 아래로 바꿔서 한다. buildx 플러그인은 시스템 경로(`/usr/libexec/docker/cli-plugins`)에 있어서 그대로 찾는다.
  - 고정 이름과 포트를 쓰지 않는다. platform CI의 PostgreSQL은 run마다 다른 컨테이너 이름과 127.0.0.1의 임의 포트로 띄우고, job 끝에 `if: always()`로 지운다.
  - 호스트에 없는 도구는 workflow가 설치한다. infra `scripts` job에 helm(helm job과 같은 v3.19.1)과 node 22 setup 액션을 넣는다.
  - platform과 cli의 Go 캐시 복원과 저장(`actions/cache`)은 GitHub-hosted runner에서만 한다(`runner.environment == 'github-hosted'`). 호스트의 `~/go`와 `~/.cache/go-build`가 job 사이에 남아 캐시 역할을 하고, 그 위에 tar로 복원하면 동시에 도는 다른 job의 go와 부딪친다. 이미 있는 모듈 폴더는 읽기 전용이라 tar가 "Cannot open: File exists"로 실패한다(로컬 tar로 확인).

## 영향과 후속 작업

- 기대 효과: private 저장소의 CI, 이미지 게시, infra 이미지 고정이 GitHub 과금과 상관없이 다시 돈다. GCP로 옮긴 뒤에는 runner 호스트에 클라우드 자격 증명이 없다.
- 단점과 위험(두 호스트 공통):
  - 호스트는 job 사이에 초기화되지 않는다. 작업 폴더(`_work`), Docker 이미지와 빌드 캐시, `runner` 사용자 홈(`~/go`, `~/.cache`, `~/.npm`)이 다음 job과 다른 runner에 남는다. 앞의 job이 남긴 파일이 다음 job에 영향을 줄 수 있다.
  - job끼리 격리되지 않는다. GCP로 옮겨도 세 runner가 같은 호스트와 같은 `runner` 사용자로 돌고 docker 그룹은 사실상 root라서, 한 job이 동시에 도는 다른 job의 환경 변수(OIDC로 받은 AWS 자격 증명 등)를 읽을 수 있다. 저장소가 모두 private이고 팀원만 push한다는 전제로 받아들인다.
  - 자동 보안 업데이트를 끄므로 해커톤 동안 호스트 패키지는 설치한 시점의 버전으로 남는다. ingress가 없고 며칠만 쓰는 호스트라 받아들인다.
  - 매일 정리에서 `until=24h`는 이미지의 생성 시각을 보므로, 쓰지 않는 기반 이미지(postgres, caddy, node)도 매일 지워지고 다음 job이 다시 받는다.
  - 호스트 한 대가 단일 장애점이다. 호스트가 멈추면 job은 `queued`로 남고 24시간 뒤 실패한다. 동시에 3개를 넘는 job도 기다리고, infra push 하나가 job 17개쯤을 줄 세운다.
  - platform CI는 PostgreSQL 포트가 run마다 달라서 DB를 쓰는 테스트 패키지는 go test 결과 캐시를 쓰지 못하고 매번 다시 돈다.
- 단점과 위험(AWS 호스트):
  - 인스턴스 역할에는 `AmazonSSMManagedInstanceCore` 대신, 그 정책에서 `ssm:GetParameter`와 `ssm:GetParameters`를 뺀 인라인 정책 `ssm-agent`만 둔다. job 코드가 hop limit과 iptables 규칙을 우회해(docker 그룹 권한으로 호스트 네트워크 컨테이너를 띄우는 식) 인스턴스 역할 자격 증명을 얻어도 `/hopcharlie/*` SSM 파라미터는 읽지 못한다. 남는 권한은 SSM 에이전트의 메시지 채널과 상태 보고뿐이다.
  - registration token은 SSM 명령 파라미터(명령 기록, 30일 보관)와 인스턴스의 SSM 작업 폴더에 남는다. token은 1시간 뒤 만료된다.
  - 비용: t3.xlarge 온디맨드가 서울에서 시간당 0.208달러로 하루 약 5달러, 80 GiB gp3가 하루 약 0.24달러, public IPv4가 하루 약 0.12달러다. t3 unlimited 모드라 CPU 크레딧을 넘겨 쓰면 추가 요금이 붙는다.
- 단점과 위험(GCP 호스트):
  - VM은 `default` 네트워크의 임시 외부 IP로 어디로든 나갈 수 있다. egress는 막지 않으므로 job 코드가 외부로 데이터를 보낼 수 있는 것은 AWS 호스트와 같다. 외부 IP는 VM을 멈췄다 켜면 바뀐다. GitHub-hosted 때도 나가는 IP가 매번 달랐으므로 IP 허용 목록에 기대는 workflow는 없다고 본다.
  - VM에는 클라우드 자격 증명이 없다. workflow가 AWS에 접근하는 길은 전과 같이 GitHub OIDC로 받는 자격 증명(`aws-actions/configure-aws-credentials`)뿐이고, GCP에는 전과 같이 GitHub WIF(`google-github-actions/auth`)로 접근한다. 이 자격 증명이 job 환경 변수와 파일에 있어서 동시에 도는 다른 job이 읽을 수 있는 위험은 그대로다.
  - iptables 규칙은 호스트에서 도는 `runner` 사용자만 막고 job의 컨테이너는 막지 않는다. GCP에는 IMDS hop limit 같은 설정이 없어서 컨테이너가 메타데이터 서버에 닿는다. 서비스 계정이 없으므로 컨테이너가 얻는 것은 인스턴스와 프로젝트 메타데이터뿐이고, token은 메타데이터에 넣지 않는다.
  - GCP 메타데이터 서버(169.254.169.254)는 VM의 DNS 서버도 겸한다. Ubuntu 이미지에서 `runner` 사용자는 systemd-resolved(127.0.0.53)를 거쳐 이름을 찾으므로 iptables 규칙에 걸리지 않는다고 가정한다. bootstrap 요약의 `runner resolves github.com` 줄로 확인하며, GCP VM에서는 아직 확인하지 않았다.
  - VPC MTU가 1460인데 Docker 기본 bridge는 1500이다. 컨테이너 안에서 큰 응답을 받을 때 path MTU discovery가 막힌 상대와는 연결이 멈출 수 있다. docker build나 PostgreSQL 컨테이너를 쓰는 job이 내려받기에서 멈추면 `/etc/docker/daemon.json`에 `"mtu": 1460`을 넣고 dockerd를 재시작하는 방법을 쓴다. 아직 확인하지 않은 위험이다.
  - bootstrap이 token 파일을 읽기 전에 실패하면 파일이 VM의 모드 700 폴더에 남는다. OS Login 사용자와 root만 읽을 수 있고 token은 1시간 뒤 만료된다.
  - 처음 `gcloud compute ssh`를 실행하면 gcloud가 로컬 SSH 키(`~/.ssh/google_compute_engine`)를 만들고 사용자의 OS Login 프로필에 공개 키를 등록한다. Windows에서는 호스트 키를 자동으로 받아들이므로, IAP가 연결해 준 상대를 VM으로 믿는다.
  - OS Login 사용자가 sudo를 쓰려면 `compute.instances.osAdminLogin` 권한이 필요하다. 사용자가 owner라서 있다고 가정하며 VM에서는 아직 확인하지 않았다.
  - Windows PuTTY 경로는 gcloud SDK 592 소스와 가짜 gcloud로만 확인했고 실제 VM에 연결해 보지 않았다.
  - 비용: e2-standard-4가 서울에서 시간당 약 0.17달러로 하루 약 4달러, 80 GB pd-balanced가 하루 약 0.3달러, 외부 IPv4가 하루 약 0.12달러로 추정한다(가격표를 확인하지 않은 추정). 옮기는 동안은 두 호스트 비용이 함께 든다.
- 필요한 후속 작업 및 검증:
  - 사용자가 `gh auth refresh -h github.com -s admin:org`로 gh 권한을 확인하고, 위 "옮기는 순서"대로 실행한다. bootstrap 요약에서 `hopcharlie-gcp-runner-1`부터 `3`까지 active이고 `runner resolves github.com: yes`인지 본다.
  - GCP runner가 job을 받는 것을 확인한 뒤 AWS 호스트를 `down.py --apply`로 지운다.
  - 해커톤이 끝나면 `python scripts/github-runner/gcp_down.py --apply`로 GCP 호스트를 지우고(AWS 호스트가 남아 있으면 `down.py --apply`도), 해커톤 정리 목록에 넣는다. 12월 1일 이후 GitHub-hosted로 돌아가려면 `runs-on`을 되돌린다.

## 관련 자료

- [scripts/github-runner/gcp_up.py](../../scripts/github-runner/gcp_up.py), [gcp_down.py](../../scripts/github-runner/gcp_down.py), [up.py](../../scripts/github-runner/up.py), [down.py](../../scripts/github-runner/down.py), [bootstrap.sh](../../scripts/github-runner/bootstrap.sh)
- [.github/workflows/terraform-check.yml](../../.github/workflows/terraform-check.yml), [terraform-gcp.yml](../../.github/workflows/terraform-gcp.yml), [platform-terraform.yml](../../.github/workflows/platform-terraform.yml)
- platform `ci.yml`, `publish-image.yml`, frontend `deploy.yml`, cli `ci.yml`, rin `deploy.yml`
- GitHub 문서: [About self-hosted runners](https://docs.github.com/en/actions/hosting-your-own-runners/managing-self-hosted-runners/about-self-hosted-runners), [About billing for GitHub Actions](https://docs.github.com/en/billing/managing-billing-for-your-products/managing-billing-for-github-actions/about-billing-for-github-actions)
- GCP 문서: [Using IAP for TCP forwarding](https://cloud.google.com/iap/docs/using-tcp-forwarding), [VPC firewall rules](https://cloud.google.com/firewall/docs/firewalls), [OS Login](https://cloud.google.com/compute/docs/oslogin/set-up-oslogin), [Maximum transmission unit](https://cloud.google.com/vpc/docs/mtu)
