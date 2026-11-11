# moimroom-legacy 시험용 VM

우리 플랫폼에 한 번도 올라온 적 없는 고객의 기존 앱을 흉내 내는 대상이다. platform ADR 0042(compose로 돌던 앱 가져오기)를 실제 AWS에서 시험할 때 쓴다. 결정은 [ADR 0093](../../../../docs/adr/0093-add-legacy-moimroom-fixture-vm.md)에 있다.

- 앱 레포 `moimroom-legacy`: moimroom 사본에서 플랫폼 파일을 빼고, `compose.yaml`(app, `postgres:17.11-alpine`, Caddy)과 ssh 배포 workflow만 둔 레포다. 레포 안에는 플랫폼, 시험, 이전을 언급하지 않는다.
- 이 root가 만드는 것: 서울 기본 VPC 공개 서브넷의 Ubuntu 24.04 t3.small, Elastic IP, SG(80/443 전체, 22는 러너만), SSM 전용 instance profile, 사진 버킷 `moimroom-legacy-photos-<account>`, 그 버킷만 쓰는 IAM 사용자 `/fixtures/moimroom-legacy-app`과 접근 키.
- 만들지 않는 것: DNS 레코드(사람이 Cloudflare에서 만든다), GitHub 레포와 secret.
- state: `charlie-tfstate-525091405886-ap-northeast-2`의 `fixtures/aws/legacy-moimroom/terraform.tfstate`. 접근 키 비밀값이 state에 남는다.
- 비용: 하루 약 0.8 USD(t3.small 0.62, 공인 IPv4 0.12, gp3 20GiB 0.06, S3는 무시할 수준).

## 러너 공인 IP 찾기

배포 workflow는 `runs-on: [self-hosted, hopcharlie]`라 label이 `hopcharlie`인 러너 아무 곳에서나 돈다. 그 러너들이 밖으로 나가는 IP를 모두 `/32`로 넣는다.

```bash
# GCP 러너 VM (임시 외부 IP라 VM을 다시 시작하면 바뀐다)
gcloud compute instances describe hopcharlie-github-runner \
  --project project-b47cdba3-db5b-42a2-9a6 --zone asia-northeast3-a \
  --format='value(networkInterfaces[0].accessConfigs[0].natIP)'

# AWS 러너 VM이 떠 있다면
aws ec2 describe-instances --profile hopcharlie --region ap-northeast-2 \
  --filters Name=tag:Name,Values=hopcharlie-github-runner Name=instance-state-name,Values=running \
  --query 'Reservations[].Instances[].PublicIpAddress' --output text

# label이 hopcharlie인 러너 목록
gh api orgs/2026-Softbank-Hackathon-Charlie/actions/runners --jq '.runners[] | [.name, .status, ([.labels[].name] | join(","))] | @tsv'
```

러너 IP가 바뀌면 `terraform.tfvars`의 `ssh_allowed_cidrs`를 고쳐 다시 apply한다.

## 적용

모든 명령은 이 폴더에서 Git Bash로 실행한다. `tf.sh`가 `hashicorp/terraform` 이미지로 Terraform을 돌리고, `AWS_PROFILE`(기본 `hopcharlie`)의 자격 증명만 넘긴다.

```bash
ssh-keygen -t ed25519 -N '' -C deploy@moimroom -f ~/.ssh/moimroom-legacy-deploy
cp terraform.tfvars.example terraform.tfvars
# deploy_ssh_public_key에 ~/.ssh/moimroom-legacy-deploy.pub 내용, ssh_allowed_cidrs에 러너 IP

./tf.sh init
./tf.sh plan
./tf.sh apply
./tf.sh output dns_record
```

user_data는 처음 부팅할 때만 돈다. 끝났는지는 SSM으로 본다.

```bash
aws ssm start-session --profile hopcharlie --target "$(./tf.sh output -raw instance_id)"
# 세션 안에서
cloud-init status --wait && docker compose version && id deploy
```

`ami`와 `user_data` 변경은 무시한다. 배포 키를 바꾸려면 VM의 `/home/deploy/.ssh/authorized_keys`를 고치거나 `./tf.sh apply -replace=aws_instance.vm`(VM과 DB 데이터가 새로 만들어진다)을 쓴다.

## Cloudflare 레코드

`hopcharlie.dev` 영역에 개별 A 레코드를 만든다. 와일드카드 `*.hopcharlie.dev`보다 개별 레코드가 이긴다.

| 종류 | 이름 | 값 | 프록시 | TTL |
| --- | --- | --- | --- | --- |
| A | `moimroom-legacy` | `./tf.sh output -raw public_ip` | DNS only(회색 구름) | Auto |

프록시를 켜면 Caddy의 HTTP-01 인증서 발급이 실패하고, 조사 단계가 VM 대신 Cloudflare IP를 보게 된다.

## 앱 레포 secret

`moimroom-legacy` 레포의 Actions secret에 넣는다.

| Secret | 값 |
| --- | --- |
| `DEPLOY_HOST` | `./tf.sh output -raw public_ip` |
| `DEPLOY_USER` | `deploy` |
| `DEPLOY_SSH_KEY` | `~/.ssh/moimroom-legacy-deploy` 개인 키 내용 |
| `DEPLOY_KNOWN_HOSTS` | (선택) `ssh-keyscan -t ed25519 <public_ip>` 결과 |
| `APP_ENV` | `./tf.sh output -raw app_env` 결과 아래에 `POSTGRES_PASSWORD`(영문과 숫자만), `HOST_CODE`, `ADMIN_TOKEN` 줄을 더한 것 |

```bash
{ ./tf.sh output -raw app_env
  echo "POSTGRES_PASSWORD=$(openssl rand -hex 24)"
  echo "HOST_CODE=$(openssl rand -hex 12)"
  echo "ADMIN_TOKEN=$(openssl rand -hex 32)"
} | gh secret set APP_ENV --repo 2026-Softbank-Hackathon-Charlie/moimroom-legacy
```

## 정리

```bash
./tf.sh destroy
```

버킷은 `force_destroy`라 안의 사진도 함께 지워진다. 그 뒤 Cloudflare의 `moimroom-legacy` A 레코드를 지우고, 앱 레포를 보관하거나 지운다.
