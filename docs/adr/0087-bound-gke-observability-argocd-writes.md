# 0087. GKE 관측 Argo 쓰기를 이름 목록과 단계별 admission 전환으로 제한한다

- 날짜: 2026-11-08
- 상태: 채택 — 사용자 코드·한정 실행 승인. 이 문서 작성 시 실제 클러스터 반영 전.
- 관련 ADR: [0082 GKE 중앙 관측 연결](0082-connect-gke-to-central-observability.md)

## 배경과 제약

GKE의 기존 Argo Google identity는 앱 등록용 dynamic-apps 역할을 가지며 기존 admission 정책이 observability와 hopcharlie-db-network 쓰기를 막는다. 따라서 준비된 관측 Application만 생성해도 실제 설치는 실패한다. 기존 역할에는 Deployment·Service·NetworkPolicy의 전체 namespace 쓰기가 이미 있어 추가 Role에서 권한을 빼는 것만으로 기존 권한이 취소되지 않는다.

기존 AWS 수집과 DB proxy를 보존하고 온프레미스는 제외한다. 관리자 bootstrap과 Argo 선택 sync를 구분하며, 기존 CI와 사용자 PR 머지 방침을 따른다.

## 검토한 대안

- Argo에 cluster-admin 부여 또는 기존 admission 비활성화: 변경 범위가 지나치게 넓어 제외한다.
- 모든 관측 리소스를 관리자만 수동 운영: 반복 배포가 GitOps에서 분리되므로 제외한다.
- 고정 이름 allowlist admission과 namespace Role, 관리자 cluster bootstrap: 기존 정책을 유지하며 관측 쓰기만 분리할 수 있어 선택한다.

## 결정과 이유

`gcp-argocd-access`에 기본 off인 `observability.enabled`, `openExistingPolicy`, `grantAccess`를 추가한다. guard 설치·검증 → 기존 정책의 정확한 예외 → 추가 binding 순서로 적용한다. 차트는 잘못된 flag 조합을 거부하지만 서로 다른 시점의 클러스터 반영 순서는 운영자가 검증해야 한다.

새 fail-closed VAP는 Argo identity의 observability 쓰기를 `(group, resource, name)` 정적 목록에 한정한다. 인증 Secret 쓰기는 허용하지 않고 KPS 공개 scrape-config Secret만 허용한다. subresource·CONNECT도 거부한다. hopcharlie-db-network는 신규 NetworkPolicy 두 이름의 UPDATE/DELETE만 허용한다. 기존 역할이 CREATE를 허용하므로 admission에서도 이 두 이름의 CREATE를 명시 거부한다. 이 정책은 관리자·각 operator identity에는 적용하지 않는다.

CRD·RBAC·ProxyClass 및 기존 DB-network namespace의 신규 NetworkPolicy 생성은 관리자 bootstrap이다. KPS Application은 `skipCrds: true`로 렌더한다. boundary/transport는 관리자 소유 자원을 제외한 namespaced 자원만 선택 sync하며 전체 sync/prune하지 않는다. Argo의 추가 cluster 역할은 CRD·ClusterRole·ClusterRoleBinding·ProxyClass 읽기만 허용한다.

## 영향과 후속 작업

- 기본 off 시 기존 RBAC·admission 렌더는 동일하다. 기존 앱의 namespace 소유권·restricted PSA 검사 내용은 바꾸지 않는다.
- **실효 권한:** privileged 관측 namespace의 Deployment/DaemonSet 변경은 호스트 파일·ServiceAccount·Secret mount 접근으로 이어질 수 있다. 이름 목록은 Pod spec 격리가 아니다. 사용자는 이 실효 노드·자격 접근 위험을 포함한 위임을 승인했다. `cluster-admin` 미부여가 해당 실행 능력을 제거한다는 주장은 하지 않는다.
- Argo cache가 observability namespace의 Secret 값을 읽는다. Agent의 기존 Secret 값 출력·파일 저장은 허용하지 않는다. Kubernetes Secret GET은 키 하나만 서버에서 선택하지 않으며 전체 응답이 메모리에 수신됨을 승인 범위에 포함했다.
- 적용 전 새 VAP type-check와 같은 Argo identity의 실제 server dry-run 허용/거부를 확인한다. wildcard rule의 type-check만으로 CEL 동작을 입증하지 않으며 실제 요청 검증을 필수로 둔다.
- 복구는 기존 정책 예외 철회/원래 deny 확인 → 추가 binding 철회 → 새 VAP 제거 순서다. 새 제한을 먼저 제거하지 않는다.
- 이미지·requests·PVC·init/sidecar·rollout surge를 포함한 node별 용량 gate를 통과해야 설치한다. 실제 GKE logs/traces/Prometheus query 및 AWS 보존 검증은 별도 실행 결과로 보고한다.

## 관련 자료

- [관측 guard·RBAC](../../helm/charts/gcp-argocd-access/templates/observability.yaml)
- [고정 리소스 이름](../../helm/charts/gcp-argocd-access/files/observability-resources.json)
- [수동 적용 입력](../../clusters/gcp-dev-workload/values/argocd-observability-access.yaml)
- [운영 순서·복구](../operations/gke-observability.md)
- [Kubernetes VAP와 type checking](https://kubernetes.io/docs/reference/access-authn-authz/validating-admission-policy/#type-checking)
