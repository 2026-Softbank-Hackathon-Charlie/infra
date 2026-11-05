# 관리 노드 증설을 기존 CI로 실행하는 변경 계획

- 날짜: 2026-11-05
- 상태: 사용자 승인 범위는 구현·검증·push·PR 생성까지. merge와 workflow_dispatch 배포는 사용자가 진행하며 Agent는 실행하지 않는다.
- 분류: CI 계약과 배포 경로에 영향을 주므로 전체 계획·독립 리뷰·명시 승인 절차를 적용한다.

## 목표와 완료 기준

사용자는 노드 증설과 확인된 refresh 차이 11건 반영을 승인했고, 실제 배포를 로컬 대신 CI로 실행하도록 요청했다. 로컬 apply는 하지 않는다. 기존 main 전용 OIDC 수동 배포 경로를 이용해 관리 2a 노드 그룹만 1→2대로 늘린다. 이번 요청의 완료 기준은 CI 코드 구현·검증과 사용자가 merge할 PR 전달이다. 실제 CI 배포 및 새 노드 Ready·기존 서비스 정상 확인은 머지 후 후속 작업이다. ClickStack 설치는 포함하지 않는다.

## 확인한 기존 구성과 변경 이유

- ADR0003/0008/0009에 따라 `.github/workflows/terraform-check.yml`은 main의 workflow_dispatch와 전체 검사 성공 후 OIDC로 선택 stack만 실행한다. `scripts/tf-ci.sh`는 saved plan을 같은 job에서 apply한다. plan/state artifact를 업로드하지 않는다.
- 기존 concurrency group `charlie-terraform-apply`, cancel-in-progress=false와 기존 CI 역할·신뢰 정책을 유지한다. IAM 변경이나 feature branch 배포는 하지 않는다.
- 현재 management 실행은 추가 변경 제한이 없다. 로컬에서 확인한 노드 수량 1건 및 특정 refresh 차이만 허용하는 검사를 CI에도 적용해야 한다.
- 기존 ClickStack 브랜치는 아직 미공개이며 다수의 별도 구현을 포함한다. 증설 PR에는 ClickStack chart·Secret·애플리케이션 설정을 넣지 않는다.

## 범위·파일·의존성

1. 승인 후 원격 main과 관련 PR/check/workflow 상태를 읽고, 최신 main 기반의 새 `feat/management-clickstack-capacity-ci` 브랜치와 별도 worktree를 만든다. upstream에 이미 같은 변경이 있거나 코드/ADR 번호 충돌이 있으면 중복하지 않고 영향 범위를 재확인한다. 사용자 원본 worktree는 수정하지 않는다.
2. management root의 기존 `node_count_by_az = { "ap-northeast-2a" = 2 }` 한 줄을 옮긴다. shared module/workload는 바꾸지 않는다.
3. `.github/workflows/terraform-check.yml`의 stack 선택에 `management-scaleout`을 추가한다. 기본 bootstrap/plan과 기존 선택은 그대로 둔다. 새 선택은 management root만 실행한다. 수동 입력 expected_sha(string, 기본 빈 값)를 추가하며 새 선택일 때만 40자리 hex merge SHA를 요구한다. deploy job에서 OIDC 인증 이전에 github.sha == expected_sha인지 검증하고 불일치 시 종료한다. 외부 입력은 env로 전달하고 shell에 직접 삽입하지 않는다.
4. `scripts/tf-ci.sh`에서 새 선택을 management로 매핑하고, plan 후 `scripts/check-management-scaleout-plan.py` 검사를 통과해야만 해당 saved plan을 apply한다. 새 선택은 검사를 생략할 수 없으며 다른 stack과 합쳐 실행하지 않는다. 기존 management/all-runtime 동작을 임의로 제한하는 공유 기본값 변경은 하지 않는다.
5. 검사기는 stdin plan JSON을 메모리에서 읽고 아래 조건을 검증한다. state/plan 원문·비밀값은 stdout, 저장소, artifact에 남기지 않는다. CI 임시 파일은 umask 077로 보호하고 기존 trap으로 삭제한다. 새 선택의 init/plan/apply 원문 출력도 보호된 임시 로그로 캡처하고 성공·실패만 요약한다. 실패 시에도 원문 로그를 자동 출력하거나 artifact로 업로드하지 않는다. 로그에는 주소·동작·수량과 guard 통과/실패만 남긴다.
6. `scripts/tests/test-management-scaleout-plan.py`, `scripts/tests/test-tf-ci.py`, `scripts/tests/test-workflow.py`, Makefile ci-test 연결, README 파일 목록, 새 ADR 및 이 계획을 갱신한다. 별도 PR에는 용량/CI와 관련된 문서만 포함하며 ClickStack 본 구현 문서 의존성을 만들지 않는다. ADR 번호는 최신 main과 현재 작업 브랜치의 번호 예약을 함께 확인해 충돌을 피한다. 분리 PR의 링크는 main에 존재하는 파일과 그 PR에 포함한 문서만 사용한다.

