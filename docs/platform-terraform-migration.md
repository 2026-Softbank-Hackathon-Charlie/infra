# Platform Terraform 이관과 운영 인수

이 변경은 `platform@5cb1c2e`의 소스 관리 위치를 infra로 옮긴다. EC2를 관리 EKS로 옮기거나 state/backend를 합치는 변경은 아니다. [ADR0015](adr/0015-centralize-platform-terraform-ownership.md), [기존 운영 절차](../terraform/platform/aws/README.md)를 함께 본다.

## 새 소유 위치

| 기존 platform 경로 | infra 경로 |
|---|---|
| `deploy/aws/*.tf`, lockfile, `tf.sh` | `terraform/platform/aws/` |
| `deploy/aws/dns/` | `terraform/platform/aws/dns/` — 별도 root |
| `deploy/aws/state/` | `terraform/platform/aws/state/` — 별도 root |
| `deploy/aws/host/`, `user_data.sh.tftpl` | `terraform/platform/aws/host/`, `user_data.sh.tftpl` |
| `examples/envs.yaml`, `examples/apps.yaml`의 배포 기본 설정 | `terraform/platform/aws/host/envs.yaml`, `host/apps.yaml` |

platform의 examples는 로컬 개발용이다. 실제 배포 설정의 변경은 infra의 host 파일을 대상으로 검토한다. 이미 운영자가 다른 값을 적용했다면 기존 로컬 입력과 SSM 설정의 소유권을 먼저 확인한다. 이 PR은 비밀값이나 운영자의 로컬 파일을 가져오지 않는다.

## 유지한 계약

| root | 기존 S3 bucket | 기존 key |
|---|---|---|
| 관리 호스트 | `hopcharlie-tfstate-525091405886-apne2` | `platform/management-host/terraform.tfstate` |
| state 버킷 | 같은 버킷 | `state/terraform.tfstate` |
| DNS·ACM | 같은 버킷 | `platform/dns/terraform.tfstate` |

기존 기본 workspace·리소스 주소·입력·provider lock·태그를 유지한다. 이름이나 폴더 표기를 정리하려고 ManagedBy 태그까지 바꾸지 않았다. state 파일 복사, `state mv`, import, backend migration 또는 실제 apply는 이 PR에 포함하지 않는다. root와 backend가 그대로라면 Git 저장소 위치 변경만으로 state 이관이 필수인 것은 아니다.

## 소스 PR 반영 순서

1. infra import PR의 보존 비교·격리 검증·기존 CI 결과를 검토하고 먼저 반영한다.
2. platform handoff PR을 반영한다. 앱 helper는 남기고 `deploy/aws/tf.sh`는 output만 새 위치로 위임한다.
3. 개발자와 배포 작업의 infra checkout을 준비하고 `INFRA_DIR=/absolute/path/to/infra`를 지정한다. 기본값은 platform 옆의 `infra` 저장소다.
4. 운영 담당자와 기존 tfvars·backend·계정·권한·작업 중 잠금을 인수한다. 별도로 승인된 실제 조회에서 이관 전후 plan을 비교하고 이관으로 인한 생성·교체·삭제가 없을 때만 새 경로를 운영 입구로 사용한다.

기존 `.terraform` 디렉터리나 state·plan·비밀값을 새 저장소에 복사하지 않는다. 기존 backend를 유지한 초기화 절차는 운영 상태를 확인한 뒤 정한다. 현재 수동 AWS workflow의 `all-runtime`에는 이 root가 들어가지 않는다. 기존 runner가 자동 생성하는 `charlie` backend/입력을 이 root에 적용하지 않는다.

## 작업 중 반영된 팀원 변경

[infra #4](https://github.com/2026-Softbank-Hackathon-Charlie/infra/pull/4)와 [platform #26](https://github.com/2026-Softbank-Hackathon-Charlie/platform/pull/26)이 이관 작업 중 main에 반영됐다. 이 PR은 infra95a746d/platform5cb1c2e로 다시 맞춰 관리 EKS 차트와 최신 앱 helper를 보존했다. 새 Pod Identity IAM 정의·DNS/ACM root·Cloudflare lock도 이관에 포함한다. 이 이관이 관리 EKS 차트를 배포하거나 EC2를 정리하는 것은 아니다.

이후 추가된 Terraform이 있다면 이관 브랜치를 최신 main에 다시 맞춘다. 소스 삭제 충돌을 해결하면서 새 리소스나 root를 누락하지 않는다.

## 코드 검증과 실행 검증

새 검증 workflow는 AWS 인증 없이 허용된 소스·payload만 임시 복사해 세 root의 fmt, `init -backend=false -input=false -lockfile=readonly`, validate를 실행한다. 사용자 tfvars·override·state·plan·자격 증명을 사용하지 않는다. [검증 script](../scripts/check-platform-terraform.py)와 [회귀시험](../scripts/test-platform-terraform.py)에 계약을 고정했다.

코드 검사 성공은 실제 no-op plan이나 배포 성공을 뜻하지 않는다. 관리 호스트·계정 권한·원격 state·실제 적용된 입력은 이번 소스 이관에서 조회하지 않았다.

frontend의 수동 배포 PR #5는 ECR/EC2/API URL을 변수로 받아 Terraform 경로를 읽지 않는다. 대상 식별자가 유지되면 이관 때문에 해당 workflow를 바꿀 필요는 없다. 이후 EKS 전환은 별도 배포 경로 변경이다.

원본 lock을 유지한다. Windows 네이티브 Terraform의 readonly provider checksum 검증이 실패하는 환경에서는 lock을 다시 쓰지 말고 Linux CI의 init/validate 결과를 확인한다. 이번 이관의 실제 Linux 검증 결과와 원격 no-op plan은 구분한다.
