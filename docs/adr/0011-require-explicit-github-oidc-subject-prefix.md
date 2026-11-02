# 0011. account 입력에 실제 GitHub OIDC subject prefix를 명시

- 날짜: 2026-11-02
- 상태: 채택
- 관련 ADR: [0003. 공통 입력](0003-share-project-inputs-between-local-and-ci.md), [0007. OIDC provider 등록](0007-register-account-github-oidc-provider.md), [0008. stack별 수동 실행](0008-select-terraform-stack-and-action.md)

## 배경과 제약

account 입력 생성기는 `github_oidc_subject_prefix`가 없으면 저장소 이름으로 `repo:OWNER/REPO`를 조용히 생성했다.
GitHub의 실제 prefix에는 불변 조직·저장소 ID가 포함될 수 있어 이 추측으로 만든 IAM trust가 실제 OIDC subject와 어긋날 수 있다.
`use_default=true`만으로 이름 기반 prefix라고 판단할 수 없으므로 저장소 API가 반환한 실제 값을 입력해야 한다.
Terraform은 이미 기존 형식과 `@ID` 포함 형식을 지원하며 main suffix를 추가한다.
이번 결정은 입력 계약 수정에 한정한다. 실제 IAM 변경이나 Terraform apply 승인을 의미하지 않는다.

## 검토한 대안

- 저장소 이름 기반 fallback 유지: 설정은 간단하지만 실제 subject와 다른 trust를 조용히 생성하므로 제외한다.
- 운영 저장소의 숫자 ID를 공통 기본값으로 고정: 한 저장소에서만 맞고 GitHub 설정과 입력의 불일치를 감추므로 제외한다.
- account 입력에 실제 prefix를 필수로 명시: 조회 단계가 필요하지만 기존·불변 ID 형식을 그대로 보존하고 누락을 실행 전에 발견하므로 선택한다.

## 결정과 이유

[입력 생성기](../../scripts/project-config.py)는 `account/aws`에서만 `github_oidc_subject_prefix`를 필수로 요구한다.
기존 형식 검증과 공통 설정의 저장소 이름 일치 검증을 유지한다. bootstrap/foundation 등 다른 stack에는 필수화하지 않는다.
관리자는 `gh api repos/OWNER/REPO/actions/oidc/customization/sub --jq .sub_claim_prefix`의 반환값을
Git에서 제외한 `config/account.local.json`에 넣는다. `use_default=true`여도 실제 반환값을 사용한다.
입력은 branch suffix를 제외한 prefix이며 Terraform이 `:ref:refs/heads/main`을 추가한다.
예제에는 교체할 `repo:OWNER/REPO`만 제공하고 운영 숫자 ID나 자격 증명은 넣지 않는다.

## 영향과 후속 작업

- 기대 효과: 누락된 prefix로 잘못된 trust 입력이 생성되는 것을 막고 명시한 기존·불변 ID 형식을 보존한다.
- 단점과 위험: 기존 로컬 account 설정에 값이 없으면 입력 생성이 실패하므로 실제 API 값을 추가해야 한다. GitHub 설정 변경 시 운영자가 로컬 입력도 동기화해야 한다.
- 필요한 후속 작업 및 검증: 누락 거부, 두 형식 보존, 다른 저장소 거부, 다른 stack의 생략 허용을 AWS 없는 Python 회귀 검사로 검증한다. 코드 merge는 실제 IAM을 변경하지 않는다. 콘솔 trust 변경을 코드와 동기화할 때도 기존 역할을 관리하는 account state/backend를 유지하고 별도 승인된 plan/apply를 수행해야 한다. live IAM 정합성과 실제 OIDC 인증 성공은 이번 로컬 검증 범위 밖이다.

## 관련 자료

- [계정 입력 예제](../../config/account.local.json.example)
- [회귀 검사](../../scripts/tests/test-project-config.py)
- [Terraform subject 입력 검증](../../terraform/account/aws/variables.tf)
- [main 브랜치 trust 구성](../../terraform/account/aws/main.tf)
- [운영 입력 및 state 안내](../../README.md)
