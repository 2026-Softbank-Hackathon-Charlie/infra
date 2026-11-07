# 0069. AWS 배포 입력 변경에만 main 자동 apply 실행

- 날짜: 2026-11-07
- 상태: 채택
- 관련 ADR: [0008](0008-select-terraform-stack-and-action.md)을 대체

## 배경과 제약

기존 AWS workflow는 파일 경로와 관계없이 main push에서 전체 검사 후 `apply + all-runtime`을 실행했다. 실제 plan에 변경이 없으면 apply를 생략하지만, GCP 설정이나 문서만 병합해도 AWS drift나 아직 반영되지 않은 설정을 적용할 수 있었다.

GKE 노드 3대 기준을 기록한 PR #123은 GCP Terraform 설정·검사와 문서만 바꾸며 현재 운영 입력도 이미 3이다. 사용자는 추가 apply 없이 해당 PR을 병합하도록 요청했다. 따라서 먼저 AWS 자동 배포 대상을 명확히 하고, 기존 PR/push 검사와 수동 plan/apply 기능은 유지해야 한다. 이 결정은 실제 이전이나 strict 전환을 허용하지 않는다.

## 검토한 대안

- 모든 main push의 기존 자동 apply를 유지한다. GCP·문서 변경에서도 AWS apply 가능성이 있어 제외한다.
- workflow의 trigger를 경로로 제한하거나 CI를 건너뛴다. GCP·문서·workflow 변경의 기존 검사까지 생략할 수 있어 제외한다.
- 기존 scripts 검사에서 전체 push의 AWS 입력 변경 여부를 출력하고 배포 job만 조건부 실행한다. 검증 gate와 수동 실행을 보존하므로 선택한다.

## 결정과 이유

scripts job은 전체 Git 이력을 checkout하고 push의 `before..after` 변경 경로를 판정한다. 파일명은 NUL로 구분하고 rename은 삭제·추가 양쪽 경로로 처리한다. 마지막 커밋만 비교하지 않으므로 여러 커밋의 push에서 앞선 AWS 변경을 놓치지 않는다. 최초 main push는 after tree 전체를 확인한다. Git 조회 실패는 검사 실패로 처리하며 배포를 허용하지 않는다.

AWS 입력 범위는 현재 all-runtime의 AWS bootstrap·foundation·management·workload 루트, 공유 EKS 모듈과 다음 공통 파일이다.

- `.terraform-version`, `config/project.json`
- `scripts/tf-ci.sh`, `scripts/common.sh`, `scripts/project-config.py`
- EKS 모듈이 직접 읽는 `terraform/config/aws-load-balancer-controller-policy.json`

해당 디렉터리 안의 문서·테스트·예제도 배포 입력에서는 제외한다. GCP·Tailscale·account/platform 전용 루트, 일반 CI 정의와 경로 판정기는 현재 all-runtime의 desired config 입력이 아니다. 공용 `config/project.json`은 AWS에도 영향을 주므로 변경 필드와 관계없이 보수적으로 AWS 입력으로 분류한다. GCP 전용 경로만의 변경과 구별한다.

판정 결과는 scripts → check job output으로 전달한다. main push는 이 값이 `true`이고 기존 검사가 모두 성공해야 deploy job을 실행한다. `false`이면 AWS 인증·plan·apply 단계가 있는 job 전체를 생략한다. PR/push의 Terraform·Helm·scripts 검사, 실패·skip·취소를 차단하는 aggregate gate, 배포 직렬화와 기존 plan·삭제 보호는 유지한다.

main의 수동 `action=plan|apply`와 stack 선택은 기존대로 유지하며 기본값도 `plan + bootstrap`이다. 수동 실행은 자동 경로 판정값에 의존하지 않는다. management-scaleout의 승인 SHA 확인과 AWS 인증 순서도 바꾸지 않는다.

## 영향과 후속 작업

- 기대 효과: GCP 전용 설정·문서·테스트·workflow 정의만 병합할 때 AWS 변경을 의도치 않게 적용하지 않는다. 모든 기존 검증은 계속 실행한다.
- 단점과 위험: 새 외부 `file()`·template·모듈 입력을 추가하면 판정 범위와 회귀검사도 갱신해야 한다. 배포 입력이 바뀌지 않은 push에서는 AWS drift 조회·복구를 하지 않으므로 필요하면 기존 수동 plan을 사용한다.
- 검증: 양방향 경로 판정, 공통 정책 JSON, 혼합·삭제·rename·여러 커밋·초기 push·Git 실패와 수동 실행을 로컬 Git fixture로 검사한다. AWS·Terraform 실제 실행은 하지 않는다.
- 병합 순서: 이 workflow PR과 PR #123 각각의 실제 diff가 자동 배포 대상이 아님을 확인한다. workflow PR의 CI 통과 후 먼저 병합하고, main 실행에서 deploy가 `skipped`임을 확인한 뒤 PR #123을 병합한다. 두 번째 main 실행도 같은 상태인지 확인한다. 이는 기존 CI나 배포 guard를 우회하는 절차가 아니다.

## 관련 자료

- [AWS workflow](../../.github/workflows/terraform-check.yml), [경로 판정](../../scripts/aws-apply-scope.py)
- [workflow·Git 경계 회귀검사](../../scripts/tests/test-workflow.py), [기존 배포 스크립트](../../scripts/tf-ci.sh)
- [AWS 입력 생성](../../scripts/project-config.py), [공통 설정](../../config/project.json)
