# metrics-server와 관리 클러스터 모니터링 설치 계획

- 날짜: 2026-11-03
- 상태: 세 앱 설치 실행 준비 — 장기 SA 토큰의 변경 계획 리뷰·추가 사용자 승인 완료
- 분류: 영향이 크거나 불확실한 작업. 두 클러스터 APIService/RBAC와 관리 클러스터 CRD·webhook·관리자 Secret·PVC 생성을 포함한다.

## 목표·완료 기준

기존 플랫폼을 유지하면서 `charlie-management-metrics-server`, `charlie-workload-metrics-server`, `charlie-management-kube-prometheus-stack` 세 Application만 수동 sync한다. 자동 sync와 root sync는 계속 끈다.

완료 기준은 metrics APIService Available 및 두 클러스터 `kubectl top nodes/pods` 성공, 대상 세 앱 Synced/Healthy·operation 완료, Prometheus/Alertmanager/Grafana Ready, 관리 PVC 20+5+2GiB Bound, Grafana health 및 Prometheus query API에서 실제 scrape된 node/kube-state 메트릭 확인이다. 기존 플랫폼 Pod/PVC identity·Ready·재시작 횟수와 제외 앱의 자동화/operation 불변도 확인한다. 외부 알림 채널 연결과 전체 scrape target 100% 정상은 보장하지 않고 상태·미수집 대상을 보고한다.

## 범위·파일·제외

- 계정 525091405886 / ap-northeast-2 / 기존 charlie-admin·SSM으로 두 EKS를 조회한다. 사전 조회는 노드 allocatable·현재 Pod requests·이미 설치된 APIService/CRD/RBAC/namespace/StorageClass/Pod/PVC/App 상태 및 이벤트, `observability/grafana-admin` 및 예정된 monitoring chart/operator 소유 Secret의 **목록 메타데이터만** 포함한다. 기존 Secret 값·감사 로그·Pod 로그/exec는 제외한다.
- 코드 수정은 최신 main 기반 새 `feat/minimal-management-monitoring` 브랜치에서 한다. 관리 `clusters/aws-dev-management/values/kube-prometheus-stack.yaml`의 `grafana.additionalDataSources`에서 Loki 항목만 제거한다. 기존 chart/image digest·replica·자원·보존기간7일·PVC 크기·ClusterIP는 유지한다. README, 이 계획, ADR0018을 함께 문서화한다.
- Loki·HyperDX·ClickHouse·OTel·workload Prometheus·Sealed Secrets·Argo Rollouts·LBC·baseline·기존 플랫폼의 sync/수정은 제외한다. root/모든 앱 자동 sync는 계속 비활성화한다. GitOps 기본 addon 목록은 바꾸지 않으며 Loki Application은 미설치 상태로 남는다.
- Terraform/state/IAM/SG/Pod Identity/DNS/ALB/Ingress, EKS 업그레이드, PVC 삭제·교체와 기존 Helm upgrade/uninstall/rollback은 제외한다. HyperDX 도입 여부와 로그·트레이스·애플리케이션 계측/OTLP 전송은 미결정으로 남긴다.

## 단계·의존성

