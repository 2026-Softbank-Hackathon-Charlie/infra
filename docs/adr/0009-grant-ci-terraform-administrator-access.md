# 0009. GitHub Terraform CI 역할에 계정 전체 관리자 권한 부여

- 날짜: 2026-11-02
- 상태: 채택
- 관련 ADR: [0003](0003-share-project-inputs-between-local-and-ci.md), [0007](0007-register-account-github-oidc-provider.md), [0008](0008-select-terraform-stack-and-action.md)

## 배경과 제약

사용자는 새 아키텍처 추가 시 IAM 권한 누락으로 파이프라인이 반복 중단되지 않도록 Terraform GitHub OIDC 역할에 관리자 권한을 요청했다.
독립 계획 리뷰 후 관리자 정책 연결, 운영자 지정, 관련 변경 공개와 CI 최초 배포 범위를 명시적으로 승인했다.
기존 역할 이름·OIDC provider·state 경로와 GitHub main 브랜치 신뢰는 유지한다.

## 검토한 대안

- 작업별 제한 정책 유지·확장: 권한을 줄일 수 있지만 새 서비스마다 정책 보완과 관리자 적용이 필요하다.
- 정책 시뮬레이션만 추가: 오류 탐지는 돕지만 누락된 권한을 실제 부여하지는 못한다.
- AWS 관리형 AdministratorAccess 연결: 계정 전체 권한을 허용하는 대신 서비스 추가 때의 권한 관리 비용을 줄이며 사용자가 지정한 선택이다.

## 결정과 이유

[ci-administrator.tf](../../terraform/account/aws/ci-administrator.tf)에서 기존 `charlie-dev-github-terraform` 역할에
`arn:aws:iam::aws:policy/AdministratorAccess`를 연결하고 account 전용 S3 state로 관리한다.
OIDC audience는 `sts.amazonaws.com`, subject는 기존 저장소 main만 허용하고 최대 세션은 2시간으로 유지한다.
기존 제한 Allow 정책은 삭제하지 않지만 관리자 권한의 상한이 아니다. 정책 문서 검사도 이 차이를 명확히 표현한다.
CLI 임시 부여 후 잊히는 구성을 피하고 Terraform plan과 코드에서 권한 부여를 확인할 수 있게 한다.

## 영향과 후속 작업

- 기대 효과: 새 AWS 서비스·리소스 도입 시 개별 Allow 누락으로 인한 CI 중단을 줄인다.
- 단점과 위험: 이 역할은 자체 IAM 역할·신뢰·정책, account state, OIDC 및 다른 프로젝트를 포함한 계정 전체를 관리할 수 있다. main에서 실행되는 코드와 저장소 쓰기 권한이 계정 관리 권한에 영향을 준다. 기존 태그·이름 제한을 보안 상한으로 간주하지 않는다. 조직 SCP·서비스 한도·용량 제한은 별개다.
- 필요한 후속 작업 및 검증: account plan이 관리자 정책 연결 한 개 생성만 포함하고 OIDC trust 변경이 없는지 확인 후 적용한다. AWS 재조회와 변경 없음 plan을 확인한다. 향후 관리자 권한을 제거하려면 이 attachment만 제거하는 plan과 남은 제한 정책의 필요 권한을 별도로 검토한다. state·plan·비밀값은 Git/ADR에 기록하지 않는다.

## 관련 자료

- [GitHub OIDC와 역할](../../terraform/account/aws/main.tf)
- [권한 회귀 검사](../../terraform/account/aws/tests/github-ci.tftest.hcl)
- [AWS AdministratorAccess](https://docs.aws.amazon.com/aws-managed-policy/latest/reference/AdministratorAccess.html)
