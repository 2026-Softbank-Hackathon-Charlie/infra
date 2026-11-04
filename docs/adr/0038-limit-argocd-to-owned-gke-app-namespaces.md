# 0038. Argo의 GKE 동적 앱 권한을 소유 namespace로 제한

- 날짜: 2026-11-04
- 상태: 제안
- 관련 ADR: [0021](0021-keyless-argocd-gke-access.md), [0035](0035-app-storage-and-secrets-infra.md), [0036](0036-app-routing-and-argo-apps.md), [0037](0037-connect-aws-gateway-to-gke-apps-with-tailscale-routers.md)

## 배경과 제약

기존 차트·ApplicationSet을 재사용해 AWS와 GKE에 앱을 배포한다. 환경 키는 기존 플랫폼 계약대로 `eks`·`gke`이며 인스턴스 이름을 namespace로 쓴다. GKE의 기존 고정 `charlie-apps` 접근만으로는 동적 namespace를 관리할 수 없다. 기존 DB·운영 도구와 Pod 보안 수준은 보존한다.

## 검토한 대안

- 앱마다 RoleBinding과 cache 목록을 infra PR로 추가: 동적 앱 계약에 맞지 않는다.
- cluster-admin 부여: 필요한 범위를 초과하므로 제외한다.
- 제한된 종류의 RBAC와 소유권 admission: 기존 인증을 유지하며 선택한다.

## 결정과 이유

기존 Google WIF 인증을 유지한다. 선택적으로 활성화하는 동적 앱 모드에 Namespace와 차트가 쓰는 Deployment·Service·CNPG Cluster·ResourceQuota·LimitRange·NetworkPolicy·SealedSecret 권한만 추가한다. SealedSecret은 [ADR 0035](0035-app-storage-and-secrets-infra.md)에서 앱 차트에 더해진 암호문이며, 복호화한 Secret은 sealed-secrets 컨트롤러가 만든다. Argo의 Secret·Role·Binding·CRD·StorageClass 쓰기는 추가하지 않는다.

해당 GSA의 쓰기만 ValidatingAdmissionPolicy로 검사한다. Namespace 생성은 앱/인스턴스 소유 관계·이름 제한·restricted Pod Security를 요구하고, 기존 namespace 인수·소유권 변경·보안 완화를 거부한다. namespace 안의 쓰기도 소유권을 요구한다. 보호 namespace는 거부하며 오류는 Fail/Deny로 처리한다. `namespaceObject` 검사는 validations에서 수행한다.

활성화는 두 단계다. 먼저 `enabled=true, grantAccess=false`로 Role·정책·정책 Binding 3개를 설치한다. 서버 type-check와 기존 `charlie-apps` edit가 허용하던 요청의 정책 거부를 확인한 뒤, `grantAccess=true`로 권한 Binding 1개를 추가하고 cache를 확대한다. 회수는 권한 Binding 제거·확인 후 정책을 해제하는 역순이다.

고정 cache 범위를 해제하고 Argo의 `resource.respectRBAC=normal`을 사용한다. GKE만 resource.inclusions에 넣어 기존 AWS를 제외하지 않는다. AppProject 제한도 유지한다.

## 영향과 후속 작업

- 동적 모드 활성화 시 기존 GSA의 `charlie-apps` edit Binding이 남아 있어도 대상 종류의 쓰기는 거부된다. 해당 namespace를 쓰는 기존 Application이 있으면 적용 전에 멈춰 검토한다.
- 실제 VAP type-check·소유 namespace 허용·보호/무소유 namespace 거부와 양쪽 앱 생성·revision·삭제 검증이 필요하다. 구현·렌더 검사는 실제 적용 완료가 아니다.
- GKE gateway 경로는 연결 준비 전 차단을 유지한다. GKE S3 공급과 실제 앱 전체 인수는 별도 태스크다.

## 관련 자료

- [접근 차트](../../helm/charts/gcp-argocd-access), [앱 환경](../../clusters/aws-dev-management/values/hopcharlie-apps.yaml)
- [Argo RBAC 존중 설정](https://argo-cd.readthedocs.io/en/stable/operator-manual/declarative-setup/#auto-respect-rbac-for-controller)
- [Kubernetes 1.36 VAP](https://v1-36.docs.kubernetes.io/docs/reference/access-authn-authz/validating-admission-policy/)
