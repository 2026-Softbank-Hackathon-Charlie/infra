# 0082. GKE 관측 신호를 전용 Tailscale 경로로 중앙 ClickStack에 연결한다

- 날짜: 2026-11-07
- 상태: 채택 — 사용자 코드 준비·PR·기존 CI 검증 승인. Secret·ACL·클러스터 변경과 실제 배포는 미실행.
- 관련 ADR: [0050 중앙 ClickStack](0050-adopt-clickstack-for-hackathon-observability.md), [0062 자동 관측](0062-automatically-observe-platform-workloads.md), [0063 TLS 갱신](0063-rotate-observability-tls-with-public-ca-bundles.md)

## 배경과 제약

AWS 관리·워크로드 수집이 동작해도 GKE 관측 profile은 비활성이며 endpoint/CA가 없었다. 실제 GKE에서 Ready 노드 3개, 기존 Tailscale operator·PD StorageClass를 확인했고 observability namespace·monitoring.coreos.com CRD는 없었다. 기존 GCP Managed Prometheus는 Google 관리 경로이며 이번 HyperDX의 자체 Prometheus 연결을 대신하지 않는다.

사용자가 현재 온프레미스에는 손대지 말라고 지시했다. 이 결정의 대상은 AWS 관리 클러스터와 GKE뿐이며 온프레미스 리소스·설정·자격은 변경하지 않는다. 기존 AWS 관측·GKE 앱·DB·복제 경로도 보존한다.

## 검토한 대안

- 공개 OTLP·Prometheus endpoint: 새로운 공개 노출과 방어 구성이 필요해 제외한다.
- 기존 AWS→GKE 앱 80 포트 라우터를 확장: 기존 경로·네트워크 정책에 영향을 주므로 제외한다.
- 기존 두 클러스터의 Tailscale operator로 전용 관측 ingress/egress 생성: 기존 연결 방식을 재사용하며 DB tag·proxy와 분리할 수 있어 선택한다.
- 모든 신호를 ClickHouse 메트릭으로 이전: 기존 PromQL·알림 계약을 바꾸므로 이번 범위에서 제외한다.

## 결정과 이유

GKE 앱/Alloy → GKE OTel gateway → 전용 tailnet 경로 → 기존 중앙 OTel collector → ClickHouse로 로그·trace를 수집한다. gateway는 GKE provider/region/zone/cluster 신원을 고정한다. 로컬 앱 TLS와 중앙 TLS·bearer는 유지하고 중앙 인증서의 기존 SAN을 SNI로 검증한다.

GKE 메트릭은 독립 Prometheus에 저장하고 TLS·BasicAuth로 제한한 기존 query proxy를 전용 tailnet 경로로 HyperDX에서 조회한다. HyperDX URL과 query leaf의 SAN은 `observability-gke-metrics.observability.svc.cluster.local`로 고정한다. GKE 전용 query 인증정보를 사용하고 TLS 이름 검증을 끄지 않는다. node·Pod·CNPG를 수집하며 kubelet/cAdvisor는 신뢰한 Kubernetes API proxy와 회전하는 projected SA token을 사용한다. 새 장기 SA-token Secret을 만들지 않는다. 이 읽기 역할의 `nodes/proxy` 권한은 실제 배포 승인에서 명시한다.

GKE upstream은 기존 중앙 차트의 `additionalIngestTokenKeys`를 재사용한 GKE 전용 bearer로 준비한다. 기존 AWS 토큰 키를 앞에 유지하고 새 키를 뒤에 추가한다. 발급·등록·중앙 ConfigMap/Deployment 반영과 rollout은 후속 실행 승인의 대상이다. 클러스터별 토큰 분리는 강한 멀티테넌트 격리를 뜻하지 않는다.

GKE 대상은 `observability.externalTargets.gke`에 두고 기존 AWS root `targets`와 분리한다. 기본값/준비 파일은 off이며, 기존 AWS Application/AppProject와 앱 profile의 렌더 결과를 보존한다. 실제 실행 시 생성되는 새 GKE 관측 Application도 수동 sync이며 자동 prune·전체 baseline 배포를 하지 않는다.

## 영향과 후속 작업

- 기대 효과: 새 GKE 앱·서비스·CNPG·Job이 공통 소유 label/OTEL profile을 통해 수집되며 앱별 collector 목록이나 토큰을 추가하지 않는다.
- 저장소: Alloy는 node-local hostPath의 positions/queue를 유지한다. 노드 교체 시 미전송 데이터가 유실될 수 있다. gateway와 Prometheus는 개별 PD PVC를 사용한다. Alloy `otelcol.storage.file`은 기존 public-preview 예외를 유지한다.
- 위험: 노드 로그 hostPath와 전용 namespace의 privileged PSA 허용, 관측 metadata RBAC, `nodes/proxy` 읽기, node-exporter의 호스트 지표 접근, 새 tailnet 장비·tag/ACL·TLS/Secret·Pod/PVC 비용과 추가 운영 책임이 생긴다. 정책은 합집합으로 평가하므로 추가 NetworkPolicy만으로 기존 allow 정책이 좁아진다고 주장하지 않는다.
- 검증: 기존 CI에서 AWS 불변/off 시 GKE 객체 0개, 이미지 pin, 전용 namespace/포트·SAN 계약, Prometheus monitor selector, projected token/CA, metadata identity·저장소·권한을 검사한다. 실제 배포는 새 실행 승인 후 별도 단계이며 코드 PR 성공을 수집 완료라고 표시하지 않는다.
- 온프레미스는 사용자 지시로 제외한다. GCP managed 수집기·DB 경로·플랫폼 자격·기존 AWS 관측은 제거하거나 덮어쓰지 않는다.

## 관련 자료

- [실행 준비·완료 기준·복구](../operations/gke-observability.md)
- [외부 대상 template](../../helm/gitops/templates/observability-external.yaml)
- [관측 전송 chart](../../helm/charts/observability-transport/Chart.yaml)
- [공개 연결 계약](../../clusters/gcp-dev-workload/observability-contract.json)
- [준비 gate](../../clusters/gcp-dev-workload/values/observability-activation.yaml)
