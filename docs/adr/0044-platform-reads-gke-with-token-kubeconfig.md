# 0044. 플랫폼 API가 GKE를 읽기 전용 토큰 kubeconfig로 읽는다

- 날짜: 2026-11-04
- 상태: 제안
- 관련 ADR: [ADR 0021](0021-keyless-argocd-gke-access.md), [ADR 0036](0036-app-routing-and-argo-apps.md), [ADR 0039](0039-add-gke-app-environment.md)

## 배경과 제약

게이트웨이가 GKE 앱으로 요청을 보내려면 플랫폼 API가 배포 후 앱 Service의 내부 LB 주소를 읽어 게이트웨이에 등록해야 한다(platform `internal/deploy/backends.go`의 `ServiceBackends`). API는 환경 `gke`의 kubeconfig 컨텍스트 `gke-hopcharlie`(`terraform/platform/aws/host/envs.yaml`)로 GKE에 접속한다.

- 확인한 사실(11/4):
  - SSM에는 `/hopcharlie/kube/eks`만 있고 `/hopcharlie/kube/gke`가 없다. 지금은 GKE에 배포해도 API가 내부 LB 주소를 읽지 못해 게이트웨이 등록이 실패한다.
  - GKE API의 허용 네트워크(master authorized networks)는 관리 VPC의 NAT 주소 두 개(15.165.220.134, 54.116.82.33)다. 플랫폼 pod가 있는 관리 EKS에서 GKE 공개 endpoint(34.64.99.246)로 접속할 수 있다.
  - 관리 EKS 게이트웨이에서 GKE 내부 LB까지 Tailscale 경로는 동작한다([ADR 0037](0037-connect-aws-gateway-to-gke-apps-with-tailscale-routers.md), 11/4 시험 200).
  - 플랫폼 이미지(alpine)에는 Google 인증 플러그인이 없다.

## 검토한 대안

- Argo처럼 Google WIF로 접속(ADR 0021): 이미지에 인증 플러그인을 넣거나 플랫폼 코드를 바꿔야 한다.
- 고정 주소 템플릿(`PLATFORM_APP_BACKEND_TEMPLATE`): 내부 LB IP는 배포마다 정해져 템플릿으로 만들 수 없다.
- EKS와 같은 읽기 전용 ServiceAccount 토큰 kubeconfig(선택): 코드 변경이 없고 이미 검증된 방식이다.

## 결정과 이유

- [clusters/gcp-dev-workload/hopcharlie-platform-access.yaml](../../clusters/gcp-dev-workload/hopcharlie-platform-access.yaml): EKS와 같은 `charlie-system/hopcharlie-platform` ServiceAccount, 토큰 Secret, 읽기 전용 ClusterRole. Secret과 ConfigMap은 읽지 못한다. `charlie-system`은 앱 ApplicationSet이 거부하는 namespace다.
- [scripts/put-platform-kubeconfig.py](../../scripts/put-platform-kubeconfig.py) `--env gke`: GKE 터널 kubeconfig로 RBAC를 적용하고, 토큰 권한(읽기 7개 yes, Secret·변경 7개 no)을 확인한 뒤 SSM `/hopcharlie/kube/gke`(SecureString)에 저장한다. 서버는 공개 endpoint, CA는 터널 kubeconfig의 클러스터 CA다. 토큰은 출력하지 않는다.
- 저장 후 관리 클러스터의 `hopcharlie-api`를 다시 시작한다.

## 영향과 후속 작업

- 기대 효과: 플랫폼이 GKE 환경 상태를 보고, GKE 앱의 내부 LB 주소를 게이트웨이에 등록할 수 있다.
- 단점과 위험: 오래 사는 토큰이 SSM에 있다. 권한은 읽기뿐이고 GKE API는 관리 NAT 주소에서만 받는다. 회수는 GKE의 토큰 Secret 삭제와 SSM 파라미터 삭제다.
- 후속(각각 승인 후):
  1. `python scripts/put-platform-kubeconfig.py --env gke --apply`, API 재시작, `charlie env list`에서 gke reachable 확인
  2. GKE 앱 배포 후 `*.hopcharlie.dev` → GKE 라우팅 확인(platform#61, #57 이후)

## 관련 자료

- [docs/runbooks/app-routing.md](../runbooks/app-routing.md) 3절
- platform `internal/deploy/backends.go`, `internal/envs/kubeclient.go`
