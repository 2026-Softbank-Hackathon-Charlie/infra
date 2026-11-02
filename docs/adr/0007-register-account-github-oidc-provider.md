# 0007. GitHub OIDC provider를 계정 공용 자원으로 등록하고 Terraform에서 참조

- 날짜: 2026-11-02
- 상태: 채택
- 관련 ADR: [0003. 공통 프로젝트 입력](0003-share-project-inputs-between-local-and-ci.md)

## 배경과 제약

사용자는 AWS의 GitHub OIDC provider 존재 여부를 조회하고, 없으면 생성하여 로컬 config에 등록하도록 요청했다.
계정과 기존 provider를 조회한 결과 대상 계정에 OIDC provider가 없었다.
독립 에이전트가 계획을 검토했고 사용자가 해당 IAM 생성과 로컬 등록 범위를 명시적으로 승인했다.
이번 작업은 provider 하나의 생성과 로컬 등록에 한정하며 Terraform account stack 전체 적용이나 배포 역할 생성은 포함하지 않는다.

## 검토한 대안

- bootstrap과 account stack 전체 적용: state와 CI 역할까지 함께 구축하지만 S3·IAM 정책 생성 및 state 이전으로 승인 범위가 확대된다.
- provider 전용 Terraform state를 추가: 선언적으로 관리할 수 있지만 별도 root와 state 소유권·운영 절차를 추가해야 한다.
- AWS CLI로 계정 공용 provider만 생성하고 기존 ARN 입력으로 참조: 최소 승인 범위 안에서 GitHub 신뢰 제공자를 등록할 수 있어 선택한다.

## 결정과 이유

관리자 프로필로 대상 계정을 재확인하고 예상 provider ARN을 조회했다. `NoSuchEntity`를 확인한 뒤
`https://token.actions.githubusercontent.com` URL과 `sts.amazonaws.com` audience로 IAM OIDC provider를 생성했다.
생성 후 GetOpenIDConnectProvider로 URL과 audience를 재검증했다. thumbprint는 고정하지 않고 AWS가 취득하도록 했다.
provider는 계정 관리자가 관리하는 공용 자원으로 유지한다. [account stack](../../terraform/account/aws/main.tf)은
Git에서 제외한 로컬 계정 JSON의 비어 있지 않은 `github_oidc_provider_arn`을 통해 이를 참조하며 새 state로 import하거나 중복 생성하지 않는다.
로컬 config에는 실제 계정 ID와 검증한 provider ARN만 등록했다. 예제 운영자 ARN은 제거했으며 EKS 운영자 선정은 후속 작업으로 남긴다.
이 문서에는 자격 증명·토큰·Terraform state·plan을 기록하지 않는다.

## 영향과 후속 작업

- 기대 효과: S3 bootstrap이나 CI 역할 전체 생성 없이 GitHub 신뢰 제공자 등록을 완료하고 Terraform 입력을 준비한다.
- 단점과 위험: provider 자체의 수명 주기는 이 저장소의 Terraform state가 관리하지 않는다. 계정의 다른 저장소·역할도 사용할 수 있으므로 임의 삭제·변경하지 않는다. 생성 후 로컬 등록이 실패해도 자동 삭제하지 않는다.
- 필요한 후속 작업 및 검증: AWS 재조회로 URL/audience를 검증했고 account/bootstrap 입력 생성 계약과 로컬 JSON 권한·Git 제외를 확인한다. GitHub 배포에는 별도 S3 state, main 저장소/브랜치를 제한하는 IAM 역할과 권한 정책, GitHub 변수 등록이 필요하다. provider 생성만으로 AWS 배포 권한을 부여하지 않는다. 해당 실제 변경과 EKS 운영자 선정은 별도 승인 후 진행한다.

## 관련 자료

- [공유 설정](../../config/project.json)
- [계정별 입력 예제](../../config/account.local.json.example)
- [입력 검증 도우미](../../scripts/project-config.py)
- [AWS CreateOpenIDConnectProvider API](https://docs.aws.amazon.com/IAM/latest/APIReference/API_CreateOpenIDConnectProvider.html)
