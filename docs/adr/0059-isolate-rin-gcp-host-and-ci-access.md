# 0059. rin 전용 GCP 호스트와 CI 접근을 독립 state로 관리한다

- 날짜: 2026-11-06
- 상태: 채택 (로컬 구현 방향; cloud/Git 발행 승인 전)
- 관련 ADR: [ADR0013](0013-add-gcp-workload.md), [ADR0041](0041-keyless-gcp-access-for-platform-app-storage.md)

## 배경과 제약

사용자는 AWS 운영비 절감을 목적으로 현재 rin을 2 vCPU/8 GiB/40 GiB 동급 GCP VM으로 옮기는 로컬 구현을 승인했다. 기존 runner/GKE/router와 분리하고 ECR·SSM·현재 Compose·DB/작업/세션을 유지한다. 원본 EC2 중지, 실제 IAM/VM 생성, Git 발행은 승인되지 않았다.

2026-11-06 공개 메타데이터 read-only 확인: GCP project number414068132732, rin GitHub repo ID1443173546/owner ID350278309, AWS account525091405886의 `rin-deploy`는 `push-image-and-deploy` inline policy로 ECR push·EC2 SSM 명령만 허용한다. `/rin/*`는 `alias/aws/ssm`으로 암호화된다. parameter 원문과 state 본문은 읽지 않았다. 기존 `RIN_AI_AWS_*` 앱용 자격의 실제 수명·권한은 미확인이며 배포용 자격과 구분한다. rin AWS 기존 state의 소유권은 확정하지 않았으므로 그 root 전체 apply를 사용할 수 없다.

## 검토한 대안

- 기존 GCP runner나 infra/GKE deploy SA 재사용: bot 상태와 운영 경계를 섞고 과도한 자격을 공유하므로 제외한다.
- 기존 GitHub WIF pool에 rin provider만 추가: 리소스는 줄지만 같은 pool의 다른 provider와 principalSet 신뢰 경계를 공유한다. 별도 pool/provider를 선택해 rin main 숫자 ID 신뢰를 독립시킨다.
- GAR/Secret Manager와 AWS federation 전체 재구성: 이미지·비밀 저장소와 identity 운영 범위를 크게 바꾸므로 이번 서버 이전에서 제외한다.
- rin AWS root 수정/전체 apply: 역할/state 소유권이 미확인이고 EC2 변경 위험이 있어 제외한다. 기존 역할의 새 inline policy 이름 하나만 독립 root에서 소유한다.

## 결정과 이유

`terraform/environments/gcp/dev/rin`과 기존 GCS bucket의 `gcp/dev/rin` prefix를 사용한다. 공유 IAP API 활성화는 기존 tailscale root 소유로 남기고 중복 관리하지 않는다. 서울 e2-standard-2/40 GiB Ubuntu24 x86 VM은 runtime SA 없이 만들고 IAP SSH만 허용한다. 새 전용 pool/provider는 rin repository와 owner의 불변 숫자 ID 및 main ref만 신뢰한다. 별도 배포 SA는 해당 VM OS Admin Login과 IAP tcp/22, 프로젝트 조회 두 권한만 가진다.

AWS `rin-deploy`의 기존 trust/push policy/EC2는 소유하지 않는다. 새 이름 `rin-gcp-deploy-read`만 소유해 `/rin` 경로와 `/rin/*` 자식 ARN의 GetParametersByPath와 해당 KMS key decrypt(SSM 경유·parameter ARN 조건)를 추가한다. provider 버전은 기존 AWS workload lock6.67.0/Google lock7.46.2를 재사용한다.

CI가 SSM env·pull-only ECR 자료를 `/run` 임시 파일로 한 번 전달한다. 호스트는 메타데이터 HTTP/HTTPS를 Docker bridge에서 차단하고 DNS53를 남긴다. boot는 guard와 Docker만 관리하며 env/Compose/봇을 재생성하지 않는다. 복원 후 root가 migration-ready를 만들어야 rin CI가 최초 앱을 생성할 수 있다.

## 영향과 후속 작업

- 기대 효과: 기존 런타임·배포 identity와 상태 소유권을 분리하고 배포용 AWS 장기키/GCP metadata 자격 없이 현재 앱을 유지한다.
- 단점과 위험: GCP VM/EBS/외부 IP·전송·보존 비용을 함께 계산해야 하며 절감을 보장하지 않는다. OS Admin Login은 rin root 권한이다. 외부 IPv4는 outbound용이고 더 높은 우선순위의 조직/네트워크 방화벽은 live 전 확인한다.
- 후속 작업: 로컬 mock·shell·workflow 검증, 두 저장소 계약 독립 검토, 실제 resource plan 승인, 일관된 PG/data 이전과 단일 bot 활성·metadata/DNS·reboot·후속 배포 확인. 원본 EC2 중지는 그 후 별도 승인이다.
- Git 주의: infra main merge가 기존 AWS all-runtime 자동 apply를 시작한다. 이 root가 새 GCP validation matrix에 추가되어도 기존 workload 수동 deploy 대상은 바꾸지 않는다.

## 관련 자료

- [구성과 검증·운영 계약](../rin-gcp-host.md)
- [Terraform host/auth](../../terraform/environments/gcp/dev/rin/main.tf)
- [rin 이전 이슈](https://github.com/2026-Softbank-Hackathon-Charlie/infra/issues/86)
- [OS Login 역할과 범위](https://docs.cloud.google.com/compute/docs/oslogin/set-up-oslogin#assign_os_login_iam_roles)
- [IAP의 gcloud 필수 조회 권한](https://docs.cloud.google.com/iap/docs/using-tcp-forwarding#permissions_details)