1. 신규 plan-reviewer의 독립 리뷰·보완 후 최종 계획/권한/비용을 제시하고 사용자 승인을 받는다. 일반 설정 요청을 아직 생성하지 않은 Secret·실제 sync 실행 승인으로 대신하지 않는다.
2. 승인 후 기존 SSM 터널은 보존하고 이번 작업 전용 터널/보호 kubeconfig를 쓴다. 실제 계정/두 cluster identity를 확인하고 root/제외 앱의 자동화·대기/Running/Terminating operation 부재를 재확인한다. 대상 앱 source/project/목적지 및 chart pin(3.14.0/91.8.2)이 기대값과 다르면 중단한다.
3. 노드 여유 CPU/메모리/스케줄링·관리 gp3/EBS CSI·observability namespace와 Pod Security·기존 리소스 소유권을 점검한다. 관리 baseline/gp3는 이미 존재하는 것을 재사용하고 workload baseline을 적용하지 않는다(metrics-server는 kube-system에서 PVC 없이 동작). requests 합계 및 노드 여유를 렌더링으로 계산한다. 부족하면 node size/replica를 임의 변경하지 않고 범위 재검토한다.
4. `grafana-admin`은 namespace의 서버 Table 메타데이터 응답으로 존재만 확인한다. 기존에 있으면 읽거나 덮어쓰지 않고 재사용하며, 키 누락은 배포 이벤트로 확인할 수 있다. 없으면 승인 범위 내에서 `admin-user=admin`, 암호학적 난수 32바이트 이상 비밀번호로 **정확히 한 개**의 Opaque Secret을 create한다. 비밀번호/Secret 원문은 stdout·CLI 인자·Git·Terraform state·문서에 넣지 않는다. stdin 전달 및 메모리 create를 쓰며, 새 자격 증명만 `.generated/monitoring/grafana-admin-credentials`(디렉터리0700/파일0600)에 사용자 인수용으로 남긴다. 기존 값 조회·비밀번호 회전은 하지 않는다. 생성 직전 재확인하고 충돌 시 덮어쓰지 않는다. 보호 디렉터리/파일 소유자·권한을 확인하고 symlink를 거부한다. 보호 파일은 no-follow/exclusive create로 기존 파일을 덮어쓰지 않으며, 입력 파일 준비가 성공한 뒤 Secret을 생성한다. create 전 실패하면 이번에 만든 미사용 credential 파일을 정리하고, 생성 성공 시에는 사용자 인수용으로 보존한다. API 오류에서 Secret 입력 원문을 출력하지 않는다.
monitoring chart·admission Hook Job·Prometheus Operator가 자체 생성하는 TLS/설정 Secret도 실행 승인 범위에 포함한다. 렌더링과 고정 chart/operator convention으로 예상 이름·namespace·소유자를 목록화하고, 기존 Secret 메타데이터와 비교한다. 무관한 기존 Secret 또는 소유권 충돌이 있으면 덮어쓰기 전에 중단한다. 신규 chart/controller가 생성한 Secret 값을 직접 조회하거나 raw manifest를 기록하지 않는다.
5. 새 브랜치에서 datasource 수정과 문서를 구현한다. 고정 Helm3.19.1/PyYAML6.0.3으로 전체 Helm·inventory·diff 검사, 실제 target의 세 차트 렌더링과 AppProject 허용범위·이미지 고정·Secret 참조·권한·저장공간을 검증한다. metrics APIService의 `insecureSkipTLSVerify`/CA 연결과 monitoring webhook TLS/failurePolicy를 명시적으로 확인한다. metrics-server chart 3.14.0의 APIService 기본값은 `insecureSkipTLSVerify=true`이며 현재 저장소에 override가 없다. 이는 kubelet 연결 검증과 다른 경로다. 이 upstream 기본값을 유지한 설치는 최종 실행 승인에 명시적으로 포함해야 하며, 사용자가 수용하지 않으면 metrics sync 전에 중단하고 CA 연결 방안을 별도 설계·리뷰한다. 승인 이후에도 TLS 오류를 우회하려고 새 insecure flag를 추가하지 않는다. 전용 PR을 생성하고 의도한 diff/필수 CI 성공 후 사용자 승인에 포함된 범위에서만 merge한다. 다른 main/앱 변경이 생기면 관련 diff를 재검토한다. 보안/권한·이미지·버전 등 새 변경이 필요하면 재계획한다.
6. 세 앱의 자동화를 켜지 않고 **하나씩** 수동 operation을 요청한다. Kubernetes Application API의 `operation.sync`를 사용하며 chart version과 merge commit을 `revisions`에 source 순서대로 고정하고 `prune=false`, `ServerSideApply=true`, `CreateNamespace=true`, 기본 Hook 전략(force=false)을 사용한다. kube-prometheus-stack의 admission 인증서 생성/patch Hook Job을 실행해야 하므로 hook을 건너뛰는 Apply 전략은 사용하지 않는다. root와 다른 앱을 sync하지 않는다. API/RBAC/server dry-run이 실패하면 Argo 새 로그인·Secret 조회로 우회하지 않는다.
7. operation 갱신은 live resourceVersion과 operation 부재를 조건으로 한다. 기존 완료 operationState는 v3.5.3 SetAppOperation 동작처럼 요청과 함께 비우고, 다른 작업 중이면 중단한다. 먼저 management metrics → 검증 → workload metrics → 검증 → management monitoring 순서로 실행한다. metrics 설치 후 실제 `kubectl top` 사용량도 확인하여 관리 monitoring 설치 전 자원 여유를 재평가한다. metrics kubelet TLS 오류가 나면 `--kubelet-insecure-tls`를 추가하지 않고 보고한다.
8. 앱별 최대15분 확인한다. 오류·기존 서비스 영향이 감지되면 다음 앱은 시작하지 않는다. **승인 대상 세 앱 중 이번 실행한 앱만** 진행 중 operationState.phase를 Terminating으로 전환해 Argo가 종료하도록 요청한다(v3.5.3 TerminateOperation과 같은 Application update semantics). 대기 operation만 있고 Running 전이면 resourceVersion 조건으로 operation을 제거한다. 자동화 해제만으로 진행 중 작업이 멈춘다고 표현하지 않는다. termination 미완료는 보고하고 임의 controller/Pod kill·재시작은 하지 않는다. 이 중단 권한은 배포 승인에 함께 포함한다.
9. Grafana `ClusterIP`에 loopback 포트포워딩을 사용해 `/api/health`, Prometheus query API로 검증한다. Grafana health만으로 datasource 연결을 검증했다고 주장하지 않는다. provision된 Prometheus datasource의 URL/config와 직접 Prometheus query 결과를 별도로 확인하며, 기존 Secret 재사용 시 비밀번호를 읽지 않으므로 UI 로그인·사용자 세션의 end-to-end 확인은 미검증으로 보고한다. 공개 ingress·새 API token/Argo 로그인은 만들지 않는다. 새 Grafana 비밀번호는 보호 파일 위치로 인수하며 기존 Secret 비밀번호는 읽지 않는다. 임시 검증용 포트포워딩/터널/kubeconfig는 정리하고, 사용자가 계속 볼 포워딩은 대상·주소·종료 방법을 명시한다. 다른 사용자의 현재 Argo8080/SSM10443은 종료하지 않는다.

