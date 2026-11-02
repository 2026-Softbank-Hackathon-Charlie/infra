# 0008. 수동 Terraform 실행을 stack별로 선택하고 기본 동작을 bootstrap plan으로 제한

- 날짜: 2026-11-02
- 상태: 채택
- 관련 ADR: [0003. 공통 입력](0003-share-project-inputs-between-local-and-ci.md), [0004. 검증 병렬화](0004-parallelize-infrastructure-checks.md), [0007. OIDC 등록](0007-register-account-github-oidc-provider.md)

## 배경과 제약

기존 수동 apply는 bootstrap부터 두 EKS까지 모든 runtime stack을 순차 적용했다.
사용자는 EKS가 한꺼번에 생성되지 않도록 최소 단계부터 실제 실행하기를 요청했다.
새 계획을 독립 검토하고 로컬 bootstrap 실행과 단계별 실행 방식을 승인받았다.
S3와 CI 역할이 없는 최초 단계는 로컬에서 처리해야 하며 GitHub 파이프라인 자체로 최초 S3를 만들 수 없다.
기존 리소스 코드·주소·state key와 검증 범위는 유지해야 한다.

## 검토한 대안

- 리소스 코드를 일시 삭제: 이미 관리 중인 자원을 삭제 대상으로 만들 수 있어 제외한다.
- 일상적인 `-target` 사용: 의존 자원의 적용 범위를 직관적으로 알기 어렵고 부분 적용에 의존하므로 제외한다.
- 기존 root stack 단위 선택: 이미 분리된 state와 소유권을 유지하며 실행 범위를 명시할 수 있어 선택한다.

## 결정과 이유

[수동 workflow](../../.github/workflows/terraform-check.yml)의 `apply` boolean을 `action=plan|apply`,
`stack=bootstrap|foundation|management|workload|all-runtime` 선택 입력으로 교체한다.
기본값은 `plan + bootstrap`이다. push/PR은 계속 AWS 인증 없는 검사만 수행한다.
main의 명시적 수동 실행과 전체 검사 성공 후에만 선택 작업을 수행하며 plan도 OIDC 인증을 사용한다.
[CI 스크립트](../../scripts/tf-ci.sh)도 같은 선택을 검증하고 생략 시 bootstrap plan만 수행한다.
선택한 stack만 초기화·계획·적용하며 account는 CI 선택에서 제외하고 관리자 로컬 영역으로 유지한다.
`all-runtime`을 명시한 경우에만 bootstrap → foundation → management → workload 순서를 사용한다.
개별 stack의 의존 인프라는 자동 생성하지 않는다. foundation state가 없으면 EKS plan은 실패할 수 있다.
bootstrap/foundation은 운영자 미설정을 허용하지만 제공된 잘못된 입력은 공통 스키마에서 거부한다.

## 영향과 후속 작업

- 기대 효과: 코드를 삭제하지 않고 EKS 없이 S3 state 연결부터 확인할 수 있다. plan은 리소스를 적용하지 않는다.
- 단점과 위험: plan에도 기존 S3 backend와 CI 역할이 필요하며 state 잠금 파일을 일시 생성할 수 있다. foundation apply에는 VPC·NAT·bridge·IAM·Loki S3가 포함된다. `all-runtime + apply`는 두 EKS를 포함하므로 명시적으로 선택해야 한다.
- 필요한 후속 작업 및 검증: fake CLI로 기본 no-apply, 단일 stack 격리, EKS 운영자 요구, 잘못된 입력 차단, 전체 순서, foundation 삭제 guard, 임시 plan 정리와 workflow gate를 검증한다. saved plan·state는 artifact로 올리지 않는다. apply 동시 실행은 직렬이며 취소하지 않는다. 실제 GitHub 실행과 IAM 역할 생성은 이번 변경에 포함하지 않는다.
- 실제 최소 실행 결과: 기존 bootstrap 절차로 state S3와 보호 설정만 생성하고 bootstrap state를 원격으로 이전했다. 정식 root의 `plan -detailed-exitcode`는 변경 없음(0)을 확인했다. EKS·VPC·NAT·IAM 역할은 생성하지 않았다. 로컬 백업은 Git에서 제외한 경로에 보존한다. state·plan 내용은 본 문서에 기록하지 않는다.

## 관련 자료

- [로컬 실행 진입점](../../scripts/tf-stack.sh)
- [실행 범위 회귀 검사](../../scripts/tests/test-tf-ci.py)
- [workflow gate 검사](../../scripts/tests/test-workflow.py)
- [최초 구축 안내](../../README.md)
