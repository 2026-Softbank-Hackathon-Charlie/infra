# 0083. 온프렘 CNPG의 API backend egress를 앱 트래픽과 분리한다

- 날짜: 2026-11-07
- 상태: 제안 — 코드·읽기 전용 진단만 수행했으며 운영 적용은 하지 않음
- 관련 ADR: [0072](0072-add-onprem-as-paired-database-environment.md), [0081](0081-onprem-minio-storage-and-platform-image-flavor.md)

## 배경과 제약

온프렘 moimroom-mig의 CNPG pgbasebackup가 Kubernetes API `10.43.0.1:443`에 연결하지 못했다. 앱은 아직 비어 있는 DB-rw Endpoint 때문에 5432 연결을 기다렸다. 현재 앱 차트의 namespace 정책은 TCP 443을 허용하지만 onprem 프로필에는 실제 API backend의 TCP 6443 허용이 없다.

승인된 기존 Argo 등록 자격으로 읽기 전용 조회한 결과 Service는 `10.43.0.1:443`, ready Endpoint는 노드 `172.17.124.166:6443`이었다. 13:52:53Z에 다른 운영 주체가 만든 `cnpg-allow-k3s-api`는 해당 CNPG cluster 라벨의 Pod에 `172.16.0.0/12:6443` egress를 허용했다. 그 이전 Job은 API refused로 실패했지만, 이후 13:53:59Z의 Job은 GKE DB 연결·checkpoint·WAL receiver·replication slot 생성까지 진행했다. 같은 기존 Job container에서 Service 443과 backend 6443 TCP 연결을 각각 확인했다.

따라서 현재 kube-proxy 장애를 재현했다고 주장하지 않는다. 관측은 API backend egress 누락과 임시 허용 후의 회복에 부합한다. DB bootstrap 완료와 앱 Healthy는 별도 확인 사항이다. 이번 작업은 NetworkPolicy 생성·적용·삭제, Secret 발급, RBAC 변경을 하지 않는다.

## 검토한 대안

- namespace의 extraEgress에 6443을 추가: 웹·worker에도 불필요한 API backend 접근을 더하므로 제외한다.
- 모든 onprem namespace에 수동 정책 유지: 새 DB 등록이나 재설치에서 같은 누락이 반복되므로 제외한다.
- CNPG Pod와 bootstrap Job을 함께 고르는 별도 정책: 실제 `cnpg.io/cluster` 라벨을 사용하고 앱 트래픽과 분리할 수 있어 선택한다.
- 사설 대역 /12 허용: WSL 주소 변경에 더 유연하지만 API 서버 이외의 약 100만 주소까지 허용한다. 수동 정책을 그대로 확대 재현하지 않고, 확인한 Endpoint /32만 기본 허용한다. 주소 변경 시 재검증과 프로필 갱신이 필요하다는 제한을 수용한다.

## 결정과 이유

`profile.networkPolicy.cnpgApiServerEgress`에 `{cidr, port}` 목록을 둔다. 기본값은 빈 배열이다. namespace 정책이 켜져 있고 postgres 리소스가 있을 때만 `hopcharlie-cnpg-api` Egress 정책을 그린다. selector는 앱의 CNPG cluster 라벨 하나이며 instanceRole/podRole을 요구하지 않아 pgbasebackup Job도 포함한다.

onprem 프로필은 실제 확인한 `172.17.124.166/32`, TCP `6443`만 선언한다. 차트에 IP나 포트를 고정하지 않고 다른 API 주소/포트를 명시할 수 있다. 기존 DNS·HTTPS·MinIO·DB replication 허용과 ingress는 바꾸지 않는다. eks/gke와 DB 없는 앱에는 정책을 추가하지 않는다.

Service 주소 변환과 NetworkPolicy 처리 순서는 CNI/Service 구현에 따라 다를 수 있다. Service 포트만 보고 backend 포트가 허용된다고 판단하지 않으며, 적용 전후 두 경로를 실제 Pod에서 따로 확인한다. 정책은 RBAC 권한을 주거나 TLS·인증서 검증을 끄지 않는다.

## 영향과 후속 작업

- 기대 효과: 온프렘 DB 최초 생성·새 환경 추가 시 CNPG의 API 연결 누락을 차트 수준에서 예방한다.
- 위험: WSL 재시작 등으로 API Endpoint가 바뀌면 이 /32 정책이 새 주소를 자동 허용하지 않는다. 재등록/점검에서 Endpoint를 확인하고 프로필을 갱신한 뒤 검증해야 한다. 접근 불가를 우회하려고 namespace 전체나 /0을 자동 개방하지 않는다. 최종 CIDR 유효성은 Kubernetes 검증도 거친다.
- 로컬 검증은 실제 Helm 렌더와 schema 거부, DB 없는 앱·eks/gke·정책 비활성 시 미생성을 확인한다. 이것은 live CNI의 실제 차단/허용 시험을 대신하지 않는다.
- 운영 반영은 root가 별도 승인 범위와 구체적인 diff를 검토한 뒤 수행한다. 임시 정책은 GitOps 정책의 실제 적용과 API 연결을 확인한 다음 소유권·범위를 확인해 정리한다. 이번 에이전트가 정리하지 않는다.
- DB basebackup 완료, DB-rw Endpoint 준비, 앱 readiness와 공개 경로를 차례로 확인해야 온프렘 전체 연결 성공이라고 보고할 수 있다.

## 관련 자료

- [앱 guardrails](../../helm/charts/hopcharlie-app/templates/guardrails.yaml)
- [앱 환경 프로필](../../clusters/aws-dev-management/values/hopcharlie-apps.yaml)
- [렌더 회귀](../../scripts/tests/test-db-network-assets.py)
- [Kubernetes NetworkPolicy의 주소 변환과 selector](https://kubernetes.io/docs/concepts/services-networking/network-policies/#behavior-of-to-and-from-selectors)