## 검증·실패 처리

- 로컬: 전체 `make helm-check`, `make inventory-check`, `git diff --check`, 실제 값 렌더링 및 requests/PVC/클러스터 권한 인벤토리. 새 아키텍처를 테스트로 가정하지 않고 실제 chart 계약을 확인한다.
- 실제: 세 앱 operation/revision/health, metrics API/`kubectl top`, 관측 Pod/PVC/Prometheus scrape/query, Grafana health, 기존 Pod/PVC 보존과 root/제외 앱 manual 상태. Alertmanager 수신처는 현재 설정이 없어 외부 알림 전달은 검증 대상이 아니다.
- 리소스가 생성된 뒤 실패해도 PVC/CRD/Secret을 자동 삭제하지 않는다. 최초 설치라 이전 정상 revision으로 간단히 rollback할 수 있다고 주장하지 않는다. 진행 작업의 중단 후 이미 생성한 리소스를 목록화하고, 제거/수정은 별도 계획/승인으로 처리한다.
- 기존 TLS/권한/보안 정책을 낮추는 임시 우회, 재설치/강제 sync/Replace/Force, 다른 앱 sync는 하지 않는다. 검사/게이트를 통과하지 못하면 부분 성공과 미실행을 구분해 보고한다.

## 권한·비용·불확실성

실행 승인에는 두 EKS/Argo 읽기·SSM/port-forward, 지정 관리자 Secret의 **부재 시 신규 자격 증명 생성·Secret create**, GitHub 전용 PR 생성/merge, 세 앱 한정 수동 sync 및 실패 시 해당 operation 종료, chart/Hook Job/operator가 만드는 TLS·설정 Secret 및 CRD/RBAC/APIService/webhook/Pod/Service/PVC·EBS 생성이 포함되어야 한다. 관측 PVC 총27GiB 외 exporter/sidecar/운영자 Pod CPU/메모리와 EBS 비용이 추가된다. 새로운 외부 노출은 없다. metrics APIService에는 위에 명시한 upstream serving-certificate 검증 생략 기본값이 있으므로 이 위험 수용 여부를 최종 승인에 포함한다. kubelet 인증서는 기존 CA 검증 설정을 유지한다.

현재 미확인 항목은 Grafana Secret 존재, 정확한 여유 자원, kubelet 인증서 호환성과 scrape 대상 실제 연결, 앱별 Argo/Admission/API 권한이다. 사전 점검에서 해소하거나 실패 원인과 함께 중단한다. 이미 설치된 리소스가 발견되면 무조건 adopt하지 않고 소유권·diff를 검토해 승인 범위 내인지 판단한다.

