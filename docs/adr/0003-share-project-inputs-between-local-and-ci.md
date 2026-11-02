# 0003. 로컬과 CI의 프로젝트 입력을 하나의 JSON 구성으로 관리

- 날짜: 2026-11-02
- 상태: 채택
- 관련 ADR: [0002](0002-require-approval-for-privileged-operations.md)

## 배경과 제약

로컬 tfvars는 CI로 전달되지 않아 프로젝트·네트워크 값이 다르게 적용될 수 있었다.
사용자는 공통 설정 적용과 기존 이름 정합성을 요청했고, 독립 검토를 거친 개발 계획을 승인했다.
이 승인은 실제 AWS 접근·배포·state 이전 승인이 아니다. 기존 state 소유권과 리소스 주소를 유지해야 한다.

## 검토한 대안

- root별 tfvars와 GitHub 변수 중복 관리: 간단하지만 값이 쉽게 어긋난다.
- HCL 입력을 임의 파싱해 병합: 문법·자동 로딩·우선순위에 따른 누락 위험이 있다.
- 공통 JSON과 별도 계정 JSON에서 root별 입력 생성: 명시적인 스키마와 표준 라이브러리로 같은 입력을 생성할 수 있어 선택한다.

## 결정과 이유

[config/project.json](../../config/project.json)을 비밀 아닌 공유 설정의 기준으로 삼고,
[도우미](../../scripts/project-config.py)가 root별 변수와 backend JSON을 임시 생성한다.
계정별 입력은 Git에서 제외한 `config/account.local.json` 또는 CI 변수로 제공하며 자격 증명을 넣지 않는다.
이번 범위는 charlie/dev와 기존 클러스터명·state key를 유지한다. 기존 OIDC provider의 소유권은 이전하지 않는다.
기존 tfvars·auto tfvars·override·backend.hcl 및 숨은 Terraform 입력은 조용히 병합하지 않고 이관을 요구한다.
기존 backend는 초기화 메타데이터의 대상과 잠금·암호화 설정을 확인하고 판독 불가·불일치 시 중단한다.
기본 workspace만 허용한다. 자동 migration/reconfigure/force-copy는 사용하지 않는다.
배포 전 저장소·브랜치를 확인하되 fork PR의 공개 검증은 막지 않는다.

## 영향과 후속 작업

- 기대 효과: 로컬·CI에서 공유 값과 명명 규칙이 같아지고 설정 충돌을 실행 전에 발견한다.
- 단점과 위험: 기존 로컬 입력은 수동으로 JSON에 이관해야 하며 기존 backend가 다르면 별도 검토가 필요하다. 직접 Terraform 명령을 실행하면 wrapper 보호를 우회할 수 있다.
- 필요한 후속 작업 및 검증: 입력 동등성·스키마·충돌·backend/workspace 거부 테스트와 AWS 없는 전체 검증을 수행한다. 실제 IAM 충분성과 배포 성공은 미확인이다. 최초 bootstrap/state 이전은 운영자의 별도 승인된 수동 작업이다.

## 관련 자료

- [로컬 진입점](../../scripts/tf-stack.sh)
- [CI 진입점](../../scripts/tf-ci.sh)
- [README](../../README.md)
