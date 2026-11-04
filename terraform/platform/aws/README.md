# AWS 관리 호스트

> **2026-11-03: EC2 관리 호스트를 철거했습니다**([ADR 0026](../../../docs/adr/0026-retire-platform-ec2-host.md)). 플랫폼은 관리 EKS에서 Argo CD로 돕니다. 이 root에는 ECR, 생성한 비밀값과 토큰, 플랫폼 pod가 읽는 SSM 파라미터, pod 역할, CodeBuild, 이미지 게시 역할만 남았습니다. 아래의 호스트 구성과 운영 절차는 기록으로 남겨 둡니다.

platform@5cb1c2e의 운영 문서를 이 위치로 이관했다. [이관·인수 절차](../../../docs/platform-terraform-migration.md)를 먼저 확인한다. 아래 구성·비용·배포 보고는 원문 기준이며 이번 소스 이관에서 실제 AWS 상태를 재조회하지 않았다.

Terraform은 이 저장소가 소유하고, 앱 이미지·재배포·kubeconfig helper는 platform에 남는다. 다음 두 checkout을 지정한 상태에서 예시를 실행한다.

```bash
export INFRA_DIR=/absolute/path/to/infra
export PLATFORM_DIR=/absolute/path/to/platform
cd "$INFRA_DIR/terraform/platform/aws"
```


플랫폼 API, 트래픽 게이트웨이, PostgreSQL, Caddy를 EC2 한 대에 Docker Compose로 띄웁니다. Notion에서 정한 "AWS 관리 호스트"를 Terraform으로 만든 것입니다.

## 관리 EKS로 옮기는 중

2026-11-02 팀 회의에서 플랫폼을 infra 레포의 관리 EKS(`charlie-dev-management`)로 옮기기로 했습니다. 클러스터와 그 위의 pod는 infra 레포가 Argo CD로 배포합니다(infra `docs/adr/0014`, `helm/charts/hopcharlie-platform`). 이 폴더는 플랫폼 전용 AWS 리소스를 계속 맡습니다.

| 파일 | 내용 |
| --- | --- |
| `eks.tf` | pod용 IAM 역할과 Pod Identity 연결 (`charlie-platform/hopcharlie-platform`). 권한은 호스트 역할과 같다 |
| `dns/` | `*.hopcharlie.dev` ACM 인증서와 Cloudflare 레코드. Cloudflare 토큰이 있는 사람만 실행한다 |

pod는 지금과 같은 SSM 파라미터(`/hopcharlie/*`)를 init 컨테이너에서 읽습니다. 그래서 토큰과 비밀값은 그대로이고, 값을 바꾸는 방법도 같습니다(put-parameter 후 pod 재시작).

옮기는 순서는 다음과 같습니다. 각 단계는 apply 전에 팀 승인을 받습니다.

1. `./tf.sh apply`로 pod 역할과 Pod Identity 연결을 만든다.
2. infra PR을 merge하면 Argo CD가 플랫폼을 배포한다. pod가 Ready인지 확인한다.
3. `hopcharlie.dev`의 네임서버가 Cloudflare로 바뀌면 인증서를 발급한다.
   ```bash
   export CLOUDFLARE_API_TOKEN=...   # Zone.DNS:Edit 권한
   ./tf.sh -chdir=dns init
   ./tf.sh -chdir=dns apply -var cloudflare_zone_id=<영역 ID>
   ./tf.sh -chdir=dns output -raw certificate_arn
   ```
4. infra에서 baseline `externalAlb`(인증서 ARN)와 플랫폼 `ingress.enabled`를 켜는 PR을 올린다. ALB가 생기면 레코드를 연결한다.
   ```bash
   ./tf.sh -chdir=dns apply -var cloudflare_zone_id=<영역 ID> -var alb_name=charlie-management-external
   API_URL=https://api.hopcharlie.dev bash "$PLATFORM_DIR/deploy/aws/push-console.sh"
   ```
5. 새 콘솔 이미지의 digest를 infra values에 반영하고, `charlie login --server https://api.hopcharlie.dev`로 확인한 뒤 EC2 호스트를 정리한다.

## 만드는 것

