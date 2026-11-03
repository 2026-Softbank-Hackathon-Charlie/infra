# 앱 배포 인프라 실행 명세
사용자 목표: 앱 배포 인프라 작업1~8을 차례대로 끝내고 작업마다 설명한다. 기술적 의사결정은 사용자에게 알린다. 실제 완료한 항목만 Notion에서 체크한다. main 직접 commit 대신 브랜치와 PR 경로를 사용한다.

## 요구 근거
- [Notion 인프라 ADR](https://app.notion.com/p/40d8bee9ada481359e59c9d2d3d0782e): 2026-11-03 13:17KST 수정본. 문서 자체는 제안.
- 플랫폼 ADR0011/PR34의 기존 CodeBuild 실행부와 로그·digest 계약을 재사용한다. 이번에는 Actions 전환/별도 로그 중계/성능 최적화를 추가하지 않는다.

## 단계와 실제 완료 증거
1. [CodeBuild·ECR IAM](https://app.notion.com/p/40d8bee9ada481c5b5f3c9619a586006) — 계정/SCP/동시빌드 확인, Terraform 변경 검토·적용, 플랫폼 역할의 실제 빌드 시작·상태/로그 조회, 시험 이미지 ECR push.
2. [플랫폼 SSM·Argo RBAC·도메인](https://app.notion.com/p/40d8bee9ada481218064fc5391a2052b) — 빈 SSM 파라미터·설정 로딩·GitHub App 값 주입 담당, Argo 최소 RBAC, 게이트웨이 도메인 실제 동작.
3. [워크로드 LBC·CNPG](https://app.notion.com/p/40d8bee9ada481d286c2c119845fa88c) — 올바른 workload 대상의 LBC/CNPG Ready·CRD·E2E 버전 일치, 내부 NLB 생성.
4. [앱 차트·ApplicationSet·환경 프로필](https://app.notion.com/p/40d8bee9ada481648d95fdbb7d46ca48) — C의 검증 차트/앱셋 인계·AWS 프로필/정책 리뷰, gitops revision 추적, 시스템 namespace 차단과 격리 시험.
5. [DNS·Ingress·보안 그룹](https://app.notion.com/p/40d8bee9ada481f583d7f544c718ba9b) — 앱 wildcard DNS/TLS, 기존 api/console/app 라우팅 보존, 관리 gateway→내부 NLB 통신과 NetworkPolicy 허용/거부 증거.
6. [워크로드 Service 조회 계정](https://app.notion.com/p/40d8bee9ada4812b900acdfa00e35abe) — Service 읽기만 가능한 별도 계정, 불필요 권한 거부, C에게 SSM 저장 인계 및 플랫폼 조회, 토큰 수명/갱신 담당.
7. [S3 버킷·자격증명 방식 합의](https://app.notion.com/p/40d8bee9ada4815681e7e95cf628c9ee) — 사용자/팀의 생성 방식·주체 결정, 앱별 credential 공급·권한 분리, 시험 사진 저장/조회.
8. [이미지 인계·실제 AWS 배포 검증](https://app.notion.com/p/40d8bee9ada481e2b397dee0dfd920ab) — 이미지 digest 담당 인계, 등록5분 이내 HTTP200, push 배포, 실패 헬스 버전에서 기존 응답 유지, 두 번째 앱 infra 변경 없음.

## 전역 제약
- 순서1→8을 유지하며 구현 준비, PR, 실제 적용, 통합 검증을 구분한다. 로컬 mock plan/CI/merge만으로 runtime Done을 만들지 않는다.
- 기존 backend·resource 주소·state 소유권을 보존한다. 기존 state/tfvars/.terraform/Secret을 복사하지 않는다.
- infra/AGENTS.md는 일반 구현 요청을 권한 실행 승인으로 간주하지 않는다. 비공개 AWS 조회, 자격증명·Secret·IAM/클러스터 변경과 실제 적용/배포는 구체적 범위를 준비한 뒤 명시적 승인한다.
- 세션 AGENTS의 P0에 따라 현재 diff의 commit, 명명한 branch push, PR 생성/수정/merge를 승인 없이 하지 않는다. 준비/로컬 검증은 계속한다.
- CodeBuild 최초 동명 리소스 존재·quota는 아직 미확인이다. AWS API 조회는0회다.
- 사용자 기본 담당은 인프라다. 플랫폼 코드를 중복 구현하거나 프런트 운영/기능 QA를 사용자에게 자동 배정하지 않는다.
- Notion 본문/담당/기한을 불필요하게 바꾸지 않는다. 시작한1번은 Doing, 2~8은 Todo. 완료 조건의 실제 증거가 있어야 체크/Done으로 바꾼다.
- 7번 S3 방식과 8번 digest 작성자는 실제 분담 의사결정 gate다. 4번 삭제 수명주기 시험은 승인된 격리 앱에만 적용한다.
