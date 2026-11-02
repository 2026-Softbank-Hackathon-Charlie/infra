# 0016. HopCharlie 플랫폼을 관리 클러스터에서 운영

- 날짜: 2026-11-02
- 상태: 채택 (2026-11-02 팀 회의 결정. 실제 배포 전)
- 관련 ADR: 없음

## 배경과 제약

- HopCharlie 플랫폼(API, 트래픽 게이트웨이, 콘솔, PostgreSQL)은 platform 레포의 `deploy/aws`가 만든 EC2 t3.small 한 대에서 Docker Compose로 돌고 있다. 비밀값과 설정 파일은 SSM Parameter Store `/hopcharlie/*`에 있고, HTTPS는 호스트의 Caddy와 sslip.io 주소로 연다.
- 관리, 워크로드 EKS는 API를 private으로만 연다. EC2 호스트는 기본 VPC에 있어서 워크로드 EKS API에 바로 닿지 않는다.
- 2026-11-02 팀 회의에서 다음을 정했다.
  - 플랫폼을 관리 EKS에서 운영한다.
  - 도메인 `hopcharlie.dev`를 Route 53 Domains에서 사고, DNS는 Cloudflare에 둔다.
  - 비밀값은 지금처럼 SSM에 둔다.
- 미확인: 관리 클러스터 노드에서 워크로드 EKS API(보안 그룹), GKE, 온프렘으로 가는 연결은 아직 확인하지 않았다.

## 검토한 대안

- EC2 호스트 유지: 지금 동작한다. 하지만 private EKS API에 닿으려면 VPC 연결을 따로 만들어야 하고, 배포가 GitOps 밖에 남는다.
- 비밀값을 Sealed Secrets로 관리: GitOps에 맞는다. 하지만 이미 나눠 준 토큰을 새로 만들어 다시 나눠야 하고, 봉인할 때 private API 너머의 controller 인증서가 필요하다.
- 비밀값을 External Secrets Operator로 동기화: SSM을 Kubernetes Secret으로 옮기는 표준 방법이다. 하지만 addon이 하나 늘고, 한 파라미터에 `KEY=value` 줄이 여러 개 있는 `db-passwords`, `rclone-env`를 키로 나누는 템플릿이 필요하다.
- 외부 노출을 NLB와 클러스터 안 Caddy(sslip.io)로 처리: 도메인 없이 바로 된다. 하지만 baseline이 준비한 공유 ALB와 ACM 경로를 두고 두 번째 진입점을 만든다.

## 결정과 이유

- `helm/charts/hopcharlie-platform` 차트를 관리 클러스터의 `charlie-platform` namespace에 Argo CD로 배포한다. Application은 `charlie-management-hopcharlie-platform`이고, gp3 StorageClass와 Load Balancer Controller가 먼저 생기도록 sync-wave 2에 둔다.
- 각 pod의 init 컨테이너(aws-cli)가 Pod Identity로 SSM `/hopcharlie/*`를 읽어 메모리 볼륨에 쓴다. 비밀값은 Kubernetes Secret과 Git에 들어가지 않는다. 지금 쓰는 토큰과 값이 그대로 이어지고, 값을 바꾸는 방법(put-parameter 후 재시작)도 같다.
- Pod Identity 역할과 연결, ECR, SSM 파라미터, ACM 인증서, Cloudflare 레코드는 platform 레포의 `deploy/aws` Terraform이 맡는다. 토큰을 포함한 이 값들은 이미 그 상태 파일에 있다. 또한 이미지 빌드, 토큰 발급과 같은 주기로 바뀐다. 이 레포는 클러스터와 그 위에 무엇을 띄우는지를 맡는다.
- PostgreSQL은 gp3 PVC를 쓰는 StatefulSet 한 개로 둔다. API의 run 상태(`-state-dir`)와 게이트웨이 상태 파일(`-state-file`)도 각각 gp3 PVC에 둔다.
- 외부 노출은 baseline의 공유 ALB(`externalAlb`)에 Ingress로 붙인다. 주소는 `api.hopcharlie.dev`(API), `app.hopcharlie.dev`(게이트웨이), `console.hopcharlie.dev`(콘솔)이다. ALB가 VPC 안 주소에서 pod로 보내는 트래픽과 헬스 체크는 별도 NetworkPolicy로 허용한다.
- API와 게이트웨이는 baseline이 막는 80, 5432 포트로 나가야 한다. 이전 대상 DB와 앱에 연결하기 때문이며, 별도 egress NetworkPolicy로 허용한다.
- 이미지는 다른 addon처럼 digest로 고정하고 [images.lock.json](../../helm/images.lock.json)에 기록한다.

## 영향과 후속 작업

- 기대 효과
  - 플랫폼이 EKS와 같은 VPC에 있게 되어 워크로드 EKS의 private API에 닿을 수 있다.
  - 플랫폼 배포가 이 레포의 PR과 Argo CD로 모인다.
- 단점과 위험
  - 게이트웨이와 API는 EBS 볼륨을 한 pod만 쓰도록 Recreate로 배포한다. 그래서 배포할 때 게이트웨이가 몇 초 끊긴다.
  - PostgreSQL이 단일 인스턴스다.
  - 온프렘(Tailscale) 연결은 아직 없다. EC2 호스트에는 Tailscale 설정이 있었지만, pod에는 사이드카를 따로 붙여야 한다.
  - 새 PostgreSQL은 비어 있다. EC2 호스트의 Plan, run, 감사 로그를 옮기려면 전환할 때 pg_dump로 따로 옮긴다.
  - 비용: gp3 PVC 13GiB가 월 약 1.2달러다. pod는 기존 노드에 올라가므로 노드 비용은 늘지 않는다. 공유 ALB를 켜면 시간당 약 0.0225달러와 LCU 요금이 붙는다. 대신 EC2 호스트(하루 약 0.8달러)를 정리한다. 정리할 때는 Application을 지운 뒤 namespace의 PVC를 지운다.
- 필요한 후속 작업 및 검증 (순서대로, 각 단계는 승인 후 실행)
  1. platform `deploy/aws`: Pod Identity 역할과 연결을 apply한다.
  2. 이 변경을 merge하면 Argo CD가 차트를 배포한다. pod가 SSM을 읽고 Ready가 되는지 확인한다.
  3. `hopcharlie.dev`의 네임서버를 Cloudflare로 바꾸면 platform `deploy/aws`에서 ACM 인증서를 발급한다. 그다음 이 레포에서 baseline `externalAlb`(인증서 ARN 포함)과 플랫폼 `ingress.enabled`를 켠다.
  4. 콘솔을 `https://api.hopcharlie.dev` 기준으로 다시 빌드하고, Cloudflare 레코드를 ALB로 연결한다.
  5. 새 주소로 CLI와 콘솔을 확인한 뒤 EC2 호스트를 destroy한다.

## 관련 자료

- [플랫폼 차트](../../helm/charts/hopcharlie-platform/Chart.yaml)
- [관리 클러스터 값](../../clusters/aws-dev-management/values/hopcharlie-platform.yaml)
- [Argo Application](../../helm/gitops/templates/applications.yaml)
- platform 레포 `deploy/aws` (EC2 호스트, SSM 파라미터, ECR)
- [EKS Pod Identity](https://docs.aws.amazon.com/eks/latest/userguide/pod-identities.html)