| 리소스 | 내용 |
| --- | --- |
| EC2 | t3.small 1대, Amazon Linux 2023, 기본 VPC, 디스크 gp3 20GiB(암호화) |
| Elastic IP | 고정 공인 IP. `api.<IP>.sslip.io`, `app.<IP>.sslip.io` 도메인으로 쓴다 |
| 보안 그룹 | 80, 443만 연다. SSH 포트는 없고 SSM Session Manager로 들어간다 |
| ECR | `hopcharlie/platform`, `hopcharlie/console` 이미지 저장소 (각각 최근 10개만 보관) |
| IAM | 호스트 역할: SSM 접속, ECR 읽기, `/hopcharlie/*` 파라미터 읽기, 보안 뷰가 부르는 보안 설정 읽기 API만 (`read-security-configuration`) |
| SSM Parameter Store | 호스트 설정 파일(`/hopcharlie/host/*`), 생성한 비밀값(DB 비밀번호, 게이트웨이 관리 토큰, API 토큰), 팀이 넣는 비밀값(아래 "설정 바꾸기") |

호스트 안의 구성은 다음과 같습니다.

- Caddy가 Let's Encrypt 인증서로 HTTPS를 받습니다.
  - `api.<IP>.sslip.io`는 플랫폼 API로 보냅니다.
  - `app.<IP>.sslip.io`는 게이트웨이로 보냅니다.
  - `console.<IP>.sslip.io`는 콘솔(frontend 레포를 빌드한 정적 파일)로 보냅니다. 콘솔 주소는 CORS 허용 목록에 자동으로 들어갑니다.
- 호스트의 설정 파일은 모두 SSM에 있고, `host/sync.sh`가 받아서 `/opt/hopcharlie`에 씁니다. 처음 부팅할 때와 `bash "$PLATFORM_DIR/deploy/aws/redeploy.sh"`를 실행할 때마다 돕니다. 그래서 설정을 바꿔도 EC2가 교체되지 않습니다(user_data는 첫 부팅 뒤 무시합니다).
- 게이트웨이 관리 포트(9901)와 PostgreSQL은 밖으로 열지 않습니다.
- sslip.io는 팀 도메인(Cloudflare)이 생기기 전까지 쓰는 임시 주소입니다.

## 비용

서울 리전 온디맨드 기준입니다.

| 항목 | 비용 |
| --- | --- |
| t3.small | 시간당 약 0.026달러 |
| 공인 IPv4 | 시간당 0.005달러 |
| 디스크 | 하루 약 0.06달러 |
| 합계 | 하루 약 0.8달러, 11/8까지 6달러 미만 |

계정의 EC2 vCPU 할당량 5개 중 2개를 씁니다.

## 준비

팀원 누구나 같은 상태로 계획하고 적용합니다. 필요한 것은 다음과 같습니다.

- Docker, Go, AWS CLI v2.9 이상
- 해커톤 계정(525091405886)용 AWS CLI 프로필. 액세스 키, `aws login`, SSO 중 무엇이든 됩니다.

Terraform은 설치하지 않아도 됩니다. `tf.sh`가 공식 Docker 이미지로 실행하고, 지정한 프로필의 자격 증명만 컨테이너에 넘깁니다. 프로필이 다른 계정을 가리키면 Terraform을 실행하기 전에 멈춥니다. 프로필 이름이 `hopcharlie`가 아니면 `AWS_PROFILE`로 지정합니다.

```bash
cd "$INFRA_DIR/terraform/platform/aws"
export AWS_PROFILE=<내 해커톤 프로필>   # 기본값 hopcharlie
./tf.sh init
./tf.sh plan
```

## 상태 파일

| 상태 | 위치 |
| --- | --- |
| 관리 호스트 (이 폴더) | `s3://hopcharlie-tfstate-525091405886-apne2/platform/management-host/terraform.tfstate` |
| 상태 버킷 자체 (`state/`) | `s3://hopcharlie-tfstate-525091405886-apne2/state/terraform.tfstate` |

