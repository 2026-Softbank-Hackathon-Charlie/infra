# 0036. GitHub 앱을 앱 ApplicationSet, 와일드카드 DNS와 ALB 규칙, 워크로드 kubeconfig로 연다

- 날짜: 2026-11-04
- 상태: 제안
- 2026-11-07 갱신: 손으로 배포했던 GKE moimroom을 지웠다. 지금 moimroom은 EKS(`moimroom/moimroom-db`)에서만 돈다. apps.yaml에서 moimroom의 `gke` 환경(`moimroom-db-gke`)을 뺐다.
- 관련 ADR: [ADR 0002](0002-require-approval-for-privileged-operations.md), [ADR 0016](0016-run-hopcharlie-platform-on-management-cluster.md), [ADR 0017](0017-adopt-existing-helm-resources-in-argocd.md), [ADR 0021](0021-configure-platform-github-app-deploy.md), [ADR 0023](0023-deploy-github-apps-with-applicationset.md), platform 저장소 ADR 0011(GitHub 앱 배포), ADR 0012(게이트웨이 호스트 라우팅), ADR 0015(브랜치 인스턴스)

## 배경과 제약

[ADR 0023](0023-deploy-github-apps-with-applicationset.md)의 경로는 이렇다. 플랫폼 API가 gitops 저장소에 `apps/<instance>/<env>.yaml`을 커밋하고, ApplicationSet이 파일마다 Application을 만들어 워크로드 EKS의 `<instance>` 네임스페이스에 앱을 배포한다. 공개 서비스는 내부 NLB를 받고, 플랫폼이 kubeconfig로 그 Service의 NLB 주소를 읽어 게이트웨이에 등록하면, 관리 클러스터의 게이트웨이가 `https://<instance>.hopcharlie.dev`를 그 NLB로 보낸다. 이 경로에서 운영에 빠진 것이 네 가지다. 아래 사실은 저장소와 문서에서 확인했고, 실제 클러스터와 AWS는 이번 작업에서 조회하지 않았다.

