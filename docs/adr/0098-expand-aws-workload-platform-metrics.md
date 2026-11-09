# 0098. AWS workload 플랫폼 메트릭 수집 확장

- 날짜: 2026-11-08
- 상태: 채택 — 독립 리뷰 및 사용자 실행 승인 후 PR188 배포 완료. 인증된 실제 HyperDX 조회까지 검증했으며 결과는 운영 안내에 기록한다.
- 관련 ADR: [0062 자동 관측](0062-automatically-observe-platform-workloads.md), [0050 ClickStack](0050-adopt-clickstack-for-hackathon-observability.md)

## 배경과 제약

실제 인증된 HyperDX curl 조회에서 AWS workload는 CNPG 메트릭만 반환했고 노드 정보와 컨테이너 CPU는 빈 시계열이었다. 해당 환경의 활성 values는 cnpg-prometheus.yaml이며 node-exporter, kube-state-metrics, kubelet 수집이 꺼져 있었다. 노드 3개는 Ready이고 추가 요청량을 수용한다. GKE는 이미 수집 중이며 기존 Prometheus connection의 HyperDX Source만 누락돼 있었다.

기존 CNPG PodMonitor 계약, Prometheus 100m/512Mi 요청·1Gi 제한·PVC10Gi·7일/8GB 보존, 업무 앱·DB·IAM·인증·네트워크 정책을 유지한다. 관리/GKE의 공통 기본 동작을 바꾸지 않는다.

## 검토한 대안

- CNPG 수집 유지: 자원은 적으나 사용자 요청인 플랫폼 CPU·메모리 확인을 충족하지 못한다.
- 새 메트릭 저장소: 중복 운영과 별도 저장 비용이 생겨 제외한다.
- 기존 Prometheus에 고정 버전 exporter와 API server proxy 수집 추가: GKE에서 사용하는 구성과 이미지를 재사용하며 선택한다.
- kubelet 10250 직접 접근: 별도 네트워크와 인증 경로 검토가 필요해 이번 범위에서 제외한다.

## 결정과 이유

AWS 전용 values에서 node-exporter와 kube-state-metrics를 켜고 observability namespace의 정확한 네 ServiceMonitor를 선택한다. 기존 CNPG PodMonitor selector는 보존한다. KSM collector는 pods/nodes/namespaces/deployments/statefulsets/daemonsets/replicasets/jobs/cronjobs/persistentvolumes/persistentvolumeclaims/services로 한정하고 해당 metadata list/watch만 부여한다.

kubelet과 cAdvisor는 API server의 nodes proxy를 통해 30초 간격으로 읽는다. 회전 ServiceAccount token 파일과 검증된 CA를 사용한다. 새 default-off awsPlatformMetrics 값으로 AWS 전용 ClusterRole/Binding 네 개를 분리한다. Prometheus에 nodes get/list/watch 및 nodes/proxy get만 추가한다. nodes/proxy GET은 RBAC로 metrics 하위 경로에만 제한할 수 없어 더 넓은 kubelet API 접근 권한을 부여한다는 위험을 승인 범위에 포함했다.

node-exporter는 nonroot 65534, hostNetwork, hostPID=false, 읽기 전용 /proc·/sys·/ mount, 읽기 전용 컨테이너 파일 시스템, 모든 capability drop, 권한 상승 금지, SA token 자동 마운트 금지로 실행한다. private node의 9100 listener를 사용한다. KSM 50m/128Mi와 node당 exporter 25m/32Mi로 현재 합계 125m/224Mi를 요청한다. 임의 노드 증설이나 기존 앱 자원 축소는 하지 않는다.

GKE Source 누락은 기존 team/connection에 promql Source 한 개만 조건부 생성해 보완한다. 기존 연결·키·사용자는 변경하지 않고 설치된 SourceSchema와 중복 여부를 확인한다. Source가 이미 있으면 재생성하지 않는다.

## 영향과 후속 작업

- 기대 효과: HyperDX에서 AWS 노드·컨테이너 CPU/메모리를 조회하고 GKE Source를 선택할 수 있다.
- 위험: nodes/proxy 권한, 호스트 네트워크·파일 읽기, 추가 시계열·TSDB 메모리/디스크 부하, 관측 설정 reload와 짧은 지연이 발생한다. 노드 수 증가 시 exporter 요청량도 늘어난다.
- 검증: 최소 권한·보안 설정·고정 이미지·정확 Monitor 네 개를 render 검사하고 실제 curl에서 비어 있지 않은 시계열·freshness·range point·CNPG up을 확인한다. 보호 API는 정상 로그인만 사용한다.
- 적용: 기존 두 Argo 앱에서 신규 RBAC를 먼저 선택 sync한 뒤 수집기·추가 scrape Secret·Prometheus CR의 정확한 변경 자원만 sync한다. 전체 root sync/prune은 하지 않는다.
- 복구: 설정 역커밋 후 이번 생성 Deployment/DaemonSet/Service/Monitor/SA/추가 scrape Secret/RBAC만 기록 UID·소유권 조건으로 제거한다. 기존 Prometheus StatefulSet/PVC/TSDB·CNPG·인증은 보존한다. GKE Source도 이번 생성 ID·내용이 일치할 때만 복구한다.

## 관련 자료

- [AWS Prometheus values](../../clusters/aws-dev-workload/values/cnpg-prometheus.yaml)
- [AWS boundary values](../../clusters/aws-dev-workload/values/observability-boundary.yaml)
- [AWS metrics RBAC](../../helm/charts/observability-boundary/templates/aws-metrics-rbac.yaml)
- [운영 안내](../operations/automatic-workload-observability.md)
- [Kubelet 인증·인가](https://kubernetes.io/docs/reference/access-authn-authz/kubelet-authn-authz/)

2026-11-08 적용 결과: AWS 15/15 대상 up·노드 3개·컨테이너 CPU/메모리·기존 CNPG up=1, GKE Source 등록과 보호 API 조회를 확인했다. 기존 두 Argo 앱은 Synced/Healthy이며 수집기·Prometheus 재시작은 0이다. 승인 범위 밖 변경이나 복구 실행은 없었다.
