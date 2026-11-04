# 0039. 앱 ApplicationSet에 GKE 환경 `gke`를 더한다

- 날짜: 2026-11-04
- 상태: 제안
- 관련 ADR: [ADR 0035](0035-app-storage-and-secrets-infra.md), [ADR 0036](0036-app-routing-and-argo-apps.md), [ADR 0037](0037-connect-aws-gateway-to-gke-apps-with-tailscale-routers.md), [ADR 0038](0038-limit-argocd-to-owned-gke-app-namespaces.md)

## 배경과 제약

[ADR 0036](0036-app-routing-and-argo-apps.md)의 앱 ApplicationSet은 `environments`에 있는 환경의 gitops 파일(`apps/<instance>/<env>.yaml`)만 Application으로 만든다. 지금은 `eks` 하나라서 플랫폼이 `gke` 파일을 커밋해도 아무것도 배포되지 않는다. 플랫폼 환경 이름은 이미 `eks`, `gke`다.

- 확인한 사실: GKE 워크로드 클러스터 `charlie-dev-gcp-workload`, StorageClass `charlie-gcp-balanced`(gcp-baseline), 게이트웨이 대역에서 GKE 앱 대역으로 가는 경로 설계([ADR 0037](0037-connect-aws-gateway-to-gke-apps-with-tailscale-routers.md)), GKE 앱 namespace용 Argo 권한 설계([ADR 0038](0038-limit-argocd-to-owned-gke-app-namespaces.md)).
- 미확인: Argo CD에 GKE 클러스터가 `charlie-dev-gcp-workload` 이름으로 실제 등록되어 있는지, 플랫폼이 GKE 앱 이미지를 Artifact Registry 주소로 쓰는지.

## 검토한 대안

- GKE 앱을 별도 ApplicationSet으로 배포: 같은 차트와 gitops 파일 규칙을 두 번 관리하게 된다.
- GKE 공개 Service를 외부 로드밸런서로 열기: 게이트웨이를 거치지 않는 공개 경로가 생긴다.
- 내부 로드밸런서에 `externalTrafficPolicy: Cluster`: GKE 노드가 출발 주소를 노드 주소로 바꿔서, 게이트웨이 대역만 허용하는 NetworkPolicy가 요청을 막는다.
- 기존 ApplicationSet에 `gke` 환경을 더하고 내부 passthrough 로드밸런서와 `Local` 정책을 쓰기(선택)

## 결정과 이유

[hopcharlie-apps.yaml](../../clusters/aws-dev-management/values/hopcharlie-apps.yaml)에 `gke` 환경을 더한다.

- 대상: Argo CD 클러스터 이름 `charlie-dev-gcp-workload`
- 공개 Service: `LoadBalancer`, 포트 80, `networking.gke.io/load-balancer-type: Internal`, `externalTrafficPolicy: Local`. 앱 차트에 `profile.publicService.externalTrafficPolicy`를 더하고, 비우면 클러스터 기본값을 쓴다(`eks`는 바뀌지 않는다).
- NetworkPolicy: 게이트웨이 대역 `10.40.0.0/19`만 받는다. Tailscale 라우터가 SNAT를 하지 않아 이 주소가 그대로 도착한다.
- DB: `charlie-gcp-balanced`, 1Gi. quota와 limitRange는 `eks`와 같다.
- 파일 저장소(`s3`): 넣지 않는다. GKE에는 플랫폼이 만드는 앱 버킷이 아직 없으므로, `s3` 리소스가 있는 앱은 렌더링에서 실패한다(버킷 없이 배포되는 것보다 먼저 멈추는 쪽을 택한다).

## 영향과 후속 작업

- 기대 효과: 플랫폼이 `gke` 파일을 커밋하면 같은 차트로 GKE에 앱이 배포되고, 게이트웨이를 통해서만 열린다.
- 단점과 위험:
  - `charlie-management-hopcharlie-apps`의 자동 sync가 켜져 있으면 병합 후 ApplicationSet이 `apps/*/gke.yaml`도 읽는다. 그런 파일이 없으면 Application은 생기지 않는다.
  - Argo의 GKE 등록, [ADR 0038](0038-limit-argocd-to-owned-gke-app-namespaces.md)의 권한, [ADR 0037](0037-connect-aws-gateway-to-gke-apps-with-tailscale-routers.md)의 라우터가 모두 준비되기 전에 `gke` 파일이 생기면 그 앱은 동기화에 실패한다.
  - 파일 저장소를 쓰는 앱은 GKE에 배포할 수 없다.
- 필요한 후속 작업 및 검증(각각 별도 승인):
  1. Argo CD에 GKE 클러스터 등록과 ADR 0038 두 단계 활성화
  2. Tailscale 라우터 apply와 tailnet 등록
  3. 시험 앱 하나로 GKE 배포, 내부 로드밸런서, 게이트웨이 주소까지 확인
  4. GKE 앱 버킷(GCS)과 플랫폼 지원은 별도 결정

## 관련 자료

- [clusters/aws-dev-management/values/hopcharlie-apps.yaml](../../clusters/aws-dev-management/values/hopcharlie-apps.yaml)
- [helm/charts/hopcharlie-app/templates/service.yaml](../../helm/charts/hopcharlie-app/templates/service.yaml)
- [scripts/check-helm.py](../../scripts/check-helm.py)
- GKE 내부 passthrough 로드밸런서: https://cloud.google.com/kubernetes-engine/docs/concepts/service-load-balancer
