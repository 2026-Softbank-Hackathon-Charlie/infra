# 0010. 기존 IAM 사용자에게 두 EKS의 운영자 접근 부여

- 날짜: 2026-11-02
- 상태: 채택
- 관련 ADR: [0008](0008-select-terraform-stack-and-action.md), [0009](0009-grant-ci-terraform-administrator-access.md)

## 배경과 제약

최초 EKS 배포에는 명시적인 운영자 principal이 필요하며 아직 지정되지 않았다.
현재 인증한 기존 IAM 사용자에게 접근을 부여하는 범위를 사용자에게 제시하고 배포 계획과 함께 승인받았다.
두 클러스터는 private API를 유지하고 자동 creator admin 권한은 켜지 않는다.

## 검토한 대안

- 운영자 미지정: 사전 검증에서 실패하고 운영자 접근 경로도 준비되지 않는다.
- 새 전용 운영자 역할 생성: 수명 주기와 추가 assume 권한 설계가 필요해 이번 최초 배포 범위를 늘린다.
- 기존 IAM 사용자 지정: 새 자격 증명이나 역할을 만들지 않고 현재 운영자가 두 클러스터에 접근할 수 있어 선택한다.

## 결정과 이유

기존 `arn:aws:iam::525091405886:user/softbank_jongwook`를 관리·워크로드 클러스터의 운영자로 지정한다.
로컬 계정 JSON과 GitHub Repository variable `EKS_OPERATOR_PRINCIPAL_ARN`을 일치시킨다.
[기존 EKS 모듈](../../terraform/modules/eks/main.tf)이 STANDARD Access Entry와
AmazonEKSClusterAdminPolicy의 cluster 범위를 연결한다. STS 세션 ARN이나 CI 역할을 운영자로 사용하지 않는다.
Argo deploy 역할의 기존 접근 설정과 private API·SSM bridge 구조는 그대로 사용한다.

## 영향과 후속 작업

- 기대 효과: 현재 운영자가 두 EKS의 Kubernetes 리소스를 관리할 수 있다.
- 단점과 위험: namespace 한정 접근이 아닌 두 클러스터 전체 관리자 권한이다. IAM 사용자 자격 증명 관리와 private API 연결 경로가 별도로 필요하다.
- 필요한 후속 작업 및 검증: GitHub 변수·로컬 입력 일치와 배포 후 Access Entry/정책 범위를 조회한다. 새 토큰·키를 발급하지 않는다. private API의 실제 kubectl 접속과 Pod Ready 확인은 SSM 터널 등 별도 연결 검증으로 구분한다. 이후 전용 역할로 전환하면 새 ADR과 접근 변경 계획을 작성한다.

## 관련 자료

- [공통 입력 도우미](../../scripts/project-config.py)
- [관리 EKS](../../terraform/environments/aws/dev/management/main.tf)
- [워크로드 EKS](../../terraform/environments/aws/dev/workload/main.tf)
