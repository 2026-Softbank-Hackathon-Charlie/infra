# 0058. 워크로드 클러스터의 앱 진입점을 Traefik과 내부 NLB 하나로 모은다

- 날짜: 2026-11-05
- 상태: 제안
- 관련 ADR: [ADR 0036](0036-app-routing-and-argo-apps.md), [ADR 0037](0037-connect-aws-gateway-to-gke-apps-with-tailscale-routers.md), [ADR 0039](0039-add-gke-app-environment.md), [ADR 0046](0046-use-strict-network-policy-on-aws-workload.md), platform ADR 0011(GitHub 앱 배포), ADR 0015(브랜치 인스턴스)

## 배경과 제약

[ADR 0036](0036-app-routing-and-argo-apps.md)의 경로에서는 앱 인스턴스마다 공개 Service가 내부 NLB를 하나씩 받는다. 플랫폼 배포 실행기는 그 Service의 NLB 주소를 읽어(`internal/deploy/backends.go`의 `ServiceBackends`) 게이트웨이에 등록한다.

확인한 사실(2026-11-05):

- 워크로드 EKS에 앱용 내부 NLB가 2개 있다(moimroom, docker-doom). 서울 리전에서 NLB 하나는 트래픽과 관계없이 월 약 16달러이고, 브랜치 인스턴스도 하나씩 받는다.
- docker-doom의 첫 배포는 5분 22초였다. 빌드 1분 58초, gitops 커밋 3초, Argo CD Healthy까지 15초, NLB가 실제로 답하기까지 3분 6초다. 목표인 5분을 넘었다. 다른 환경으로 옮길 때도 대상 환경의 로드밸런서를 같은 만큼 기다린다.
- 게이트웨이는 `httputil.ProxyRequest.SetURL`로 백엔드 주소의 호스트를 Host 헤더로 보낸다. 백엔드 주소만 호스트 이름으로 바꾸면 클러스터 안에서 호스트로 앱을 나눌 수 있다. 게이트웨이 코드는 바꾸지 않는다.
- 플랫폼에는 백엔드 주소의 틀(`PLATFORM_APP_BACKEND_TEMPLATE`)이 이미 있다. 틀이 모든 환경에 하나라서 eks만 먼저 옮기려면 환경마다 틀을 정해야 한다(platform PR #73).
- 관리 EKS의 CoreDNS는 `forward . /etc/resolv.conf`로 VPC resolver에 묻는다. VPC `vpc-04b7c6a79ad46fcdc`(`10.40.0.0/16`)는 DNS 지원과 DNS 호스트 이름이 켜져 있다. Route 53 hosted zone은 아직 없다.
- Argo CD는 Ingress의 `status.loadBalancer`가 채워져야 Healthy로 본다. 진입점 컨트롤러가 Ingress status를 쓰지 않으면 배포가 `PLATFORM_ARGOCD_TIMEOUT`(10분)까지 기다리다 실패한다.
- 워크로드 EKS의 앱이 아닌 애드온은 `charlie-root`가 그리는 Application이고 자동 sync가 없다. `charlie-root`는 sync하지 않는다([ADR 0036](0036-app-routing-and-argo-apps.md)).

제약:

- 운영 중인 moimroom과 docker-doom이 전환 중에 끊기면 안 된다. 지금 moimroom의 쓰기와 읽기는 GKE(게이트웨이에 손으로 넣은 백엔드 `http://10.80.0.7:80`)로 간다.
- GKE에는 차트로 배포한 앱이 아직 없다. 그래서 이번 범위는 워크로드 EKS(`eks`)뿐이고 GKE는 아래 후속 작업으로 둔다.

## 검토한 대안

- 앱마다 NLB 유지: 바꿀 것이 없지만 첫 배포마다 3분을 기다리고 인스턴스마다 비용이 든다.
- Terraform으로 NLB 하나를 만들고 앱마다 TargetGroupBinding: NLB는 호스트를 보지 않으므로 앱마다 리스너 포트가 필요하고, 포트 관리와 보안 그룹 규칙이 앱마다 생긴다.
- 게이트웨이가 경로나 헤더로 인스턴스를 나누기: 게이트웨이 코드를 바꿔야 하고 앱의 절대 경로가 깨질 수 있다.
- 내부 ALB 하나에 앱 Ingress를 모으기(AWS Load Balancer Controller 그룹): 앱마다 대상 그룹이 생기고 등록과 헬스 체크를 다시 기다린다. 앱마다 규칙도 늘어난다.
- 클러스터마다 Traefik과 내부 NLB 하나, 앱은 Ingress, 내부 DNS 와일드카드(선택): NLB는 한 번만 기다리고, 새 앱은 Traefik이 Ingress를 읽는 몇 초 안에 열린다. 팀 기술 제안서의 클러스터 안 진입점(Traefik)과 같다.

## 결정과 이유

1. 진입점: Traefik 차트 `42.1.2`(Traefik `v3.7.16`, 이미지 digest 고정)를 워크로드 EKS의 `traefik` 네임스페이스에 Argo CD 애드온 `charlie-workload-traefik`으로 둔다. `charlie-root`의 렌더와 같은 객체를 [scripts/register-traefik.py](../../scripts/register-traefik.py)가 만들고(AppProject 변경은 diff를 보고 apply, Application은 resources finalizer를 달고 자동 sync 없이 create), sync 한 번을 요청한다. root는 sync하지 않는다.
   - 쓰지 않는 것은 끈다. CRD provider와 차트의 CRD 25개(`skipCrds`), 대시보드, 443 포트 노출.
   - IngressClass `traefik`은 기본 클래스가 아니고, Traefik은 그 클래스를 명시한 Ingress만 읽는다(`providers.kubernetesIngress.ingressClass`). Ingress status에 NLB 주소를 쓴다(`publishedService`).
2. NLB: Traefik Service 하나가 내부 NLB 하나를 받는다. 대상은 pod IP, 이름은 `hopcharlie-apps-eks`(고정, 바꾸지 않는다. 바꾸면 컨트롤러가 NLB를 새로 만들고 DNS 이름이 바뀐다), cross-zone 켬, 등록 해제 지연 30초, 출발지는 관리 서브넷 `10.40.0.0/19`(게이트웨이와 플랫폼 API)만 연다. Service에 `argocd.argoproj.io/sync-options: Delete=false,Prune=false`를 달아 Application을 지우거나 prune해도 NLB가 남는다.
3. 무중단:
   - `traefik` 네임스페이스에 `elbv2.k8s.aws/pod-readiness-gate-inject=enabled`(Application의 `managedNamespaceMetadata`)를 단다. 새 pod는 NLB 대상이 healthy가 된 뒤 Ready가 되므로, 롤링 업데이트가 NLB에 등록되지 않은 pod로 넘어가지 않는다.
   - 멈추는 pod는 `requestAcceptGraceTimeout` 20초 동안 새 연결을 계속 받고(NLB가 등록을 해제하는 동안), `graceTimeOut` 30초 안에 남은 요청을 끝낸다. pod 종료 유예는 60초다.
   - replica 2개, PodDisruptionBudget `minAvailable: 1`, 노드에 나눠 둔다.
4. 헤더와 시간 제한: `forwardedHeaders.trustedIPs: 10.40.0.0/16`과 `notAppendXForwardedFor`로 앱은 지금처럼 게이트웨이가 보낸 `X-Forwarded-For`, `X-Forwarded-Proto`(https), `X-Forwarded-Host`(`<instance>.hopcharlie.dev`)를 본다. 요청 읽기 시간 제한은 두지 않고(`readTimeout: 0`, 긴 업로드와 WebSocket), 백엔드 유휴 연결은 30초 뒤 닫는다(`serversTransport.forwardingTimeouts.idleConnTimeout`).
5. 내부 DNS(다음 PR): Route 53 private hosted zone `hopcharlie.internal`을 VPC에 연결하고 `*.eks.hopcharlie.internal`을 공용 NLB의 alias로 둔다. 워크로드 Terraform root에 두고, NLB는 컨트롤러 태그로 찾아 없으면 레코드를 만들지 않는다(plan이 실패하지 않는다).
6. 앱 차트(다음 PR): 공개 서비스마다 Ingress `<app>-<service>`(클래스 `traefik`, 호스트 `<instance>.eks.hopcharlie.internal`)를 그린다. AppProject `hopcharlie-apps`에 Ingress를 허용한다. EKS에서는 Traefik pod 주소가 기존 `gatewayFrom`(`10.40.0.0/16`) 안이라 NetworkPolicy를 바꾸지 않는다.
7. 플랫폼: `deploy.backendTemplate`으로 `PLATFORM_APP_BACKEND_TEMPLATE=eks=http://{instance}.eks.hopcharlie.internal`을 넘긴다. platform PR #73이 들어간 이미지가 infra에 고정된 뒤에만 켠다. 옛 이미지는 이 값을 모든 환경의 틀로 읽는다. `gke`와 `onprem`은 지금처럼 LoadBalancer 주소를 읽는다.
8. 전환 순서: docker-doom을 재배포해 게이트웨이 eks 백엔드를 `http://docker-doom.eks.hopcharlie.internal`로 바꾼다(배포 실행기가 새 주소가 답한 뒤에만 바꾼다). moimroom은 배포가 활성 gke 쓰기 백엔드를 지키는 플랫폼 수정이 들어간 뒤 한 번 재배포한다. 게이트웨이의 어떤 백엔드도 `elb.amazonaws.com`이 아니게 된 뒤에 eks 프로필의 공개 Service를 `ClusterIP`로, quota `services.loadbalancers`를 0으로 바꾼다. 그때 컨트롤러가 앱 NLB를 지운다.
9. 이름 보호: `traefik`을 앱 ApplicationSet의 `deniedNamespaces`(와 같은 목록을 유지하는 GKE `protectedNamespaces` 값), 플랫폼의 예약 앱 이름(platform PR #73), 워크로드 strict NetworkPolicy 호환 목록에 넣는다.

## 영향과 후속 작업

- 기대 효과: 첫 배포가 NLB를 기다리지 않는다. docker-doom 기록으로 약 2분 20초(빌드 약 2분, 커밋, Argo CD 약 15초, 확인 몇 초)이고 재배포는 30초 안이다. 앱용 NLB는 앱과 브랜치 수와 관계없이 하나다. 지금 기준 월 약 33달러가 약 17달러(NLB 하나와 hosted zone 0.5달러)가 되고, 새 앱과 브랜치는 로드밸런서 비용이 없다.
- 단점과 위험:
  - Traefik이 워크로드 클러스터 앱 전체의 한 경로가 된다. replica 2개와 PDB로 줄이지만, Traefik이 모두 멈추면 모든 앱이 멈춘다.
  - Traefik이 없으면 새 Ingress의 status가 채워지지 않아 배포가 Argo CD 시간 제한까지 기다린다.
  - Traefik의 ClusterRole은 모든 네임스페이스의 Secret을 읽는다(Ingress TLS 때문). 우리는 TLS를 쓰지 않지만 차트에 범위를 줄이는 값이 없고, 앱 네임스페이스가 동적이라 `providers.kubernetesIngress.namespaces`로도 줄일 수 없다. 해커톤 뒤에 차트 RBAC를 끄고 Secret 권한이 없는 ClusterRole을 직접 두는 방법을 검토한다.
  - Service의 `Delete=false` 때문에 Application을 지워도 Service와 NLB가 남는다. 진입점을 정말 없앨 때는 Service를 직접 지운다([실행 순서](../runbooks/app-entry.md)).
  - NLB가 다시 만들어지면(Service 삭제 뒤 재생성 등) DNS 이름이 바뀐다. 워크로드 Terraform을 다시 적용해야 레코드가 따라간다([실행 순서](../runbooks/app-entry.md)).
  - 첫 설치 때 만든 pod에는 readiness gate가 붙지 않는다(대상 그룹이 pod보다 늦게 생긴다). 첫 롤링 재시작부터 붙는다.
  - `charlie-root`를 통째로 sync하면 이 Application에도 자동 sync가 켜진다. 지금처럼 root는 sync하지 않는다.
- 필요한 후속 작업 및 검증:
  - 앱을 옮기기 전에 끊김 감시를 켜고 Traefik 롤링 재시작을 한 번 해서 실패가 0인지 본다. Traefik을 거치는 `%2F` 경로와 docker-doom `/websockify`의 101 응답을 확인한다.
  - GKE(오늘 하지 않음): GKE에 Traefik을 operator가 helm으로 설치한다(내부 로드밸런서, 고정 내부 IP 예약, 출발지 `10.40.0.0/19`, `externalTrafficPolicy: Local`, `trustedIPs: 10.40.0.0/19`). `*.gke.hopcharlie.internal` 레코드를 더한다. gke 프로필에 Ingress를 켜고 `gatewayFrom`에 `traefik` 네임스페이스를 더한다(GKE pod 대역 `10.81.0.0/16`은 지금 규칙 밖이다). `gcp-argocd-access`의 ClusterRole과 ValidatingAdmissionPolicy에 `ingresses`를 더하고 GKE에 다시 적용한다. 권한이 바뀐 뒤 Argo CD가 GKE 클러스터의 리소스 목록을 다시 읽게 캐시를 무효화한다. 시험 앱 하나로 배포부터 게이트웨이 주소까지 확인한 뒤 플랫폼 틀에 `gke=...`를 더한다.

## 관련 자료

- [clusters/aws-dev-workload/values/traefik.yaml](../../clusters/aws-dev-workload/values/traefik.yaml), [helm/gitops/templates/applications.yaml](../../helm/gitops/templates/applications.yaml), [helm/gitops/templates/projects.yaml](../../helm/gitops/templates/projects.yaml)
- [scripts/register-traefik.py](../../scripts/register-traefik.py), [docs/runbooks/app-entry.md](../runbooks/app-entry.md)
- platform `internal/deploy/backends.go`, `internal/deploy/job.go`(`register`, `waitForBackends`), `internal/gateway/gateway.go`(`SetURL`)
- [Traefik Kubernetes Ingress provider](https://doc.traefik.io/traefik/reference/install-configuration/providers/kubernetes/kubernetes-ingress/), [AWS Load Balancer Controller Service annotations](https://kubernetes-sigs.github.io/aws-load-balancer-controller/latest/guide/service/annotations/), [pod readiness gate](https://kubernetes-sigs.github.io/aws-load-balancer-controller/latest/deploy/pod_readiness_gate/)
