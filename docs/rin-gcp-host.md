# rin 전용 GCP 호스트

현재 단계는 로컬 코드·mock 검증이다. 실제 VM/IAM 생성, 원본 EC2 중지, 데이터 이동, Git 발행은 별도 승인 전 실행하지 않는다.

## 범위와 소유권

- Root: `terraform/environments/gcp/dev/rin`, 기존 GCS bucket의 독립 prefix `gcp/dev/rin`.
- GCP: `project-b47cdba3-db5b-42a2-9a6`, `asia-northeast3-a`, `hopcharlie-rin`, e2-standard-2(2 vCPU/8 GiB), Ubuntu 24.04 x86_64, 40 GiB pd-balanced.
- 기존 default network를 참조한다. 외부 IPv4는 apt/ECR/Slack outbound용이며 앱 포트는 열지 않는다. rin 태그에만 IAP `35.235.240.0/20` tcp/22 allow800, 전체 IPv4 ingress deny900을 적용해 기본 internal/SSH allow1000보다 먼저 평가한다. 기존의 더 높은 우선순위/hierarchical 정책은 실제 plan 전 점검한다.
- VM runtime service account가 없다. `charlie-rin-deploy` WIF pool·`rin-main` provider·배포 SA를 새로 만들고 infra/runner/GKE 계정·pool·권한은 재사용하지 않는다. 신뢰 조건은 rin repo ID1443173546, owner ID350278309, `refs/heads/main`이다.
- IAP 터널은 해당 VM tcp/22, OS Admin Login은 해당 VM에만 부여한다. 프로젝트 custom role은 gcloud 조회용 `compute.projects.get`·`compute.instances.list`만 부여한다. SSH sudo는 rin VM의 root와 동등하므로 rin main 변경 승인과 Actions 접근을 보호해야 한다.
- AWS는 기존 `rin-deploy`에 새 inline policy `rin-gcp-deploy-read` 하나만 관리한다. 기존 `push-image-and-deploy`, trust, EC2, `rin/deploy/aws` state는 관리하지 않는다. SSM `/rin` 경로와 `/rin/*` 자식 ARN 두 개의 GetParametersByPath와 명시한 KMS key의 decrypt(SSM ViaService·parameter ARN 조건)만 추가한다. KMS key ARN은 공개 메타데이터이며 parameter 값은 읽지 않았다.

## 로컬 검사

저장소 `.terraform-version`의 Terraform1.16.4, Google7.46.2, AWS6.67.0을 사용한다. 새 root lock은 기존 저장소 lock의 동일 버전·공식 checksum을 재사용한다.

```sh
terraform fmt -check -recursive terraform/environments/gcp/dev/rin
terraform -chdir=terraform/environments/gcp/dev/rin init -backend=false -input=false -lockfile=readonly
terraform -chdir=terraform/environments/gcp/dev/rin validate
terraform -chdir=terraform/environments/gcp/dev/rin test
python terraform/environments/gcp/dev/rin/tests/test_bootstrap.py
bash -n terraform/environments/gcp/dev/rin/startup.sh
python scripts/tests/test-gcp-workflow.py
python -X utf8 scripts/check-inventory.py
```

GCP workflow는 이 root를 credential-free validate/mock matrix에 추가한다. 기존 수동 deploy job은 계속 workload root만 대상으로 하며 rin을 apply하지 않는다. **infra main merge는 별개 AWS workflow의 `all-runtime` 자동 apply를 실행한다.** 이 사실도 Git 발행 승인 범위에 포함한다.

## 검토할 실제 plan

새 root의 리소스 전체가 승인 대상이다. GCP VM·방화벽·pool/provider·배포 SA·IAM·discovery role과 AWS 새 inline policy만 생성되어야 한다. 기존 AWS root의 전체 apply, `-target`으로 일부만 먼저 배포, 기존 역할 import는 하지 않는다.

IAP API의 활성화 자원은 기존 tailscale root 소유이므로 이 root에서 중복 관리하지 않는다. IAP/Compute/OS Login/IAM/WIF 관련 API가 이미 켜져 있어야 한다. 실제 계획 전 기존 GCS bucket 이름, GCP provisioning 자격(Compute/IAM/WIF/Service Usage/IAP IAM/state 접근), AWS profile `softbank-charlie`와 기존 role의 inline policy 읽기/추가 권한을 확인한다. **배포 SA는 provisioning 권한이 없으므로 이 root를 apply할 수 없다.** backend 예제는 read-only 확인한 기존 bucket `charlie-tfstate-project-b47cdba3-db5b-42a2-9a6`을 가리킨다. 이를 로컬의 Git 제외 `backend.hcl`로 복사한다. AWS provider는 `AWS_PROFILE=softbank-charlie` 등 승인된 로컬 세션을 사용한다. 승인된 plan에서만 backend를 초기화한다.

