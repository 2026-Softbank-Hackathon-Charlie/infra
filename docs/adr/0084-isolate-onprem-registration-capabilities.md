# 0084. 온프레미스 등록 기능은 전용 API 계정과 소유권 차단 정책으로 격리한다

- 날짜: 2026-11-07
- 상태: 제안 — 코드 준비 중이며 운영 미적용
- 관련 ADR: [0064](0064-platform-creates-management-app-db-routes.md), [0082](0082-onboard-existing-compose-apps-infra.md)

## 배경과 제약

자동 등록은 노트북에 발급한 자격 증명으로 실제 Kubernetes API를 확인한 뒤 관리 클러스터에 API 경로, Argo cluster Secret, AppProject와 ApplicationSet을 만든다. 기존 API·게이트웨이·Postgres는 같은 ServiceAccount와 `/hopcharlie/*` 조회 역할을 공유한다. 이 계정에 새 기능을 그대로 추가하면 다른 Pod도 등록 OAuth를 읽게 된다.

RBAC는 생성할 자원의 이름 접두사나 소유권 라벨을 제한하지 못한다. Secret의 GET도 접두사로 제한할 수 없으므로 기존 Argo 클러스터 인증정보를 읽는 권한은 부여하지 않는다. 기존 deployer와 DB route admission 정책은 새 자원 요청과 충돌하므로 새 전용 API 계정 요청에 한해서만 등록 정책에 위임한다.

## 검토한 대안

- 공용 계정과 기존 onboarding OAuth 재사용: 변경량은 작지만 기존 클러스터 인증정보와 장비 태그의 권한 경계가 섞여 제외한다.
- 등록 전용 별도 서버: 계정을 더 분리할 수 있지만 API 상태·등록 트랜잭션 분산이 필요하다. 현재는 기존 기능을 보존하는 API 전용 계정 분리를 선택한다.
- API에 Secret GET 허용: 구현은 간단하지만 argocd의 기존 클러스터 credential도 읽게 되어 제외한다. 생성·갱신·삭제는 admission이 기존 소유권까지 검사한다.

## 결정과 이유

기본값은 꺼짐이다. 활성화할 때 API만 `hopcharlie-platform-api` 계정을 사용한다. 게이트웨이와 Postgres의 기존 계정은 유지한다. 새 Pod Identity 역할은 API의 기존 설정·보안·스토리지·빌드·compose onboarding 정책을 보존한다. 새 OAuth는 `/hopcharlie/onprem-registration-oauth`에 별도로 준비하며 Terraform은 값을 발급하거나 읽지 않는다.

기존 역할은 새 파라미터의 모든 `GetParameter*`를 명시적으로 거부한다. 개별 파라미터의 Deny만으로는 부모 경로의 재귀 조회를 막지 못하므로 `GetParametersByPath`는 기존 fetch가 사용하는 kube와 sealed-secrets 하위 경로 외 모두 거부한다. 등록 OAuth와 compose OAuth는 코드의 서로 다른 환경변수와 별도 태그로 사용하며 fallback하지 않는다.

API의 신규 권한은 다음과 같다.

- argocd: Secret 생성·갱신·삭제만, AppProject/ApplicationSet 조회·생성·갱신·삭제. 등록 ID와 UUID가 일치하는 자원만 허용하며 기존 소유권의 변경은 거부한다.
- hopcharlie-db-network: `hop-k3s-<등록 ID의 24 hex>` API 경로. 전용 ProxyClass, `tag:hop-onprem-api-client`, `hop-k3s-*.tail567fe8.ts.net`, TCP 6443만 허용한다. operator가 관리하는 externalName·finalizers는 갱신 시 보존한다. 기존 DB Service UPDATE는 계속 거부한다.
- Argo source·generator·destination·동기화 정책·profile은 서버 설정과 정확히 일치해야 한다. profile은 restricted를 강제하며 시스템 namespace, RBAC, Secret의 앱 동기화를 허용하지 않는다.