- 버킷은 버전 관리, 암호화, 공개 차단, HTTPS 전용 정책이 걸려 있고, 실수로 지워지지 않게 `prevent_destroy`로 막아 두었습니다. 지난 버전은 90일 동안 남습니다.
- 잠금은 S3 잠금 파일(`use_lockfile`)로 합니다. 두 사람이 동시에 apply하면 나중 사람은 `Error acquiring the state lock`과 함께 잠금을 가진 사람(`Who:`)을 보게 됩니다. 그 사람의 작업이 끝난 뒤 다시 실행합니다. 강제로 푸는 `force-unlock`은 상대가 확실히 멈춘 경우에만 씁니다.
- 상태에는 생성한 토큰과 DB 비밀번호가 들어 있습니다. 로컬에 내려받거나 다른 곳에 복사하지 않습니다.
- 버킷 스택을 처음부터 다시 만들어야 하면 `state/backend.tf`의 주석을 따릅니다.

## 배포

```bash
./tf.sh apply -target=aws_ecr_repository.platform -target=aws_ecr_repository.console   # 이미지 저장소 먼저 (처음 한 번)
bash "$PLATFORM_DIR/deploy/aws/push-image.sh"                                       # 플랫폼 이미지 빌드, 푸시
bash "$PLATFORM_DIR/deploy/aws/push-console.sh"                                     # 콘솔 빌드, 푸시 (frontend 레포가 platform 옆에 있어야 함)
./tf.sh apply                                         # 나머지 전부
./tf.sh output api_url
```

호스트는 처음 부팅할 때 Docker를 설치하고, 이미지를 받아 Compose를 띄웁니다. 3분에서 5분쯤 걸립니다.

## 토큰

토큰은 Terraform이 만들고, 원격 상태와 SSM에만 있습니다.

```bash
./tf.sh output -json operator_tokens   # 사람마다 operator 토큰 하나
./tf.sh output -raw planner_token      # AI 플래너용 agent 토큰
./tf.sh output -raw viewer_token       # 화면용 읽기 전용 토큰
charlie login --server "$(./tf.sh output -raw api_url)"
```

각자에게는 자기 토큰만 전달합니다. Slack 채널이나 Notion에는 올리지 않습니다.

## 코드 바꾼 뒤 다시 배포

```bash
bash "$PLATFORM_DIR/deploy/aws/push-image.sh"     # platform 코드가 바뀌었을 때
bash "$PLATFORM_DIR/deploy/aws/push-console.sh"   # frontend 코드가 바뀌었을 때
bash "$PLATFORM_DIR/deploy/aws/redeploy.sh"       # SSM으로 호스트에서 sync.sh 실행 (설정 받기, docker compose pull, up -d)
```

## 설정 바꾸기

Terraform이 관리하는 설정(`host/compose.yaml`, `host/Caddyfile`, `host/envs.yaml`, `host/apps.yaml`, 변수)은 `./tf.sh apply` 다음 `bash "$PLATFORM_DIR/deploy/aws/redeploy.sh"`로 반영합니다.

팀이 직접 넣는 비밀값은 SSM SecureString에 두고, 바꾼 뒤 `bash "$PLATFORM_DIR/deploy/aws/redeploy.sh"`를 실행합니다. Terraform은 이 값을 만들기만 하고 apply할 때 덮어쓰지 않습니다. 값은 Slack이나 Notion에 올리지 않습니다.

| 파라미터 | 내용 | 넣는 방법 |
| --- | --- | --- |
| `/hopcharlie/db-passwords` | Plan이 참조하는 DB 비밀번호. 한 줄에 `이름=값` (`SRC_PGPASSWORD`, `GKE_MOIMROOM_PGPASSWORD` 등) | 아래 명령 |
| `/hopcharlie/rclone-env` | 파일 이전에 쓰는 rclone 원격. 한 줄에 `RCLONE_CONFIG_<원격>_<옵션>=값` | 아래 명령 |
| `/hopcharlie/kube/<이름>` | 클러스터마다 kubeconfig 하나. 워크로드 EKS(`eks`)는 infra의 `scripts/put-platform-kubeconfig.py`가 넣음 ([ADR 0036](../../../docs/adr/0036-app-routing-and-argo-apps.md)) | `bash "$PLATFORM_DIR/deploy/aws/put-kubeconfig.sh"` |
| `/hopcharlie/tailscale-authkey` | 있으면 호스트가 Tailscale에 붙음 | 아래 명령 |
| `/hopcharlie/github-app-private-key` | GitHub App `hopcharlie-platform`의 private key PEM. 관리 EKS의 API pod만 읽음 ([ADR 0021](../../../docs/adr/0021-configure-platform-github-app-deploy.md)) | 아래 명령 |
| `/hopcharlie/github-webhook-secret` | 같은 App의 webhook secret. App 설정의 값과 같아야 함 | 아래 명령 |
| `/hopcharlie/platform-admins` | 콘솔 관리자 계정. `이름:bcrypt 해시`를 쉼표로 구분. 관리 EKS의 API pod가 `PLATFORM_ADMINS`로 읽음 ([ADR 0033](../../../docs/adr/0033-platform-admin-accounts-from-ssm.md)) | 아래 관리자 계정 명령 |