## ADR·미래 HyperDX 범위

[ADR0018](../adr/0018-stage-minimal-management-monitoring.md)을 제안으로 작성한다. [ADR0017 준비 단계](https://github.com/2026-Softbank-Hackathon-Charlie/infra/blob/c6e1034/docs/adr/0017-adopt-existing-helm-resources-in-argocd.md)의 수동 제어를 유지하며, 이 세 앱에만 별도 실제 sync 승인 범위를 추가한다. metrics-server는 HPA용 resource API이고 Prometheus/Grafana는 클러스터 시계열·대시보드용이다. HyperDX도 메트릭을 지원하므로 나중에 통합/중복·보존·비용을 다시 결정할 수 있다. 이번에 HyperDX를 로그·트레이스 전용으로 제한하거나 전체 관측 백엔드를 확정하지 않는다.

Argo operation 근거: [SetAppOperation](https://github.com/argoproj/argo-cd/blob/v3.5.3/util/argo/argo.go), [SyncOperation type](https://github.com/argoproj/argo-cd/blob/v3.5.3/pkg/apis/application/v1alpha1/types.go), [TerminateOperation](https://github.com/argoproj/argo-cd/blob/v3.5.3/server/application/application.go).

독립 리뷰: 새 plan-reviewer GREEN. Hook 전략·Secret 생성 범위·credential 인수·datasource 검증 한계를 보완했다. APIService TLS 기본값을 명시적 승인 조건으로 보완한 뒤 같은 reviewer의 최종 재검토도 GREEN이다. 이 기본값 수용이 명확하지 않으면 실제 metrics sync를 진행하지 않는다.

검토 근거: [Metrics Server FAQ — API server와 Metrics Server 사이 TLS](https://github.com/kubernetes-sigs/metrics-server/blob/master/FAQ.md). 최종 GREEN은 계획 품질 판정이며 설치나 APIService TLS 기본값 수용에 대한 사용자 승인을 대신하지 않는다.

## 승인 및 준비 결과 — 2026-11-03

사용자는 APIService TLS 검증 생략 기본값 유지, 지정 Grafana 관리자 자격 증명/Secret 부재 시 생성, chart 내부 TLS/구성 Secret, 수정 PR merge, 세 앱의 수동 sync와 해당 operation 중단 권한·PVC27GiB 비용을 명시한 최종 요청에 “진행해줘”라고 승인했다.

새 브랜치 `feat/minimal-management-monitoring`을 main `bea060e`에서 만들었고 Grafana 추가 데이터 소스의 Loki 항목만 제거했다. AWS charlie-admin 세션 만료를 확인하여 사용자 재로그인을 요청했다. 현재까지 클러스터 조회·Secret 생성·실제 sync는 시작하지 않았다.

### 로그인 갱신 후 사전 점검

사용자가 AWS 재로그인 완료를 알렸고 계정/두 EKS 신원을 재확인했다. 모든 앱 수동 상태·다른 operation 부재, 두 클러스터 노드 Ready·무 taint, metrics APIService/Deployment 부재, 관리 gp3/EBS CSI와 observability namespace를 확인했다. 현재 chart 리소스 이름 충돌은 없다. 관리 요청 여유는 CPU2.16코어/메모리약11.3GiB, workload CPU3.18코어/메모리약13.1GiB다. metrics-server 추가 요청은 각 CPU0.2/256MiB, monitoring 최소 요청은 CPU0.45/832MiB이며 exporter/reloader 등 추가 실제 사용량은 이후 확인한다. Grafana 관리자 Secret은 없다.

전체 Helm 검사(21개 고정 이미지), inventory(203개 파일), diff 검사가 통과했다. 관리 Prometheus webhook은 초기 failurePolicy Ignore/CA 비어 있음이며 create/patch Hook으로 인증서를 생성하고 패치하도록 선언돼 있다. metrics APIService는 승인된 insecureSkipTLSVerify true다.

**새 권한 경계:** 고정 chart91.8.2는 `observability/monitoring-prometheus-token`을 `kubernetes.io/service-account-token`으로 생성하고 controller가 만료 없는 토큰을 채운다. 이 내용은 기존 최종 승인에 명시한 Grafana/내부 TLS·설정 Secret 범위를 벗어나는 신규 장기 API 자격 증명이다. 토큰이나 모니터링 리소스·Grafana 자격 증명은 아직 생성하지 않았으며, monitoring 앱 sync는 추가 리뷰·구체적 승인 전 보류한다. 이미 승인된 두 metrics 앱은 진행할 수 있다. 코드 수정 범위는 여전히 Loki datasource 제거뿐이다.

## 변경 계획: chart의 장기 Prometheus scrape 토큰 — 제안

실제 렌더링으로 발견한 `observability/monitoring-prometheus-token` 추가 권한만 검토한다. metrics-server 두 앱의 기존 승인과 실행은 유지하고, monitoring sync는 새 리뷰·사용자 승인 전 보류한다. 새 설계 선택은 [ADR0019](../adr/0019-use-chart-managed-prometheus-scrape-token.md)에 제안으로 기록한다.

- 목표: 원래 chart91.8.2가 선언한 control-plane 수집을 유지하되 만료 없는 SA 토큰 Secret 생성 여부를 명시적 승인받는다. 코드/image/PVC/설치 대상/기본 TLS/수동 sync·중단·검증 방식은 기존 계획대로다.
- 추가 승인 대상: management/observability의 Secret `monitoring-prometheus-token` 하나와 SA `monitoring-prometheus` 연결. 기본 ClusterRole/Binding의 nodes·nodes/metrics·services·endpoints·pods·endpointslices·ingresses 조회 및 metrics URL GET 권한으로 쓰이며, Secret API 리소스의 직접 조회·변경 및 cluster-admin 권한은 없다. 다만 전체 namespace의 Pod spec 조회에는 직접 설정된 환경변수/설정이 포함될 수 있다.
- 구현: 사용자가 승인하면 기존 chart 선언을 유지해 Argo가 Secret을 만들고 Kubernetes controller가 토큰을 채우게 한다. 직접 TokenRequest/토큰 추출/로그 출력/로컬 저장은 하지 않는다. TLS·설정 Secret과 이 장기 토큰을 혼동하지 않는다.
- 검증: 렌더링으로 Secret 종류/SA annotation/참조/연결 RBAC를 검사한다. 실행 후 Secret metadata와 scrape 성공만 확인하고 값은 읽지 않는다. Prometheus query/targets, 기존 Pod/PVC 보존, 대상 앱 operation/health 및 제외 앱 manual 상태를 확인한다.
- 위험/중단: 자동 만료·회전이 없는 자격 증명이다. 새 승인 전에는 monitoring sync를 하지 않는다. 보류된 Grafana 관리자 자격 증명도 아직 만들지 않는다. 미승인 시 두 metrics-server 완료 결과와 monitoring 보류를 보고한다. 토큰 폐기·회전은 별도 승인 대상이며 prune=false를 자동 폐기로 표현하지 않는다.
- 범위 변경 없음: Loki/HyperDX/OTel/다른 앱/전체 root sync, 신규 외부 노출, 기존 token/Secret 값 조회는 계속 제외한다.

변경 계획 독립 리뷰: 신규 plan-reviewer GREEN. Pod spec 조회의 정보 범위와 추가 RoleBinding/ClusterRoleBinding 확인 권고를 반영했다. 추가 사용자 승인 전 monitoring sync/토큰 생성은 계속 보류한다.

추가 검증: 해당 SA를 참조하는 다른 RoleBinding/ClusterRoleBinding으로 권한이 합산되지 않았는지 설치 전후 확인한다. 예상 밖 추가 binding이 있으면 토큰 생성/다음 sync를 시작하지 않고 보고한다.

### 장기 토큰 추가 승인 완료 — 2026-11-03

신규 plan-reviewer GREEN 후 사용자는 위의 장기 토큰 생성과 권한·만료 없음·Pod spec 정보 범위·별도 폐기 필요·값 조회/저장 제외 조건을 명시한 질문에 “토큰 생성 포함해 모니터링 진행”을 선택했다. 기존 변경 계획의 보류 조건은 해소됐다. 해당 SA를 참조하는 기존 다른 binding은 없다. 세 앱만 순차 설치하며, monitoring 토큰은 여전히 실제 sync 전에는 생성하지 않는다.