Argo CRD의 `helm.valuesObject`는 opaque 필드여서 CEL이 내부 profile을 정확히 검증하기 어렵다. `valueFiles`보다 우선하는 `helm.values`에 Go JSON encoder로 만든 `{"profile": ...}` 문자열을 넣는다. JSON은 Helm values의 유효한 YAML 표현이며, VAP는 신뢰한 profile과 소유자 ID·UID를 포함한 문자열을 정확히 비교한다. 추가 `valuesObject` override는 허용하지 않는다. 공식 Argo CRD와 실제 API로 이 표현을 검증하며 타입 경고도 실패로 처리한다.

새 장비 profile은 기존 onprem profile에서 출발하되 기존 노트북 전용 DB·S3·복제 경로는 복사하지 않는다. 연결 확인은 Kubernetes와 Hello World까지만 뜻한다. DB·스토리지·Sealed Secrets 기능은 별도 준비와 실제 검증 전까지 사용 가능하다고 표시하지 않는다.

등록 profile은 내부 ClusterIP를 사용하고 기존 노트북의 Tailscale 웹 ProxyClass·태그·MinIO 송신 허용을 복사하지 않는다. AppSet은 해당 등록 ID와 클러스터 UID를 profile에 주입하고 앱 차트가 Namespace 라벨로 전달한다. 원격 admission이 자기 소유 Namespace만 허용하는 platform 설치 계약과 맞춘다. 기존 앱 Pod의 `automountServiceAccountToken: false`도 유지한다.

Argo credential 갱신은 양수 registration-version과 UTC token-expires-at을 검사한다. 새 버전은 이전 버전보다 커야 하고 같은 버전은 같은 config를 재게시할 때만 허용한다. 만료가 줄거나 endpoint·CA가 바뀌면 거부한다. 만료일과 version의 잘못된 형식도 거부하며 실제 API 시험으로 검증한다.

## 영향과 후속 작업

- 운영 적용은 승인 후 Terraform 계획 검토, 전용 OAuth와 Tailnet ACL 준비, 기존 admission 정책 갱신, 관리 AppProject 허용 범위 준비, 마지막으로 API feature 활성화 순서다. 현재 변경은 이 작업을 실행하지 않는다.
- 관리 AppProject의 argocd·db-network 목적지와 VAP·ProxyClass 허용도 별도 기본 꺼짐 설정이다. 선행 설정이 없으면 배포를 활성화하지 않는다.
- API 계정 분리 전 GCP storage WIF도 새 Kubernetes subject를 명시적으로 허용해야 기존 GCS/rclone 기능이 유지된다. 이 GCP 변경 역시 기본 꺼짐이며 별도 계획 검토와 승인 대상이다.
- 실제 kind API와 공식 Argo CRD의 허용·거부 검증을 CI로 수행한다. 이 시험은 Tailscale 연결이나 실제 노트북 배포 성공을 증명하지 않는다.
- IAM Terraform mock 검증은 정책 문서만 검사한다. 배포 후 실제 두 Pod Identity로 각 조회 동작과 부모 경로 거부를 확인해야 운영 경계가 입증된다.
- platform의 Secret 발행·폐기는 GET 없이 동작해야 한다. 알려진 이름의 CREATE/UPDATE/DELETE와 기존 소유권 admission 검증을 사용한다.
- Secret 삭제는 영속 상태가 terminal revoked인 등록만 수행한다. 같은 enrollment ID는 재활성화·재사용하지 않으므로 늦게 도착한 삭제 역시 해당 등록의 의도된 폐기다. ready/failed 일반 정리나 ID 재사용을 도입하면 UID/resourceVersion 사전조건 등 새로운 동시성 경계를 먼저 설계해야 한다.
- 활성화 전 전용 API 이미지의 agent 바이너리, 이미지 digest, 원격 보호 admission, 기능 소비자 계약을 함께 검토한다. 현재는 Draft를 유지한다.

## 관련 자료

- [Kubernetes ValidatingAdmissionPolicy](https://kubernetes.io/docs/reference/access-authn-authz/validating-admission-policy/)
- [Tailscale operator의 externalName 관리](https://tailscale.com/docs/kubernetes-operator/egress/access-tailnet-service)
- [AWS Parameter Store 경로 조회와 Deny 범위](https://docs.aws.amazon.com/systems-manager/latest/userguide/ps-restrict-parameter-access.html)