```sh
terraform -chdir=terraform/environments/gcp/dev/rin init -input=false -lockfile=readonly -backend-config=backend.hcl
terraform -chdir=terraform/environments/gcp/dev/rin plan -input=false -out=/secure-local-path/rin.tfplan
```

이 명령은 실행 예시다. saved plan/state는 Git·ADR·로그에 넣지 않고 공개 resource 주소·추가/변경/삭제 개수만 요약한다. 역할의 기존 inline policy 소유권이 바뀌거나 같은 새 정책명이 존재하면 적용을 멈추고 소유권부터 확인한다. VM은 deletion_protection와 Terraform prevent_destroy가 있고 boot disk auto_delete=false이다. 이후 변경 계획에서 교체/삭제가 보이면 데이터 보존 절차와 별도 승인이 필요하다.

## rin CI 공개 변수

| Terraform output | rin repository variable |
|---|---|
| `project_id` | `RIN_GCP_PROJECT_ID` |
| `zone` | `RIN_GCP_ZONE` |
| `instance_name` | `RIN_GCP_INSTANCE` |
| `workload_identity_provider` | `RIN_GCP_WORKLOAD_IDENTITY_PROVIDER` |
| `deploy_service_account` | `RIN_GCP_DEPLOY_SERVICE_ACCOUNT` |

`RIN_DEPLOY_TARGET` 미설정은 AWS 배포를 보존한다. 데이터 복원·검증·원본 단일 활성 통제 완료 후 승인된 전환에서만 `gcp`로 바꾼다. `/srv/rin/.migration-ready`는 복원·ownership·DB/파일 검증을 끝낸 운영자가 root로 작성한다. bootstrap과 Terraform은 이 파일을 만들지 않는다.

## 부팅·복구 계약

1. 첫 부팅은 Docker/Compose와 metadata 방어만 설치한다. Docker 패키지 설치 중 서비스 자동 시작을 mask하고, 영속 systemd guard가 성공해야 Docker를 시작한다. DOCKER-USER에서 `169.254.169.254:80,443`만 차단하며 UDP/TCP53 DNS는 보존한다. IPv4-only NIC와 Docker bridge가 대상이다. host network/privileged sandbox를 허용하는 설정 변경은 별도 보안 검토가 필요하다.
2. DB·작업·세션·첨부 복원 전 bot을 생성하지 않는다. host에는 배포용 AWS 장기키·GCP runtime identity를 저장하지 않는다. 앱용 env의 기존 `RIN_AI_AWS_*` 자격은 별도 수명·권한을 가진 입력이며 그 값을 조회하지 않았다. CI는 root-only `/run`0600 일회성 env와 ECR pull-only token을 전달하고 성공/실패 모두 정리한다(rin 저장소의 배포 계약).
3. 일반 재부팅에서는 systemd metadata guard가 Docker보다 먼저 실행되고 기존 컨테이너 restart 정책이 작동한다. bootstrap은 Compose/env/컨테이너를 재생성하지 않는다.
4. env 없는 컨테이너 재생성·DB 복구는 승인된 CI 배포가 env를 다시 전달하는 별도 절차다. 빈 env로 Compose를 실행하거나 원본과 새 bot을 동시에 실행하지 않는다.
5. live 합격은 복원 데이터·실제 bot 요청·sandbox metadata 차단/DNS·재부팅·GCP 후속 배포 검증 후 판정한다. 원본 EC2 `i-05d4ba044ec963ed0`는 그 후 별도 승인으로 중지한다. 새 호스트가 쓰기를 받은 뒤에는 역이전 없이 옛 DB로 복귀하지 않는다.

## 근거

- [ADR0059](adr/0059-isolate-rin-gcp-host-and-ci-access.md)
- [rin 이전 이슈](https://github.com/2026-Softbank-Hackathon-Charlie/infra/issues/86)
- [IAP TCP forwarding 권한](https://docs.cloud.google.com/iap/docs/using-tcp-forwarding#permissions_details)
- [OS Login 역할 범위](https://docs.cloud.google.com/compute/docs/oslogin/set-up-oslogin#assign_os_login_iam_roles)