```bash
# 파일 내용을 그대로 넣는다. 파일은 넣은 뒤 지운다.
aws ssm put-parameter --profile hopcharlie --region ap-northeast-2 --overwrite \
  --name /hopcharlie/db-passwords --type SecureString --value file://db-passwords.env
aws ssm put-parameter --profile hopcharlie --region ap-northeast-2 --overwrite \
  --name /hopcharlie/rclone-env --type SecureString --value file://rclone.env
aws ssm put-parameter --profile hopcharlie --region ap-northeast-2 \
  --name /hopcharlie/tailscale-authkey --type SecureString --value 'tskey-auth-...'
aws ssm put-parameter --profile hopcharlie --region ap-northeast-2 \
  --name /hopcharlie/github-app-private-key --type SecureString --value file://github-app.pem
aws ssm put-parameter --profile hopcharlie --region ap-northeast-2 \
  --name /hopcharlie/github-webhook-secret --type SecureString --value file://github-webhook-secret.txt
bash "$PLATFORM_DIR/deploy/aws/redeploy.sh"
```

GitHub App 파라미터 두 개는 관리 EKS의 API pod가 시작할 때만 읽습니다. 차트에 `github.appId`를 넣기 전에 만들어 두고, 값을 바꾼 뒤에는 `hopcharlie-api` Deployment를 다시 시작합니다.

관리자 계정은 platform 저장소에서 비밀번호의 bcrypt 해시를 만들어 넣습니다. 비밀번호는 해시를 만들 때만 입력하고, SSM에는 해시만 들어갑니다. 이 파라미터가 없으면 API pod가 시작하지 않으므로 차트보다 먼저 만들고, 바꾼 뒤에는 `hopcharlie-api` Deployment를 다시 시작합니다.

- 값은 `이름:해시`를 쉼표로 이은 목록 전체입니다. `--overwrite`는 값을 통째로 바꾸므로, 관리자를 추가하거나 비밀번호를 바꿀 때는 먼저 `aws ssm get-parameter --with-decryption`으로 지금 값을 읽고, 항목을 더하거나 바꾼 목록 전체를 넣습니다.
- 같은 이름이 두 번 있거나, 해시 형식이 틀리거나, 관리자마다 bcrypt cost가 다르면 API가 시작하지 않습니다. `-hash-password`로 만든 해시만 씁니다.
- 비밀번호 입력은 터미널에서만 가려집니다. Windows의 Git Bash(mintty)에서는 입력한 비밀번호가 화면에 보일 수 있으므로 PowerShell에서 실행합니다.

```powershell
# PowerShell, platform 저장소에서. 관리자 한 명으로 처음 만드는 경우
$hash = go run ./cmd/api -hash-password   # 비밀번호를 묻는다. 입력은 보이지 않는다
aws ssm put-parameter --profile hopcharlie --region ap-northeast-2 --overwrite `
  --name /hopcharlie/platform-admins --type SecureString --value "<아이디>:$hash"
Remove-Variable hash
```

Linux와 macOS 터미널에서는 같은 일을 이렇게 합니다.

```bash
cd "$PLATFORM_DIR"
hash="$(go run ./cmd/api -hash-password)"   # 비밀번호를 묻는다. 입력은 보이지 않는다
aws ssm put-parameter --profile hopcharlie --region ap-northeast-2 --overwrite \
  --name /hopcharlie/platform-admins --type SecureString --value "<아이디>:$hash"
