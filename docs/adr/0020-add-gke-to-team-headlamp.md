# 0020. 팀 Headlamp의 GKE 연결에 개인 Google 인증을 분리한다

- 날짜: 2026-11-03
- 상태: 제안
- 관련 ADR: [0013 GCP workload](0013-add-gcp-workload.md), [0017 팀 Headlamp](0017-team-headlamp-with-cognito.md)

## 배경과 제약

기존 Headlamp 제안은 Cognito를 신뢰하도록 설정한 AWS 두 EKS에 읽기 전용 그룹 권한을 연결한다. 이미 생성한 GKE는 기본 Google IAM 인증이며 같은 Cognito 토큰을 그대로 신뢰하지 않는다. GKE를 새로 만들거나 기존 AWS 로그인을 바꾸지 않고 같은 Headlamp에 표시하려고 한다.

Headlamp v0.46.0은 kubeconfig context별 OIDC 설정이 있으면 OIDC 로그인을, 없는 user면 token 로그인을 선택한다. OIDC token broadcast도 동일 issuer/client를 가진 context만 대상으로 한다. GKE 외부 IdP 연결용 구형 Identity Service는 Google 공식 문서상 2026년7월부터 deprecated이며, 이 연동을 위해 활성화하지 않는다.

## 검토한 대안

- GKE 주소에 Cognito 설정 복사: GKE API의 토큰 신뢰 설정이 없으므로 제외한다.
- Workforce Identity Federation으로 Cognito 통합: 장기 선택지지만 조직·IAM·연합 인증 설정 변경이 필요하므로 이번 최소 연동에서 보류한다.
- 공유 관리자/서비스 계정 토큰 저장: 개인 식별·권한 회수·비밀 관리 문제가 있어 제외한다.
- 개인 Google access token 로그인: 기존 IAM/RBAC를 사용하며 추가 GKE 리소스 없이 연결 가능하므로 제안한다.

## 결정과 이유

관리 EKS의 `headlamp-clusters` Secret에 GKE의 공개 endpoint·CA와 자격 증명 없는 context/user만 추가한다. AWS OIDC 설정·client secret은 메모리에서 그대로 보존하며, Google 토큰은 이 Secret이나 파일에 넣지 않는다. 등록 도구의 기본 동작은 네트워크·Secret 조회 없는 공개 메타데이터 미리보기다. 명시적 승인 후 opt-in 실행만 기존 Secret 조회·갱신과 Headlamp Deployment 재시작을 수행한다.

이는 **GKE 조회 전용을 강제하지 않는다**. Google Owner/관리자 토큰은 기존 권한에 따라 리소스를 변경할 수 있다. 조회 전용 ClusterRole 추가는 기존 IAM의 더 큰 권한을 제거하지 않는다. 실제 조회 전용을 원하면 별도 제한된 개인 identity·IAM/RBAC 정책을 검토·승인해야 한다. Google token은 다른 Cloud API 권한도 가질 수 있어 프로젝트 전용 계정·신뢰된 서버로 범위를 제한한다.

## 영향과 후속 작업

- Secret 값은 메모리와 stdin에서만 처리하고 출력·디스크·명령 인자에 남기지 않는다. `resourceVersion`이 포함된 replace로 동시 수정을 감지한다.
- 기존 Secret의 다른 필드와 AWS context를 보존한다. 이름 충돌은 자동 덮어쓰지 않으며 같은 구성의 재실행은 불변이다.
- GKE는 기존 AWS NAT 허용 CIDR을 사용하고 광범위한 방화벽·인증 변경을 하지 않는다. 실제 Headlamp egress·TLS·계정 권한은 live 검증이 필요하다.
- 별도 management kubeconfig와 SSM 터널이 선행한다. 기본 kubeconfig를 변경하거나 Session Manager 도구를 자동 설치하지 않는다.
- Secret 교체·재시작·개인 토큰 발급·입력의 구체적 승인을 받고 rollout/세 context 로그인·세션 만료를 검증해야 한다. 코드 준비를 실제 연동 완료로 기록하지 않는다.
- 기존 `prepare-headlamp.py --install-secret` 실행은 AWS 목록으로 Secret을 재생성하므로 GKE 재등록 절차가 필요하다. 실제 GitOps/Secret 소유권 이관은 별도다.

## 관련 자료

- [GKE 운영 절차](../headlamp-gke.md)
- [Headlamp 멀티 클러스터](https://headlamp.dev/docs/latest/installation/in-cluster/)
- [Headlamp OIDC와 token broadcast](https://headlamp.dev/docs/latest/installation/in-cluster/oidc/)
- [GKE 인증](https://docs.cloud.google.com/kubernetes-engine/docs/authentication)
- [GKE 외부 IdP와 Identity Service 지원 상태](https://docs.cloud.google.com/kubernetes-engine/docs/how-to/oidc)
