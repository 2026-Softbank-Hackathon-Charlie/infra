# 0021. Argo CD의 GKE 접근에 EKS ServiceAccount와 Google WIF 사용

- 날짜: 2026-11-03
- 상태: 채택
- 관련 ADR: [0002](0002-require-approval-for-privileged-operations.md), [0017](0017-team-headlamp-with-cognito.md)

## 배경과 제약

기존 GKE와 AWS 관리 Argo CD를 연결하려면 Kubernetes API 네트워크 경로 외에 Google 인증과 GKE RBAC가 필요하다. Headlamp 개인 토큰이나 GitHub workflow 인증을 장기 controller 인증으로 재사용하면 수동 갱신 또는 신뢰 범위 확장이 생긴다. 승인된 실제 적용 후 두 Argo Pod의 Google WIF 인증과 GKE API 접근, 허용·거부 권한을 확인했다.

## 검토한 대안

- 개인 access token: 빠르지만 짧은 만료·개인 권한에 의존해 상시 배포 인증에서 제외한다.
- Google SA JSON key: 장기 비밀값 배포·회전이 필요해 제외한다.
- 기존 GitHub WIF provider 확장: 서로 다른 실행 주체의 신뢰 경계를 합치므로 제외한다.
- AWS credential federation: 가능하지만 EKS Pod Identity/IRSA credential 획득 경로가 추가된다.
- EKS KSA projected JWT와 별도 Google OIDC WIF: 두 Argo KSA subject와 전용 audience에만 신뢰를 한정하며 키 없이 갱신할 수 있어 채택한다.

## 결정과 이유

별도 GCS prefix의 Terraform root로 WIF pool/provider·전용 Google SA·container.clusterViewer와 impersonation binding을 관리한다. 관리 EKS의 기존 issuer와 argocd namespace의 application-controller/server KSA만 허용한다. 두 Pod에 public ADC ConfigMap과 1시간 projected JWT volume을 추가한다. Google 단기 access token은 argocd-k8s-auth gcp가 자동으로 얻는다.

GKE에는 전용 SA의 view ClusterRoleBinding과 승인된 앱 namespace의 edit RoleBinding만 준비한다. Argo target은 해당 namespace만 cache하고 clusterResources=false로 등록한다. cluster-admin·CNPG CRD 설치 권한은 부여하지 않는다. charlie-apps는 설치된 gcp-baseline release가 단독 소유한다. service2의 GCP baseline과 CNPG operator 설치는 완료된 상태이며 이 ADR의 Argo 인증 적용과 구분한다. access 차트는 namespace를 생성하거나 baseline의 소유권을 바꾸지 않는다.

## 영향과 후속 작업

- 사용자 승인 범위에서 별도 Google IAM/신뢰·Argo 전용 GKE RBAC·Argo 설정을 실제 적용했다. Headlamp 개인 인증과 Argo 상시 인증은 별도 경계로 유지한다. 이후 권한·대상 변경은 별도 승인이 필요하다.
- namespace edit는 Secret 접근/변경을 포함한다. 개인 Headlamp 조회 전용 권한과 다르다.
- offline generator에는 현재 live controller/server의 이름 있는 env/volume/mount와 KSA 이름을 메모리에서 읽어 전달한다. 전체 live Helm values를 파일에 보관하지 않는다. 생성 overlay는 기존 배열을 보존하고 충돌을 거부한다. 같은 pinned release와 --reuse-values를 사용하되 실제 dry-run/차이를 검토하고 기존 AWS Pod Identity·서비스 계정·다른 Helm 값을 유지한다.
- 실제 token 교환·GKE API 접근·AWS 회귀·RBAC 허용/거부와 임시 권한 회수는 확인했다. 장시간 token 갱신 및 실제 앱 동기화·target cache 검증은 앱 배포 후 남은 검증이며, 이번 접근 연동 완료와 구분한다.
- 공통 baseline과 CNPG operator는 service2에서 설치했다. 앱/DB 배포·데이터 통신·실제 이전 검증은 별도 후속 태스크다.

## 실제 적용과 검증

- 원격 GCS `gcp/dev/argocd` state로 Terraform 리소스 6개를 생성했다. 변경·삭제는 각각 0개이며 Google SA key는 사용하지 않았다.
- GKE의 `gcp-argocd-access` Helm revision 1에 전용 Google SA의 cluster view와 `charlie-apps` namespace edit를 적용했다. 관리 EKS의 `argocd` Helm revision 2에서 controller/server 각각 Ready 1개와 projected JWT·public ADC 구성을 확인했다.
- 두 Argo Pod에서 실제 WIF token 교환 후 GKE API HTTP 200을 확인했고 RBAC 허용·거부 검사 12개를 통과했다. 기존 AWS management/workload의 Argo 연결은 모두 Successful이다.
- GKE는 Argo에 등록했으며 현재 앱 0개로 모니터링되지 않아 연결 표시는 Unknown이다. 앱 배포 성공이나 실제 이전 완료를 의미하지 않는다. 기존 RBAC 객체 15개·CRD·bootstrap Secret은 유지했고 GCP baseline/CNPG operator도 revision 1을 유지했다.
- 작업용 임시 EKS admin-view·namespace-edit 접근 정책 2개와 access entry를 제거했고 SSM 터널 2개를 닫았다. Headlamp의 Google 개인 인증 API HTTP 200과 기존 AWS 두 클러스터의 브라우저 조회 정상도 확인했다. 아직 Git commit·push·PR 출판은 하지 않았다.

## 관련 자료

- [운영 절차](../gcp-operational-access.md)
- [Google 공식 Kubernetes federation](https://docs.cloud.google.com/iam/docs/workload-identity-federation-with-kubernetes)
- [고정 Argo CD GCP 인증](https://github.com/argoproj/argo-cd/blob/v3.5.3/cmd/argocd-k8s-auth/commands/gcp.go)