unset hash
```

### 클러스터 연결

호스트에는 aws CLI나 gke-gcloud-auth-plugin 같은 인증 플러그인이 없습니다. 그래서 클러스터마다 ServiceAccount 토큰으로 된 kubeconfig를 만들어 넣습니다.

```bash
# 그 클러스터의 관리자 권한 kubectl 컨텍스트에서
kubeconfig_file="$PLATFORM_DIR/deploy/aws/gke.kubeconfig"
bash "$PLATFORM_DIR/deploy/k8s/make-kubeconfig.sh" gke-hopcharlie "$kubeconfig_file" moimroom   # 컨텍스트 이름은 host/envs.yaml과 같게
bash "$PLATFORM_DIR/deploy/aws/put-kubeconfig.sh" gke "$kubeconfig_file"
rm -- "$kubeconfig_file"
bash "$PLATFORM_DIR/deploy/aws/redeploy.sh"
```

- 권한은 `platform의 deploy/k8s/hopcharlie-platform.yaml`에 있습니다. 클러스터 전체에서는 읽기만 하고(환경 상태, 보안 뷰), 이전에 필요한 변경 권한은 앱 namespace에서만 받습니다.
- 클러스터 API가 private 주소라면, 호스트가 닿는 주소를 `SERVER=https://...`로 지정해서 kubeconfig를 만듭니다. 이때 호스트는 Tailscale로 닿습니다.
- 토큰을 끊으려면 그 클러스터에서 `kubectl delete secret hopcharlie-platform-token -n hopcharlie-system`을 실행합니다.

### Tailscale

`/hopcharlie/tailscale-authkey`가 있으면 `sync.sh`가 Tailscale을 설치하고 `hopcharlie-mgmt`라는 이름으로 tailnet에 붙습니다. 이때 API와 게이트웨이 컨테이너는 MagicDNS(100.100.100.100)로 이름을 찾으므로, Plan의 `*.ts.net` DB 주소와 게이트웨이 백엔드 주소에 닿습니다. 일회용이 아닌 재사용 가능 키를 쓰고, 호스트를 다시 만들 일이 없으면 만료 기간은 짧게 둡니다. 실제 tailnet에서 아직 확인하지 않았으므로 처음 붙일 때 `tailscale status`와 컨테이너 안의 이름 해석을 확인합니다.

## 호스트에 들어가기

```bash
aws ssm start-session --profile hopcharlie --region ap-northeast-2 --target "$(./tf.sh output -raw instance_id)"
sudo -i; cd /opt/hopcharlie; docker compose ps; docker compose logs -f api
```

- 설정 파일은 `/opt/hopcharlie/.env`, `db-passwords.env`, `rclone.env`(비밀값, 0600), `compose.yaml`, `Caddyfile`, `envs.yaml`, `apps.yaml`, `kube/<이름>`입니다. 모두 `sync.sh`가 SSM에서 받아 쓰므로 호스트에서 직접 고치지 않습니다. 직접 고친 내용은 다음 `bash "$PLATFORM_DIR/deploy/aws/redeploy.sh"` 때 사라집니다.
- 마지막 동기화 기록은 `/var/log/hopcharlie-sync.log`에 있습니다.
- 게이트웨이 백엔드는 Terraform 변수 `gateway_backends`입니다. 클러스터가 생기면 실제 주소로 바꾸고 `./tf.sh apply`, `bash "$PLATFORM_DIR/deploy/aws/redeploy.sh"` 순서로 반영합니다.
- 게이트웨이가 쓰기를 붙잡은 채 엔진이 멈췄다면, 상태를 확인한 뒤 호스트에서 직접 풀 수 있습니다.

```bash
docker compose exec gateway sh -c 'wget -qO- --header "Authorization: Bearer $GATEWAY_ADMIN_TOKEN" --post-data "" http://localhost:9901/v1/writes/release'
```

## 정리

```bash
./tf.sh destroy
```

ECR 이미지, Elastic IP, SSM 파라미터까지 모두 지웁니다. PostgreSQL 데이터도 함께 사라집니다.

상태 버킷은 `prevent_destroy`로 막혀 있습니다. 해커톤이 끝나고 정말 지울 때만 `state/main.tf`의 `prevent_destroy`를 풀고, 버킷 안의 모든 객체 버전을 비운 뒤 `./tf.sh -chdir=state destroy`를 실행합니다. 관리 호스트를 먼저 destroy해야 합니다.