## plan 검사 계약

- 계정 525091405886, 리전 ap-northeast-2, `charlie-dev-management`의 `module.eks.aws_eks_node_group.az["ap-northeast-2a"]`만 update한다. min/desired/max가 모두 1→2이며 나머지 before/after 값은 동일해야 한다.
- 대상 외 managed resource는 no-op여야 한다. data source read는 허용하되 관리 리소스 생성·삭제·교체·이동·import·forget과 deferred changes는 거부한다. 대상 변경에 미확정 값이 있거나 output 변경이 있으면 거부한다. 지원하지 않는 plan 형식은 실패로 처리한다.
- refresh 차이는 사용자에게 설명한 11건만 허용한다. 주소순 정렬 후 각 주소·actions와 실제로 다른 필드의 before/after만 추출해 `json.dumps(sort_keys=True,separators=(",",":"))`로 만든 SHA256을 승인된 로컬 결과와 비교한다. 원본 state/plan은 커밋하지 않는다. hash `495482837a709de5d095c17f8d88ccbc3fbe2ae1500d60725ce486f76e554f98`만 검사기에 고정한다. 새 plan에서 정확히 같은 차이가 아니면 중단하며 예외를 자동 확대하지 않는다.
- IAM 연결 목록과 SG 규칙은 별도 attachment/rule 리소스의 계획값과도 일치해야 한다. EKS access scope와 LT 차이는 null→[] 정규화에 한정한다.
- 이미 적용된 no-op 경로는 별도로 처리한다. 대상 min/desired/max=2 및 2c=1, 다른 managed/output 변경 없음, unknown/import/move/deferred 없음과 대상 identity를 확인한다. update 경로에서만 11건 drift hash 일치를 요구하며, no-op 경로는 drift=0일 때만 apply를 호출하지 않고 종료한다. no-op에 다른 refresh 차이가 있거나 수량이 예상과 다른 부분 적용 상태이면 자동 복구하지 않고 중단한다.

## 검증·실행 순서

1. 새로운 계획 리뷰 에이전트 검토, 지적 반영, 최종 개발 계획과 아래 실제 실행 범위를 사용자에게 명시적으로 승인받는다. 기존 증설·11건 refresh 승인 사실은 유지한다.
2. 로컬에서 구현 후 guard 정상·예외·악성 변경 사례 테스트: 주소/필드/해시 변조, 다른 리소스 update/create/delete, 대상의 IAM/LT/버전 변경, unknown/import/move/deferred, output 변경, malformed JSON을 거부한다. fake CLI 테스트로 plan 검사 실패 시 apply가 호출되지 않고 임시 plan이 정리되는지 확인한다.
3. fmt/validate 및 management root·shared EKS mock tests, ci-test, inventory-check를 수행한다. Git diff에서 증설 관련 변경만 있는지 확인한다.
4. 새 브랜치를 push하고 PR을 생성한다. GitHub의 검사·리뷰·브랜치 보호 규칙을 준수하며 우회하지 않는다. 검사 성공 여부를 기록하고 PR을 사용자에게 전달한다. 사용자 요청에 따라 Agent는 merge하지 않는다.
5. 후속 사용자 작업: merge SHA를 확인하고 기존 workflow를 main에서 `action=plan, stack=management-scaleout, expected_sha=<merge SHA>`로 한 번 실행해 성공을 확인한다. main이 다른 SHA로 바뀌면 자동 apply하지 않고 새 변경 범위를 확인한다.
6. 후속 사용자 작업: 실행 직전 기존 승인 범위에서 계정·vCPU quota/사용량·subnet IP·노드 그룹 상태를 다시 읽기 전용 확인한다. 사전 조건과 main/PR 범위가 동일하고 CI plan guard가 통과한 경우 `action=apply, stack=management-scaleout, expected_sha=<동일 merge SHA>`를 실행한다. apply job에서 새 plan을 생성하고 같은 guard를 다시 검증한 뒤 그 saved plan만 적용한다. plan 실행과 apply 실행 사이의 동일 파일을 전달한다고 주장하지 않는다.
7. 후속 사용자 작업: 관리 EKS 상태를 읽어 ACTIVE·Ready 노드 3대·기존 워크로드 Ready·Pending·CPU 여유를 확인한다. 이 확인은 CI 결과 및 기존 승인된 CLI 읽기 전용 경로를 사용하며 로컬에서 배포 명령을 실행하지 않는다. 실패 시 로그의 비밀이 아닌 요약만 확인하고 다른 인프라 변경이나 자동 축소는 하지 않는다.

