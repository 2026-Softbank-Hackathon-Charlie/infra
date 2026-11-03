# 0018. 기본 메트릭과 관리 클러스터 모니터링을 단계적으로 설치

- 날짜: 2026-11-03
- 상태: 채택 — 승인한 세 앱 설치·실제 메트릭/Grafana 검증 완료
- 관련 ADR: [0017 수동 연결 준비](https://github.com/2026-Softbank-Hackathon-Charlie/infra/blob/c6e1034/docs/adr/0017-adopt-existing-helm-resources-in-argocd.md)

## 배경과 제약

metrics-server와 kube-prometheus-stack Application은 등록했지만 아직 설치하지 않았다. 사용자는 HyperDX 도입도 고려하면서 우선 두 구성부터 설정하는 범위를 문의했다. 현재 관리 EKS에 플랫폼이 실행되고 workload EKS는 기본 구성만 있다. 모든 Argo 앱과 root는 수동 상태다. 현재 Grafana values에는 미설치 Loki 데이터 소스가 있고 관리자 Secret을 별도 참조한다.

## 검토한 대안

- 전체 기본 addon/root sync: 아직 선택하지 않은 로그/배포 도구와 workload 운영 구성을 동시에 설치하므로 제외한다.
- HyperDX 결정까지 모든 모니터링 보류: 중복은 줄지만 현재 플랫폼을 볼 수 있는 기본 메트릭·대시보드 준비가 지연된다.
- metrics-server 양쪽 및 관리 Prometheus 스택만 수동 설치: 기존 구성을 대부분 재사용하고 범위를 한정할 수 있어 제안한다. HyperDX 확정 시 일부 모니터링 구성이 달라질 수 있다.

## 결정과 이유

두 EKS의 metrics-server와 관리 EKS의 kube-prometheus-stack 세 앱만 사용자가 승인한 범위에서 설치한다. Grafana의 Loki 데이터 소스만 제외하고 현재 replica/image/자원/7일 보존/PVC27GiB 및 ClusterIP 방식을 유지한다. root와 다른 앱의 자동화는 켜지 않는다. Grafana 인증은 기존 admin Secret 방식을 유지하고, 부재 시 해당 Secret 신규 생성은 구체적 실행 승인을 받는다. 스택이 내부 생성하는 TLS/설정 Secret도 별도 데이터 조회 없이 승인된 chart/operator 범위에서 생성한다.

metrics-server는 autoscaling/resource API를, Prometheus/Grafana는 현재 클러스터 메트릭과 대시보드를 담당한다. HyperDX는 로그·메트릭·트레이스·세션을 다룰 수 있으므로 향후 통합 시 중복 수집/보관/대시보드 유지 범위를 다시 검토한다. 이번 결정은 HyperDX 배제나 로그·트레이스 전용 분담 확정이 아니다. OTLP 전송이나 앱 SDK는 변경하지 않는다.

## 영향과 후속 작업

- 기대 효과: 현재 플랫폼과 노드 상태를 확인하면서 후속 관측 도구 선택을 유지한다.
- 단점과 위험: 관리 Prometheus20GiB/Grafana5GiB/Alertmanager2GiB PVC 및 Pod 자원 비용이 생긴다. metrics kubelet TLS, scraper 접근, Grafana Secret 준비와 노드 자원 여유는 미확인이다. HyperDX 도입 후 일부 투자·구성이 중복될 수 있다. metrics-server upstream chart는 APIService serving certificate 검증을 생략하는 기본값을 사용하므로, kubelet 인증서 검증과 구분해 사용자에게 명시하고 해당 기본값 수용 승인 없이는 metrics sync를 진행하지 않는다. CA 연동을 선택하면 별도 보안 설계로 검토한다.
- 후속 작업: [설치 계획](../plans/metrics-and-management-monitoring.md)의 독립 리뷰·사용자 승인 후 사전 조회와 수정 PR·검증을 수행한다. 세 앱을 하나씩 수동 sync하고 필요 시 해당 operation만 종료한다. 외부 알림 수신처·Loki·OTel·HyperDX·workload Prometheus 설치는 별도 범위로 남긴다.

## 관련 자료

- [관리 모니터링 values](../../clusters/aws-dev-management/values/kube-prometheus-stack.yaml)
- [관리 metrics-server values](../../clusters/aws-dev-management/values/metrics-server.yaml)
- [GitOps Application](../../helm/gitops/templates/applications.yaml)
- [Metrics Server](https://kubernetes-sigs.github.io/metrics-server/)
- [ClickStack](https://github.com/ClickHouse/ClickStack)

## 승인 범위 — 2026-11-03

사용자는 APIService의 인증서 검증 생략 기본값, kubelet CA 검증 유지, 관리자·내부 TLS/설정 Secret 생성, 수정 PR merge, 세 앱만의 수동 sync/중단 및 PVC27GiB를 명시한 계획을 승인했다. 이 ADR의 채택은 실제 배포 성공을 뜻하지 않으며 결과는 설치 계획에 별도로 기록한다.

## 실행 결과 — 2026-11-03

승인한 metrics-server 두 앱과 관리 kube-prometheus-stack이 Synced/Healthy, operation Succeeded다. 관리 관측 PVC27GiB가 Bound이고 Grafana 인증·Prometheus datasource와 target19개 정상 수집을 확인했다. 기존 플랫폼·PVC는 유지했다. root/앱 자동화와 다른 애드온 설치는 계속 제외한다. 최초 monitoring 준비 중 실패는 동일 revision의 일반 sync1회로 Hook 완료 후 해소했다. 상세 결과와 접근·정리 상태는 연결한 계획에 기록했다.