## 앱 이미지 빌드 준비

`app-build.tf`는 전용 CodeBuild 프로젝트, 역할·정책과 3일 보존 로그 그룹을 추가한다. 플랫폼 Pod Identity에는 해당 프로젝트 시작·상태와 로그 조회 권한만 추가한다. 플랫폼이 빌드마다 buildspec과 GitHub token을 전달해야 하며 기본 buildspec은 실패한다. 권한 범위와 운영 전 확인 사항은 [ADR 0022](../../../docs/adr/0022-platform-app-build.md)를 참조한다. 실제 계정 및 state는 확인하지 않았고 plan/apply도 실행하지 않았다.

## 앱 버킷과 시크릿

platform ADR 0016(앱마다 버킷과 그 버킷 전용 키)과 ADR 0019(앱 시크릿)에 필요한 것을 이 root와 플랫폼 차트가 준비한다. 결정 이유와 위험은 [ADR 0035](../../../docs/adr/0035-app-storage-and-secrets-infra.md)에 있다.

| 리소스 | 내용 |
| --- | --- |
| `app-storage.tf` `aws_iam_role_policy.platform_app_storage` | 플랫폼 pod 역할이 platform의 aws 저장소 드라이버로 이 계정의 `hopcharlie-eks-*` 버킷을 만들고 비우고 지우며, `/hopcharlie-apps/` 경로의 IAM 사용자를 boundary를 붙일 때만 만들고 그 사용자의 인라인 정책과 액세스 키를 관리한다 |
| `app-storage.tf` `aws_iam_policy.app_boundary` | 그 사용자의 permissions boundary `hopcharlie-app-boundary`. 이 계정의 `hopcharlie-eks-*` 버킷에서 객체 사용과 목록만 허용한다 |
| `app-storage.tf` `aws_ssm_parameter.app_secrets_key` | 앱 시크릿 암호화 키. 아래 표 |

`/hopcharlie-apps/` 경로에는 플랫폼만 사용자를 만든다. 사람이나 다른 도구가 이 경로에 사용자를 만들지 않는다.

| 파라미터 | 내용 | 만드는 방법 |
| --- | --- | --- |
| `/hopcharlie/app-secrets-key` | SecureString. 32바이트를 base64로. 파라미터가 있으면 API의 init 컨테이너가 `api.env`에 `PLATFORM_SECRETS_KEY`로 쓴다. 파라미터가 없으면 쓰지 않고, 그동안 시크릿 API는 503을 돌려준다 | Terraform(`random_bytes`). 값은 이 파라미터와 state에만 있다. `./tf.sh destroy`는 이 파라미터도 지운다 |
| `/hopcharlie/sealed-secrets/<환경>` | String. 그 환경 클러스터의 Sealed Secrets 공개 인증서. API의 init 컨테이너가 `/config/sealed-secrets/<환경>.pem`으로 복사한다 | `scripts/put-sealed-secrets-cert.sh`(지금은 `eks`만) |

### 적용 순서

플랫폼 API는 envs.yaml의 모르는 필드를 거부한다. 그래서 envs.yaml에 `sealedSecretsCert`와 `storage`를 넣는 변경은 별도 PR(브랜치 `feat/app-storage-envs`)로 나누었고, 그 필드를 읽는 플랫폼 이미지가 돈 뒤에 넣는다. 이 PR은 플랫폼 이미지보다 먼저 merge해도 된다. 각 단계는 실행 전에 승인을 받는다.

1. 이 PR을 merge한다. Argo CD가 AppProject `hopcharlie-apps`(SealedSecret 허용), 앱 차트, 플랫폼 차트의 `fetch-settings.sh`를 동기화한다. API pod는 다시 시작하지 않는다. 다시 시작하더라도 키와 인증서가 없으면 쓰지 않을 뿐 실패하지 않는다.
2. main에서 Terraform을 적용한다.
   ```bash
   cd "$INFRA_DIR/terraform/platform/aws"
   git switch main && git pull --ff-only
   ./tf.sh init
   ./tf.sh plan
   ```
   plan은 `aws_iam_policy.app_boundary`, `aws_iam_role_policy.platform_app_storage`, `random_bytes.app_secrets_key`, `aws_ssm_parameter.app_secrets_key`의 생성만 보여야 한다. `host_file`의 변경이나 삭제, 교체가 있으면 멈춘다. 확인했으면 `./tf.sh apply`로 적용한다.