1. ApplicationSet이 없다. root `charlie-root`는 자동 sync 없이 손으로 등록했고([계획](../plans/argocd-root-bootstrap.md)), 그때 만든 하위 Application 13개와 AppProject 2개에는 `charlie-management-hopcharlie-apps`와 `charlie-hopcharlie-apps`가 없다(PR #16이 그 뒤에 추가했다). root를 sync하면 Git의 `automated`가 모든 하위 앱에 다시 켜져 워크로드 애드온 배포까지 시작되므로 root는 sync하지 않는다. 플랫폼 앱 하나만 따로 넘겨받아 자동 sync를 켠 전례가 있다(PR #26). Argo CD에는 infra 저장소의 repository Secret(`charlie-infra-repository`, 읽기 전용 PAT)만 있고 gitops 저장소의 자격 증명은 없다([계획](../plans/argocd-workload-target.md)).
2. 와일드카드 DNS가 없다. [dns/main.tf](../../terraform/platform/aws/dns/main.tf)는 `api`, `app`, `console`만 만든다. 공유 ALB의 규칙도 이 세 호스트와 Headlamp(group.order 19, 20)뿐이고, 나머지 호스트는 baseline anchor(group.order 1000)의 고정 404를 받는다. `*.hopcharlie.dev` ACM 인증서는 이미 ALB에 있다.
3. 플랫폼이 워크로드를 읽을 kubeconfig가 없다. API의 init 컨테이너는 SSM `/hopcharlie/kube/*`를 `/config/kube`에 받아 `KUBECONFIG`로 넘기고, [envs.yaml](../../terraform/platform/aws/host/envs.yaml)은 환경 `eks`의 컨텍스트를 `eks-hopcharlie`로 둔다. 플랫폼 코드는 이 컨텍스트 하나를 세 곳에 쓴다. 배포 실행기의 Service 조회(`internal/deploy/backends.go`), 환경 보기의 서버 버전, 노드, CNPG Cluster 목록(`internal/envs/prober.go`), 보안 보기의 pod, Service, NetworkPolicy, ClusterRoleBinding 목록(`internal/security/kubernetes.go`)이다. 이전 엔진도 같은 컨텍스트로 앱 네임스페이스의 CNPG Cluster를 바꾼다. 워크로드 EKS API는 VPC `10.40.0.0/16` 안의 private endpoint뿐이고, API 이미지에는 aws CLI나 인증 플러그인이 없다.
4. 게이트웨이 pod에서 워크로드 내부 NLB 80 포트까지의 경로를 확인한 기록이 없다.

이 ADR은 네 가지를 하나의 결정으로 기록한다. 넷 다 같은 요청 경로의 구간이고 하나라도 빠지면 앱 주소가 열리지 않으며, 한 PR과 한 실행 순서로 함께 적용하기 때문이다. 각 구간의 대안은 아래에 따로 적는다.

## 검토한 대안

Argo CD 등록

- root를 sync: 한 번에 끝나지만 13개 하위 앱의 자동 sync가 돌아와 승인받지 않은 워크로드 애드온 배포가 시작된다.
- 앱 ApplicationSet용 root를 따로 만들기: root 둘이 같은 객체를 그리게 되고 손으로 적용할 매니페스트가 늘어난다.
- root가 렌더링하는 두 객체만 그대로 create하고 그 Application만 sync(선택): 이전의 수동 등록, PR #26의 플랫폼 인수와 같은 방식이다. root를 나중에 sync해도 차이는 `automated` 하나다.

gitops 저장소 자격 증명

- 플랫폼 GitHub App(`hopcharlie-platform`) 재사용: 이미 있지만 gitops에 쓰는 권한이 있어서 Argo CD에 쓰기 자격 증명이 생긴다.
- 읽기 전용 deploy key: 사람과 만료에 묶이지 않지만 SSH URL만 쓸 수 있다. ApplicationSet과 앱 AppProject의 `gitops.repoURL`을 바꿔야 하고, 같은 AppProject를 고치는 저장소 PR과 겹친다.
- 읽기 전용 GitHub App 새로 만들기: HTTPS URL 그대로 쓰고 만료도 없지만 조직 owner가 App을 만들고 설치해야 한다.
- gitops 하나에 Contents Read-only만 준 fine-grained PAT(선택): infra 저장소와 같은 방식이라 이미 확인한 절차를 그대로 쓰고 차트를 고치지 않는다. 만든 사람에게 묶이고 만료가 있다.

와일드카드 라우팅

- 플랫폼이 앱마다 Cloudflare 레코드와 ALB 규칙을 만들기: 플랫폼에 Cloudflare 토큰과 Ingress 권한이 필요하고 앱마다 infra 밖의 상태가 생긴다.
- ALB 기본 동작을 게이트웨이로 바꾸기: anchor는 baseline이 소유하고, 도메인 밖 호스트까지 게이트웨이로 간다.
- 기존 플랫폼 Ingress에 `*.hopcharlie.dev` 규칙을 넷째로 넣기: 짧지만 AWS Load Balancer Controller 문서는 Ingress 안의 규칙 순서를 정하지 않는다.
- group.order 900의 별도 Ingress(선택): ALB 규칙 순서를 문서에 있는 group.order로 정한다. Cloudflare는 와일드카드 CNAME 하나다. 명시한 레코드가 와일드카드보다 우선한다.

kubeconfig 인증과 권한

- IAM 인증(aws CLI 또는 aws-iam-authenticator exec): pod에 도구가 없고 플랫폼 코드는 exec 플러그인을 쓰지 않는다.
- TokenRequest의 짧은 토큰: API는 시작할 때만 kubeconfig를 읽으므로 만료되면 다시 넣고 재시작해야 한다.
- platform `deploy/k8s/hopcharlie-platform.yaml` 그대로: 코드가 쓰지 않는 namespace 조회, watch가 있고, 이전용 `hopcharlie-migrator`는 앱 네임스페이스에서 Secret, Service, Deployment, CNPG Cluster를 바꾼다.
- Service 조회만: 가장 좁지만 같은 컨텍스트를 쓰는 환경 보기와 보안 보기가 `eks`에서 실패한다.
- 장기 ServiceAccount 토큰과 코드 경로에 맞춘 읽기 전용 ClusterRole(선택)

## 결정과 이유

1. Argo CD: [scripts/register-hopcharlie-apps.py](../../scripts/register-hopcharlie-apps.py)가 `charlie-root`의 지금 values로 `helm/gitops`를 렌더링해 AppProject `charlie-hopcharlie-apps`와 Application `charlie-management-hopcharlie-apps`만 꺼낸다. Application의 `automated`는 지우고 create만 한다(이미 있으면 멈춘다). `preview`가 서버 dry-run과 sync 결과물의 `kubectl diff`를 먼저 보여 주고, `sync`가 그 Application에만 sync operation 하나를 요청한다. 결과를 확인한 뒤 root 템플릿과 같은 `automated: {prune: false, selfHeal: true}`를 사람이 켠다. AppProject와 차트는 고치지 않는다.
2. 자격 증명: GitHub fine-grained PAT(리소스 owner는 조직, 저장소는 `gitops` 하나, 권한은 Contents Read-only, 만료 2026-11-30)를 Secret `argocd/charlie-gitops-repository`(`type: git`, HTTPS URL, `username: x-access-token`)로 넣는다. 스크립트는 토큰을 파일에서 읽어 kubectl stdin으로만 넘기고 출력하지 않으며, 같은 이름의 Secret을 덮어쓰지 않는다.
3. DNS와 ALB: [dns/main.tf](../../terraform/platform/aws/dns/main.tf)에 `*.hopcharlie.dev` CNAME(공유 ALB, proxied false, TTL 300)을 기존 세 레코드와 같은 방식으로 더한다. 플랫폼 차트([network.yaml](../../helm/charts/hopcharlie-platform/templates/network.yaml))는 `ingress.enabled`이고 `appDomain`이 있을 때 Ingress `hopcharlie-apps`(`*.<appDomain>`, `hopcharlie-gateway:8000`, group.order 900)를 그린다. 인증서가 `*.<ingress.domain>`만 덮으므로 `appDomain`이 `ingress.domain`과 다르면 렌더링을 실패시킨다. AWS Load Balancer Controller는 group.order가 작은 Ingress의 규칙을 먼저 평가하므로 `api`, `app`, `console`(10)과 Headlamp(19, 20)가 와일드카드보다 앞선다. 같은 그룹에 명시 호스트를 더하는 Ingress는 900보다 작은 order를 쓴다. `app.hopcharlie.dev`는 그대로 플랫폼 Ingress가 게이트웨이의 기본 앱으로 보낸다.
4. kubeconfig: 환경 `eks`는 워크로드 클러스터 `charlie-dev-workload`를 가리킨다. 앱 ApplicationSet이 `eks` 파일을 그 클러스터로 배포하고, 배포 실행기가 Service를 찾는 곳, 환경 보기가 보여 줄 노드와 CNPG, apps.yaml의 `eks` DB가 모두 거기 있기 때문이다. 관리 클러스터는 앱의 대상 환경이 아니다. [scripts/put-platform-kubeconfig.py](../../scripts/put-platform-kubeconfig.py)가 [hopcharlie-platform-access.yaml](../../clusters/aws-dev-workload/hopcharlie-platform-access.yaml)을 적용하고, 토큰, EKS CA, private endpoint로 컨텍스트 `eks-hopcharlie`의 kubeconfig를 만들어 SSM `/hopcharlie/kube/eks`(SecureString)에 넣는다. 다른 AWS 계정이면 `tf.sh`처럼 시작 전에 멈추고, 저장 전에 터널로 그 토큰의 허용과 거부를 `kubectl auth can-i`로 확인한다.
   - 권한(ClusterRole `hopcharlie-platform-observer`): Service의 get, list(배포 실행기의 NLB 주소 조회, 보안 보기), node의 list(환경 보기), `postgresql.cnpg.io` Cluster의 list(환경 보기), pod, NetworkPolicy, ClusterRoleBinding의 list(보안 보기). 코드가 get으로 읽는 것은 Service뿐이다. 서버 버전은 규칙이 필요 없다. Secret, ConfigMap 조회와 모든 변경 권한은 없다.
   - 이전(migration)은 `hopcharlie-migrator`를 앱 네임스페이스마다 바인딩해야 하지만 지금 주지 않는다. apps.yaml의 `eks` DB 주소는 Tailscale(`*.eks.ts.net`)인데 관리 EKS의 API pod는 tailnet에 없다. Cluster 이름은 앱 차트(`<app>-<resource>`, 예 `moimroom-db`)와 apps.yaml이 달랐는데, 2026-11-05에 apps.yaml의 `eks`를 실제 이름 `moimroom-db`로 맞췄다(이전 원본으로 쓰는 DB). `gke`의 `moimroom-db-gke`는 이전 때 엔진이 새로 만드는 대상 이름이라 그대로 둔다. 앱 네임스페이스는 Argo CD가 Application과 함께 만들고 지우므로 RoleBinding도 그때마다 다시 만들어야 한다.
   - ServiceAccount는 `charlie-system`에 둔다. 앱 ApplicationSet이 이 네임스페이스를 막고 있어서(`deniedNamespaces`) 같은 이름의 앱이 배포되어 토큰 Secret을 마운트할 수 없다. platform 문서의 `hopcharlie-system`은 막혀 있지 않다. ClusterRole과 ClusterRoleBinding 이름도 platform의 `hopcharlie-observer`와 다르게 두어, 누가 이 클러스터에서 platform의 `make-kubeconfig.sh`를 실행해도 이 바인딩의 subject가 바뀌지 않는다.
5. ApplicationSet 삭제 보호([ADR 0023](0023-deploy-github-apps-with-applicationset.md)의 동작 보완): ApplicationSet이 만든 Application은 ApplicationSet의 ownerReference와 템플릿이 명시한 `resources-finalizer.argocd.argoproj.io`를 가진다. ApplicationSet이 지워지면 모든 앱의 네임스페이스와 DB가 함께 지워진다. 차트는 ApplicationSet에 `argocd.argoproj.io/sync-options: Delete=false`를 붙여, `charlie-management-hopcharlie-apps`나 root가 cascade로 지워져도 Argo CD가 ApplicationSet을 지우지 않게 한다. `spec.syncPolicy.preserveResourcesOnDeletion`은 쓰지 않는다. Argo CD v3.5.3(`applicationset/utils/utils.go`)은 이 값을 템플릿에 finalizer가 없을 때 finalizer를 더하지 않는 데에만 쓰므로, finalizer를 명시한 이 템플릿에서는 효과가 없다. 템플릿에서 finalizer를 빼면 gitops 파일을 지울 때도 리소스가 남아, 플랫폼의 앱 삭제가 네임스페이스와 DB를 지운다는 ADR 0023과 platform ADR 0011의 동작이 바뀐다. ApplicationSet을 직접 지워야 할 때는 `--cascade=orphan`만 쓴다([실행 순서](../runbooks/app-routing.md) 5단계).
6. 네트워크: 변경하지 않는다. 다음 근거로 게이트웨이 pod에서 내부 NLB 80까지 열려 있다고 판단한다.
   - 게이트웨이 pod의 egress: 플랫폼 차트의 NetworkPolicy `hopcharlie-egress`가 api, gateway pod에 TCP 80, 5432를 목적지 제한 없이 연다(`egressPorts`). baseline `application-boundary`가 DNS를 연다.
   - 관리 노드 보안 그룹: 모듈의 node SG가 모든 egress를 연다([eks 모듈](../../terraform/modules/eks/main.tf)). pod는 VPC CNI로 노드 ENI의 보안 그룹을 쓴다.
   - NLB: 프로필([hopcharlie-apps.yaml](../../clusters/aws-dev-management/values/hopcharlie-apps.yaml))은 `external`, `internal`, 대상 `ip`, 리스너 80이다. LBC는 보안 그룹 annotation이 없으면 frontend 보안 그룹을 만들고, source range가 없으면 scheme과 관계없이 `0.0.0.0/0`에서 리스너 포트를 연다(LBC v3.5.0). 그래서 같은 VPC의 관리 pod 주소(`10.40.0.0/19`)가 허용된다. 내부 NLB라 주소는 VPC 안에서만 닿는다. 실제 규칙은 첫 NLB가 생긴 뒤 확인한다. 서브넷은 `kubernetes.io/role/internal-elb`와 워크로드 클러스터 태그가 있는 워크로드 private 서브넷(`10.40.32.0/20`, `10.40.48.0/20`)이 골라진다([network.tf](../../terraform/environments/aws/dev/foundation/network.tf)). 관리 서브넷은 다른 클러스터 태그가 있어 빠진다.
   - NLB에서 pod로: LBC가 대상 ENI의 보안 그룹 중 `kubernetes.io/cluster/charlie-dev-workload` 태그가 있는 EKS 클러스터 보안 그룹에 NLB 보안 그룹에서 대상 포트로 들어오는 규칙을 더한다. 워크로드 노드의 다른 보안 그룹(모듈 node SG)에는 그 태그가 없어 고르는 대상이 하나다.
   - 앱 네임스페이스 NetworkPolicy: `gatewayFrom`이 `10.40.0.0/16`을 허용한다. ip 대상은 클라이언트 IP 보존이 기본으로 꺼져 있어 pod가 보는 출발지는 워크로드 서브넷의 NLB 주소이고, 헬스 체크도 같다. 둘 다 이 범위 안이다.
   - 같은 VPC의 local route이고, 관리 pod가 워크로드 EKS API(443)에 닿는 경로는 이미 Argo CD가 쓰고 있다(`management_api_source`, `workload_api_target` 보안 그룹).

## 영향과 후속 작업

- 기대 효과: 플랫폼이 gitops에 커밋하면 앱이 워크로드에 배포되고, 배포 실행기가 NLB 주소를 게이트웨이에 등록해 `https://<instance>.hopcharlie.dev`가 열린다. 두 번째 앱부터 infra 변경이 없다.
- 단점과 위험:
  - 플랫폼 앱은 자동 sync라서 이 PR을 merge하면 Ingress `hopcharlie-apps`가 바로 생기고 ALB 규칙이 하나 늘어난다. merge 자체가 배포이므로 승인이 필요하다. `prune: false`라서 커밋을 되돌려도 Ingress는 남는다.
  - LBC가 group.order를 문서와 다르게 다루면 와일드카드가 `api`, `console`을 가릴 수 있다. merge 직후 ALB 규칙 우선순위와 세 주소의 응답을 확인한다([실행 순서](../runbooks/app-routing.md) 1단계).
  - 와일드카드 DNS 뒤로는 없는 이름도 ALB에 닿고 게이트웨이가 404 `unknown_app`으로 답한다. 두 단계 이름(`a.b.hopcharlie.dev`)은 인증서가 덮지 않아 TLS에서 실패한다.
  - ApplicationSet sync 직후 gitops에 남아 있는 모든 `apps/*/eks.yaml`이 자동 sync, prune으로 배포된다. 파일을 지우면 네임스페이스와 DB가 함께 지워진다(ADR 0023).
  - `Delete=false`는 상위 Application의 cascade 삭제만 막는다. `kubectl delete applicationset hopcharlie-apps`를 orphan 없이 실행하면 여전히 모든 앱의 네임스페이스와 DB가 지워진다.
  - 장기 토큰이 SSM에 있다. 클러스터 전체의 pod spec(환경 변수 포함)과 Service를 읽을 수 있다. 끊으려면 토큰 Secret을 지운다.
  - PAT는 만든 사람에게 묶이고 2026-11-30에 만료된다. 만료되면 ApplicationSet이 새 파일을 읽지 못한다.
  - ALB는 `headlamp.hopcharlie.dev`를 Headlamp로 먼저 보낸다. 플랫폼의 예약 이름은 `api`, `app`, `console`뿐이라 `headlamp`라는 앱을 등록하면 그 주소에 닿지 않는다.
  - NLB cross-zone이 기본으로 꺼져 있어 replica 1개인 앱은 한 AZ에만 대상이 있다. 대상이 없는 AZ의 NLB 주소는 헬스 체크 뒤 DNS에서 빠지지만, 처음 생길 때와 pod가 AZ를 옮길 때 잠깐 실패할 수 있다.
- 필요한 후속 작업 및 검증:
  - [실행 순서](../runbooks/app-routing.md)대로 단계마다 승인받아 실행하고 결과를 그 문서의 실행 기록에 남긴다. 실행 전에는 이 ADR을 채택으로 바꾸지 않는다.
  - 워크로드 LBC가 실제로 떠 있는지 확인한다. 2026-11-03 기록에는 `Missing`이었다.
  - S3 리소스를 쓰는 앱(moimroom의 `photos`)은 `app-s3` Secret이 있어야 Ready가 된다. 별도 PR(feat/app-storage-infra)이 맡는다.
  - 필요하면 프로필에 `service.beta.kubernetes.io/aws-load-balancer-attributes: load_balancing.cross_zone.enabled=true`와 관리 서브넷만 여는 source range를 더한다. 같은 프로필을 고치는 저장소 PR과 겹치지 않도록 그 PR 뒤에 한다.
  - `eks` 이전을 쓰려면 DB 경로(Tailscale 또는 다른 경로), apps.yaml의 Cluster 이름, `hopcharlie-migrator` 바인딩을 platform 팀과 함께 정한다.
  - platform의 예약 앱 이름에 `headlamp`를 더할지 platform 팀과 정한다.
  - 해커톤 뒤에는 PAT와 토큰 Secret을 지우고 SSM `/hopcharlie/kube/eks`를 지운다.

## 관련 자료

- [docs/runbooks/app-routing.md](../runbooks/app-routing.md): 실행 순서와 명령
- [scripts/register-hopcharlie-apps.py](../../scripts/register-hopcharlie-apps.py), [scripts/put-platform-kubeconfig.py](../../scripts/put-platform-kubeconfig.py), [scripts/tests/test-app-routing.py](../../scripts/tests/test-app-routing.py)
- [clusters/aws-dev-workload/hopcharlie-platform-access.yaml](../../clusters/aws-dev-workload/hopcharlie-platform-access.yaml)
- [helm/charts/hopcharlie-platform/templates/network.yaml](../../helm/charts/hopcharlie-platform/templates/network.yaml), [terraform/platform/aws/dns/main.tf](../../terraform/platform/aws/dns/main.tf)
- [helm/gitops/templates/applications.yaml](../../helm/gitops/templates/applications.yaml), [helm/gitops/templates/projects.yaml](../../helm/gitops/templates/projects.yaml), [helm/charts/hopcharlie-apps/values.yaml](../../helm/charts/hopcharlie-apps/values.yaml)
- platform 저장소 `deploy/k8s/hopcharlie-platform.yaml`, `internal/deploy/backends.go`, `internal/envs/prober.go`, `internal/security/kubernetes.go`
- [AWS Load Balancer Controller Ingress annotations(group.order)](https://kubernetes-sigs.github.io/aws-load-balancer-controller/latest/guide/ingress/annotations/), [Service annotations(보안 그룹, source range, 클라이언트 IP 보존)](https://kubernetes-sigs.github.io/aws-load-balancer-controller/latest/guide/service/annotations/)
