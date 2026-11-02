# 0015. 플랫폼 Terraform의 관리 위치를 infra로 모으고 기존 state 경계를 유지한다

- 날짜: 2026-11-02
- 상태: 채택
- 관련 ADR: [0003](0003-share-project-inputs-between-local-and-ci.md), [0004](0004-parallelize-infrastructure-checks.md)

## 배경과 제약

사용자는 platform에 있는 Terraform을 infra로 옮기고 PR까지 작성하도록 요청했다. 이 결정은 소스 관리 위치에 관한 것이며 PR merge, 실제 state 접근·이전, plan/apply, IAM·클러스터 변경의 승인은 아니다.

`platform@5cb1c2e`의 `deploy/aws`는 기존 관리 EC2·ECR·IAM·SSM과 별도 S3 state 버킷 root를 갖는다. `infra@95a746d`의 AWS roots는 `charlie` 이름·입력·backend 계약을 사용한다. 기존 관리 EKS와 이 EC2는 다른 리소스다. 원격 state와 현재 리소스의 drift는 이번 작업에서 조회하지 않았다.

작업 중 [infra #4](https://github.com/2026-Softbank-Hackathon-Charlie/infra/pull/4)와 [platform #26](https://github.com/2026-Softbank-Hackathon-Charlie/platform/pull/26)이 main에 반영됐다. 두 이관 브랜치를 그 최신 main에 맞추고 새 Pod Identity·DNS Terraform까지 포함했다. 관리 EKS Helm 차트는 그대로 보존하며 이 작업에서 배포하지 않는다. 기존 ADR0014 다음인 번호0015를 사용한다.

## 검토한 대안

- 기존 `management` root와 state에 병합: 관리 위치는 줄지만 모듈·리소스 주소, 입력, state와 운영 수명까지 동시에 바뀐다. 소스 이관 범위를 넘어 제외한다.
- platform을 sibling checkout으로 참조: 소스 중복은 줄지만 Terraform 검증·컨테이너 mount·CI가 다른 private 저장소에 의존한다. 자체 완결된 root를 위해 제외한다.
- 세 root를 별도 경계로 옮기고 기존 identity를 보존: 경로와 소유권만 정리할 수 있어 선택한다. 운영 설정은 infra 소유로 복사하고 platform의 기존 examples는 로컬 개발 예제로 남긴다.

## 결정과 이유

`terraform/platform/aws`에 관리 호스트 root, 그 아래 `state/`에 별도의 state 버킷 root, `dns/`에 DNS·ACM root를 둔다. 기존 리소스를 child module로 감싸지 않는다. backend의 bucket/key/region/암호화/잠금, 리소스·data·output 이름, for_each 키, provider constraints와 세 lockfile, 기본값과 ManagedBy 태그를 유지한다. 기존 state 버킷은 새로 만들거나 infra의 `charlie` 버킷으로 옮기지 않는다.

호스트 payload와 bootstrap 템플릿을 함께 옮긴다. 기존 envs/apps 설정은 `host/`에 두고 파일 참조만 바꾸며 SSM으로 전달되는 내용은 보존한다. 실제 tfvars·`.terraform`·state·plan·자격 증명은 복사하지 않는다.

기존 generic AWS runner의 roots, `all-runtime`, 계정 정책과 `config/project.json`은 바꾸지 않는다. 새 세 root는 자격 증명과 사용자 Terraform 설정을 배제한 임시 복사본에서만 별도로 fmt/init-backend-false/validate한다. 자동 apply는 추가하지 않는다. 기존 Docker 기반 Terraform1.17.1 wrapper는 새 root를 mount한다.

platform에는 앱 이미지·재배포·kubeconfig helper를 유지한다. output 전용 compatibility shim은 `INFRA_DIR`가 가리키는 infra wrapper로 위임한다. 이전 위치의 init/plan/apply/destroy는 실패하며 새 소유 위치를 안내한다. 비밀값이 포함될 수 있는 전체 output을 자동으로 출력하지 않는다.

## 영향과 후속 작업

- 기대 효과: Terraform과 인프라 검증의 소유권을 infra에서 확인할 수 있다. 기존 앱 helper는 명시적 저장소 연결로 필요한 output만 받는다.
- 단점과 위험: 두 저장소 PR의 merge 순서를 맞춰야 한다. 기존 운영자의 로컬 입력·자격 증명·backend 초기화 상태는 별도로 인수해야 한다. 소스 identity 보존은 원격 no-op plan을 증명하지 않는다.
- 필요한 후속 작업 및 검증: infra import PR을 먼저 반영한 뒤 platform handoff PR을 반영한다. 실제 계획을 실행하기 전 담당자와 입력·state를 확인하고 구체적인 조회·변경 승인을 받는다. 기존 위치의 임의 apply를 중단하고 이관 전후 plan을 비교해 이관으로 인한 생성·교체·삭제가 없는지 확인한다.
- 동시 작업: 이미 병합된 Pod Identity·DNS 정의와 Cloudflare provider lock, `platform/dns/terraform.tfstate` backend를 함께 보존한다. 최신 main의 앱 helper `API_URL` override와 infra 관리 EKS 차트·검사도 유지한다. 이관 중 추가 main 변경이 생기면 다시 대조하며 삭제 충돌로 팀원 변경을 버리지 않는다.

## 관련 자료

- [이관·운영 인수 절차](../platform-terraform-migration.md)
- [이관한 운영 문서](../../terraform/platform/aws/README.md)
- [격리 검증](../../scripts/check-platform-terraform.py)
- [Terraform 모듈·주소 변경](https://developer.hashicorp.com/terraform/language/modules/develop/refactoring)
