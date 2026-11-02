# 0012. GitHub의 실제 immutable OIDC subject와 AWS 신뢰 조건 일치

- 날짜: 2026-11-02
- 상태: 채택
- 관련 ADR: [0011](0011-require-explicit-github-oidc-subject-prefix.md), [0007](0007-register-account-github-oidc-provider.md), [0009](0009-grant-ci-terraform-administrator-access.md), [0010](0010-grant-eks-operator-access.md)

## 배경과 제약

CI 역할의 AdministratorAccess 적용 후 최초 runtime 실행은 AWS 인증에서 `sts:AssumeRoleWithWebIdentity` 거부로 실패했고 Terraform 단계에 진입하지 않았다.
GitHub 저장소 API 조회 결과 immutable subject를 사용하고 실제 prefix에 owner/repository ID가 포함되는 것을 확인했다.
기존 이름 전용 subject와 일치하지 않았다. 초기 준비에서 실제 subject 확인을 누락했다.
사용자는 검토한 신뢰 조건 교정과 변경 공개, 신규 배포 실행 한 번을 승인했다.

## 검토한 대안

- 관리자 정책 추가 확대: 인증 신뢰 subject 불일치를 해결하지 못한다.
- wildcard 또는 legacy subject로 GitHub 설정 완화: 다른 identity를 허용하거나 GitHub 보안 설정을 바꿀 수 있어 제외한다.
- API가 반환한 정확한 immutable subject 사용: 같은 저장소의 main만 실제 ID까지 지정해 선택한다.

## 결정과 이유

`repos/2026-Softbank-Hackathon-Charlie/infra/actions/oidc/customization/sub`의 `sub_claim_prefix`를 기준으로 한다.
로컬 계정 JSON의 기존 지원 입력 `github_oidc_subject_prefix`에
`repo:2026-Softbank-Hackathon-Charlie@350278309/infra@1442353250`을 저장한다.
Terraform account stack은 뒤에 `:ref:refs/heads/main`을 붙여 role trust의 `StringEquals` subject 하나만 갱신한다.
provider ARN·audience·main 제한·세션 시간·AdministratorAccess와 다른 권한 정책은 유지한다.
예제는 ADR 0011의 교체할 placeholder를 유지하고, README와 본 문서에는 이번 API로 확인한 비밀 아닌 값을 설명한다. 저장소 변경 시에는 반드시 API를 재조회한다.
helper는 문법과 이름 일치만 확인하며 ID의 진위를 인증하지 않는다. 토큰을 진단 목적으로 발급하거나 로그에 출력하지 않는다.

## 영향과 후속 작업

- 기대 효과: 실제 GitHub token subject와 역할 신뢰를 일치시키며 다른 저장소·브랜치를 추가하지 않는다.
- 단점과 위험: 저장소 rename/transfer 등으로 실제 prefix가 달라지면 신뢰를 다시 검토해야 한다. 예제 placeholder나 다른 저장소의 조회 결과를 그대로 재사용하지 않는다.
- 필요한 후속 작업 및 검증: 적용 직전 API prefix를 재조회하고 저장 plan에서 역할 한 개 update, 생성·삭제 없음과 subject만 변경됨을 검증한다. 적용 후 AWS 신뢰 재조회와 변경 없음 plan을 확인한다. Python 전달 검사와 Terraform의 실제 sub/audience/provider/main 회귀 검사를 수행한다. runtime state 미존재와 새 source 검증 성공을 재확인 후 최초 구축 배포를 한 번 새로 실행하며 실패 시 자동 재시도·destroy하지 않는다. state·plan·비밀값은 Git에 넣지 않는다.

## 관련 자료

- [계정 예제](../../config/account.local.json.example)
- [계정 trust 회귀 검사](../../terraform/account/aws/tests/github-ci.tftest.hcl)
- [입력 전달 검사](../../scripts/tests/test-project-config.py)
- [GitHub OIDC reference](https://docs.github.com/en/actions/reference/security/oidc)
