# 0093. GKE 앱의 자동 관측 자원 쓰기를 제한적으로 허용한다

- 날짜: 2026-11-08
- 상태: 채택 — 사용자 코드·실제 권한 적용 승인. 문서 작성 시 앱 활성화 전.
- 관련 ADR: [0082 중앙 관측](0082-connect-gke-to-central-observability.md), [0087 관측 namespace 권한](0087-bound-gke-observability-argocd-writes.md)

## 배경과 제약

GKE backend 설치 후 HyperDX Prometheus 조회는 성공했지만 기존 Argo identity는 앱 namespace의 공개 CA ConfigMap과 PodMonitor를 생성할 수 없었다. 기존 view는 ConfigMap 읽기를 이미 허용하며 PodMonitor 읽기는 없었다. AppProject는 두 kind를 이미 허용한다. 새 앱도 자동 관측되어야 하므로 기존 앱에 관리자 수동 생성만 하는 방식은 충족하지 못한다.

## 검토한 대안

- 앱별 수동 생성: 새 앱마다 작업이 필요하여 제외한다.
- 제한 없는 ConfigMap·PodMonitor 쓰기: 임의 수집 대상·인증 설정으로 확대될 수 있어 제외한다.
- 기존 Argo identity에 필요한 종류만 추가하고 별도 admission으로 내용·소유권 제한: 선택한다.

## 결정과 이유

기본 off인 `observability.appResourcesEnabled/appResourcesGranted/appCABundle`로 새 정책과 역할을 분리한다. Argo identity의 observability 밖 ConfigMap·PodMonitor 쓰기는 플랫폼 소유 이름 계약과 restricted PSA namespace에서만 허용한다. CA는 이름 `hopcharlie-observability-ca`, data `ca.crt` 하나이며 승인된 공개 bundle과 정확히 일치해야 한다. PodMonitor는 같은 앱 이름의 CNPG selector와 metrics/30s/10s endpoint만 허용하고 환경 gke·클러스터 charlie-dev-gcp-workload의 고정 relabelings 두 개만 허용하며 추가 spec 키·namespaceSelector·인증·TLS·proxy·임의 relabel은 거부한다. DELETE는 oldObject, 나머지는 object를 검사한다.

기존 ConfigMap 읽기는 재사용한다. PodMonitor 전체 namespace get/list/watch를 추가하며 Secret 권한은 늘리지 않는다. 새 guard와 Deny Binding 설치 → 금지 dry-run 검증 → 역할·권한 binding → 정상·금지 요청 검증 순서로 관리자 선택 적용한다. CA/PodMonitor 최초 CREATE는 같은 Argo identity로 검증한다. rollback은 권한 binding 제거 후 guard 제거다.

## 영향과 후속 작업

- 노드·Pod·CNPG 지표는 Prometheus, 로그·트레이스는 기존 ClickStack 경로를 유지한다.
- Argo가 전체 namespace의 PodMonitor 구성을 읽을 수 있으며 쓰기 RBAC는 admission과 함께 제약된다. 일반 ConfigMap/Secret 값 출력·저장은 수행하지 않는다.
- CA가 바뀌면 guard의 old/new bundle 전환과 기존 객체 갱신·삭제를 함께 검증해야 한다.
- 기존 CI·실제 GKE server dry-run으로 검증하고 앱2개는 순차 활성화한다. SDK가 없는 앱의 내부 trace는 완료로 표시하지 않는다.
- 온프레미스·업무 DB 데이터·토폴로지·IAM·새 CI는 변경하지 않는다.

## 관련 자료

- [앱 관측 guard](../../helm/charts/gcp-argocd-access/templates/app-observability.yaml)
- [GKE 활성화](../../clusters/gcp-dev-workload/values/app-observability.yaml)
- [검증·복구](../operations/gke-observability.md)
