# 0017. 팀 공용 Headlamp에 Cognito 로그인과 조회 전용 권한 연결

- 날짜: 2026-11-03
- 상태: 제안
- 관련 ADR: [0014](0014-local-eks-access-commands.md), [0016](0016-run-hopcharlie-platform-on-management-cluster.md)

## 배경과 제약

팀원이 개인 PC의 AWS CLI·SSM 터널 없이 URL로 관리·워크로드 EKS의 실제 상태를 확인하려고 한다.
이는 관측 가능성과 실제 운영 상태 시각화를 보완한다. 저장소에 기존 공용 ALB 및 와일드카드 ACM 설정이 있다.
실제 ALB·인증서 상태, OIDC 연결 유무, 배포자 권한과 여유 노드 용량은 아직 조회하지 않았다.

## 검토한 대안

- 개인 Headlamp와 SSM: 기존 방식이지만 팀원마다 도구와 EKS 권한을 설정해야 한다.
- 공용 관리자 토큰 또는 로그인 없는 서비스 계정 공유: 사용자별 접근 통제가 없어 제외한다.
- Cognito OIDC와 사용자 토큰: 각자 로그인하고 EKS에서 직접 인증·권한 검사하므로 선택한다.
- 기존 Google/OIDC: 이미 운영하는 IdP가 확인되면 대체할 수 있다. 현재 제공된 것은 IAM 콘솔 계정이다.

## 결정과 이유

제안 구성은 `headlamp.hopcharlie.dev`의 HTTPS Ingress가 기존 관리 ALB를 공유하고 관리 EKS의 Headlamp 한 개로 연결된다.
Cognito는 관리자만 계정을 생성할 수 있으며 `team-viewers` 그룹만 두 EKS에서 조회한다.
EKS에 issuer/client를 등록하고 `headlamp:` 접두어로 그룹을 격리한다. 기존 IAM 인증은 유지한다.
Headlamp Pod에는 API 권한이나 서비스 계정 토큰을 주지 않고 실제 로그인 사용자의 OIDC 토큰을 전달한다.
Secret·ConfigMap 조회 및 workload 변경·exec 권한은 제외한다. 로그·배포 YAML에는 애플리케이션이 기록한 민감 정보가 있을 수 있다.
노드·Pod 리소스 조회와 권한 확인용 SelfSubjectAccessReview 생성만 허용한다.

## 영향과 후속 작업

- Cognito 팀원 계정은 IAM 콘솔 계정과 별개이며 개인별로 발급한다. 사용자에게 따로 승인받지 않고 이메일을 보내지 않는다.
- 기존 ALB와 노드를 재사용하므로 새 ALB·EC2의 고정비는 계획하지 않는다. ALB 사용량과 기존 노드 자원은 증가한다.
- Cognito Lite는 공식 가격표의 계정/조직 무료 MAU 범위 내 소규모 사용 시 추가 사용자 요금이 없을 수 있다. 무료 여부는 계정 사용량에 따라 달라진다.
- 암호화된 기존 S3 버킷의 별도 state key에 클라이언트 secret이 저장된다. Kubernetes Secret은 stdin으로 등록하고 로그·Git에는 기록하지 않는다.
- workload private API에 management 노드 SG에서 TCP 443 접근하는 규칙을 추가한다. 실제 기존 규칙과 충돌하면 먼저 중복 소유 여부를 확인한다.
- EKS에 이미 외부 OIDC provider가 있다면 적용을 중단한다. 기존 provider를 대체하거나 클러스터를 새로 만들지 않는다.
- 로컬 검증 후 실제 조회·생성·인증 설정·DNS·배포 변경의 구체적 승인을 받는다. 자동 GitOps 활성화는 기본 false이다.
- 삭제 순서는 운영 문서에 따른다. 실제 배포, 사용자 로그인, 부정 권한 검증이 끝나기 전에는 완료로 표현하지 않는다.

## 관련 자료

- [운영 절차](../headlamp-team.md)
- [Headlamp 공식 OIDC](https://headlamp.dev/docs/latest/installation/in-cluster/oidc/)
- [Cognito 가격](https://aws.amazon.com/cognito/pricing/)