3. 워크로드 클러스터의 인증서를 SSM에 넣는다. 이 단계는 1, 2와 순서가 바뀌어도 된다. 터널은 [Private EKS 접근](../../../README.md#private-eks-접근과-argo-cd-설치)을 따른다.
   ```bash
   # 터미널 A, infra 저장소 루트. 세션이 시작되면 켜 둔다.
   make aws-tunnel CLUSTER=workload
   # 터미널 B, infra 저장소 루트. 해커톤 계정이 아닌 프로필은 거부한다.
   AWS_PROFILE=hopcharlie bash scripts/put-sealed-secrets-cert.sh
   ```
   subject, 만료일, SHA-256 지문을 출력하고 `stored /hopcharlie/sealed-secrets/eks`로 끝나야 한다.
4. Argo CD bootstrap에 SealedSecret health 검사를 넣는다. bootstrap은 Helm으로 설치되어 있고, GKE 연결 때 넣은 값을 지키려고 `--reuse-values`로 올린다([GCP 운영 접근](../../../docs/gcp-operational-access.md)의 bootstrap upgrade와 같은 방법). dry-run에서 `argocd-cm`의 키 하나 말고 다른 차이가 보이면 멈춘다.
   ```bash
   # 터미널 A: make aws-tunnel (관리 클러스터)
   helm dependency build helm/bootstrap
   helm upgrade argocd helm/bootstrap --reuse-values --no-hooks \
     --kubeconfig .generated/access/management.kubeconfig --kube-context charlie-dev-management \
     --namespace argocd --dry-run=server --hide-secret
   helm upgrade argocd helm/bootstrap --reuse-values --no-hooks \
     --kubeconfig .generated/access/management.kubeconfig --kube-context charlie-dev-management \
     --namespace argocd --wait --timeout 5m
   ```
   이 단계 전에는 설치된 Argo CD의 내장 검사가 쓰인다. 첫 시크릿 앱을 배포하기 전에 한다.
5. platform ADR 0016, 0019 구현이 들어간 플랫폼 이미지가 infra main에 고정되고 API가 그 이미지로 도는지 확인한다([hopcharlie-platform values](../../../clusters/aws-dev-management/values/hopcharlie-platform.yaml)의 `images.platform` 태그). 이 이미지는 envs.yaml에 `storage`가 없으면 s3 앱 등록에 503을 돌려주며 그 밖에는 그대로 동작한다.
6. envs PR(`feat/app-storage-envs`)을 merge하고 main에서 Terraform을 다시 적용한다. plan은 `aws_ssm_parameter.host_file["envs.yaml"]`의 변경만 보여야 한다.
   ```bash
   git switch main && git pull --ff-only
   ./tf.sh plan
   ./tf.sh apply
   ```
7. API를 다시 시작해 키, 인증서, envs.yaml을 읽힌다.
   ```bash
   kubectl --kubeconfig .generated/access/management.kubeconfig --context charlie-dev-management \
     -n charlie-platform rollout restart deployment/hopcharlie-api
   kubectl --kubeconfig .generated/access/management.kubeconfig --context charlie-dev-management \
     -n charlie-platform rollout status deployment/hopcharlie-api
   ```
8. 확인한다. init 컨테이너 로그가 `fetched settings for api`로 끝나고, `charlie env list`에 `eks`가 보이며, 시크릿 API(`charlie app secret list <앱>`)가 503 `secrets_not_configured`를 돌려주지 않아야 한다. s3 리소스가 있는 앱을 배포하면 워크로드 클러스터에 SealedSecret `app-s3`가 Healthy가 되고 버킷 `hopcharlie-eks-<인스턴스>`가 생긴다.

되돌릴 때는 envs.yaml 변경을 되돌리는 커밋을 main에 넣고 `./tf.sh apply`, API 재시작 순서로 한다. 시크릿 키는 지우지 않는다. 지우면 저장된 시크릿을 되살릴 수 없다.