## 권한·위험·승인 범위

추가 실행 범위는 해당 GitHub 저장소의 관련 코드/PR/check/run/비밀값을 제외한 배포 설정 메타데이터 조회, 새 브랜치 push·PR 생성·검사 상태 조회까지다. PR merge와 main 수동 plan/apply 실행은 사용자가 진행하며 이번 Agent 실행 범위에 포함하지 않는다. CI는 기존 OIDC 역할로 management/foundation state 조회·management state 잠금/쓰기 및 지정 노드 그룹 scaling과 EC2 1대·루트 gp3 30GiB 생성을 수행한다. 새 자격 증명·GitHub Secret·IAM/브랜치 보호 변경은 없다. 로컬 apply·다른 stack 실행·ClickStack 설치는 제외한다.

CI 역할 자체는 ADR0009에 따라 관리자 권한을 가진다. 이번 실행의 범위 제한은 검토한 코드·main 게이트·검사기이며 IAM상 최소 권한이라고 표현하지 않는다. PR merge는 사용자가 직접 수행한다. 기존 전체 증설 비용 약 $88.66/730시간과 데이터·축소 위험은 유지한다. CI runner에서 guard가 실패하면 원인만 보고하고 검사 생략으로 우회하지 않는다.

## 관련 자료

- 기존 용량 점검·증설 승인은 이 문서의 배경에 요약했다. 별도 PR에서 ClickStack 미병합 문서를 가져오지 않는다.
- [ADR0003](../adr/0003-share-project-inputs-between-local-and-ci.md)
- [ADR0008](../adr/0008-select-terraform-stack-and-action.md)
- [ADR0009](../adr/0009-grant-ci-terraform-administrator-access.md)

## 리뷰·검증 결과

새 plan-reviewer `review_management_ci_execution`에서 GREEN 판정, 중대 미해결 지적 없음. 승인 merge SHA의 CI 내부 검증, no-op 경로 분리 및 문서 링크 독립성을 반영했다. 별도 최신 main 브랜치에서 구현했고 inventory 395개와 diff 검사를 통과했다. 관리 root validate·mock 1개, 공유 모듈 validate·mock 10개 및 guard·CI orchestration·workflow 검사에 통과했다. 전체 ci-test는 macOS의 기존 Tailscale GNU sed 의존 테스트 1건에서 실패했으며, 관련 코드는 수정하지 않았다. Linux CI 결과를 PR에서 확인한다. 계획 대비 변경은 최신 main(749c88e)의 self-hosted runner와 네트워크 정책 코드를 그대로 유지하고, 최신 ADR 목록에 맞춰 번호를 0047로 정한 것이다. 실제 인프라 변경은 없다. CI 실행 방식 결정은 사용자 지시에 따라 ADR0047에 채택으로 기록한다. 실행 범위는 PR 생성까지이며 실제 배포 완료를 의미하지 않는다.

## 머지 후 사용자 실행 방법

1. 이 PR의 검사를 확인하고 사용자가 main에 merge한다.
2. merge commit SHA(40자리)를 확인한다. GitHub Actions의 **Charlie infrastructure**에서 main, action=plan, stack=management-scaleout, expected_sha=해당 SHA로 수동 실행한다.
3. plan guard 통과 후 같은 SHA로 action=apply, stack=management-scaleout을 수동 실행한다. apply 실행은 새 plan을 검증하고 같은 job의 saved plan을 적용한다. 다른 SHA나 refresh 차이가 발견되면 실패하며 guard를 생략하지 않는다.
4. 적용 전 quota/IP/노드 그룹 진행 상태를 확인하고, 적용 후 노드 3대 Ready와 기존 워크로드 상태를 확인한다. Agent는 이번 요청에서 workflow_dispatch를 실행하지 않는다. PR push/생성으로 AWS 인증 없는 기존 검사 workflow만 자동 실행될 수 있다.

공개 서울 단가 기준 기존과 같은 m7i-flex.large 한 대와 루트 gp3 30GiB 추가 비용은 약 $88.66/730시간이다. 세금·트래픽·할인·ClickStack 데이터 디스크는 별도다.
