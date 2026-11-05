# 0051. CI에서 관리 ClickStack을 부트스트랩하는 실행 경로

- 날짜: 2026-11-05
- 상태: 채택 — C 개발 계획·코드 구현 승인. 실제 권한·Secret 생성 및 배포는 미승인/미실행
- 관련 ADR: [0050 ClickStack 선택](0050-adopt-clickstack-for-hackathon-observability.md), [0009 CI IAM 관리자](0009-grant-ci-terraform-administrator-access.md), [0047 CI 노드 증설](0047-run-management-scaleout-through-ci.md)

## 배경과 제약

사용자는 실제 배포를 로컬이 아닌 CI에서 수행하기를 요청했다. 기존 CI IAM 역할의 AWS 권한과 EKS Kubernetes 권한은 별개다. 로컬 운영자 입력에 CI 역할은 없고 실제 access mapping은 아직 조회하지 않았다. 신규 Secret과 Argo Application 등록을 위한 첫 접근 경로를 정해야 한다. 해커톤에서는 기존 main/OIDC/SSM/Argo를 재사용하고 새로운 장기 배포 토큰을 운영하지 않는 것이 목표다.

## 검토한 대안

- 로컬 운영자 bootstrap: 기존 접근을 재사용하지만 사용자의 CI 실행 방침과 다르므로 제안하지 않는다.
- 영구 CI cluster-admin: 이후 실행이 쉽지만 Kubernetes 관리자 접근을 상시 유지하므로 선택하지 않는다.
- 전용 최소 RBAC bootstrap: 권한을 좁힐 수 있으나 RBAC를 최초 설치할 별도 bootstrap 주체가 필요하다. 장기 운영 전 개선 대안이다.
- 기존 CI 역할에 일시적인 EKS cluster-admin mapping: 별도 장기 credential 없이 초기 설치할 수 있다. 모든 namespace에 대한 유효 권한과 정리 실패 위험을 명시적으로 승인받는 조건으로 제안한다.

## 결정과 이유

새 main 전용 수동 workflow에서 지정 CI 역할의 access entry가 없는 경우에만 owner/run ID를 붙여 생성하고 cluster-admin policy를 연결하는 방식을 제안한다. 기존 mapping은 수정하거나 삭제하지 않는다. SSM을 통해 private EKS API에 접근하고 Secret allowlist와 고정 SHA의 관측 네 앱만 처리한다. 애플리케이션 리소스 설치는 Argo operation.sync로 실행하며 root 일괄 sync는 하지 않는다.

정상/실패 cleanup과 독립적인 own-access cleanup 액션을 둔다. runner 강제 종료 때 자동 정리를 보장하지 못하며, 같은 CI IAM 역할의 다른 세션에도 cluster-admin이 적용될 수 있다. IAM 역할이 이미 AWS 관리자라는 사실은 이 추가 권한의 승인 필요성을 없애지 않는다. 코드 allowlist를 Kubernetes 권한의 상한으로 표현하지 않는다.

## 영향과 후속 작업

- 임시 EKS 접근 변경, 지정 Secret 11개, operator/RBAC·PVC·Argo 앱과 테스트 계정/데이터가 실제 실행 승인 대상이다.
- AppProject 목적지를 management로 제한하는 새 값과 검증을 후속 PR에 구현한다. 다른 클러스터 수집은 확장하지 않는다.
- private CA의 개인키도 지정 Kubernetes Secret에 보관한다. 데모용 leaf7일/CA30일 갱신 후속 작업과 비용·실패 시 잔존 리소스를 보고한다.
- 독립 계획 리뷰·사용자 개발 승인 후 구현하며 PR merge와 실제 CI 실행 권한은 별도 구분한다. 사용자의 개발 계획 승인은 받았으며 코드 구현만 진행했다. 실제 실행 승인을 의미하지 않는다.

## 관련 자료

- [C 설치 계획과 권한·Secret 목록](../plans/clickstack-management-deployment.md)
- [AWS EKS policy 권한](https://docs.aws.amazon.com/eks/latest/userguide/access-policy-permissions.html)
- [AWS access entry 삭제](https://docs.aws.amazon.com/eks/latest/userguide/deleting-access-entries.html)
- [Argo kubectl sync](https://argo-cd.readthedocs.io/en/stable/user-guide/sync-kubectl/)

## 구현 보완

GitHub run ID에 attempt 태그를 더해 재실행이 이전 attempt의 mapping을 자동 삭제하지 않도록 했다. 합성 event ID도 attempt별로 구분해 재실행 시 기존 이벤트와 중복 집계하지 않는다. 인증서는 Python cryptography50.0.2로 메모리에서 만들고 Python 의존성 release hash를 고정했다. kubectl1.35.8 및 SSM plugin1.2.896.0의 공식 공개 배포본 checksum을 잠갔다.

정상 실행에서는 access entry 삭제 뒤 터널을 닫기 전에 Kubernetes 요청이 거부되는지 확인한다. 강제 종료 시 이 확인을 보장하지 못하며 별도 cleanup 액션은 owner/run을 검증한 API mapping 정리 결과만 보고한다. 브라우저 조작 및 실제 queue backlog 재생은 검증 완료로 주장하지 않는다.
