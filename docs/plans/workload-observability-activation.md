# AWS 워크로드 자동 관측 활성화 실행 계획 C

- 날짜: 2026-11-06
- 상태: 제안. 추가 읽기 점검 승인 완료. C1 코드·확정 manifest 준비 계획은 독립 재리뷰 GREEN(두 MAJOR 반영); 사용자 C1 준비 승인 완료; override·선택 renderer·합성 명세 구현 및 로컬 검증 완료. 실제 Secret·배포 C2는 확정 diff·별도 실행 승인 전이다.
- 분류: Secret·공통 배포 계약·RBAC·네트워크·실제 rollout 영향이 있는 작업. 전체 계획/독립 리뷰/사용자 승인 적용.
- 기존 결정: [ADR0061 사설 경로](../adr/0061-private-otlp-ingress-for-aws-workload.md), [ADR0062 자동 관측](../adr/0062-automatically-observe-platform-workloads.md). 채택된 구성을 활성화하고, B에서 C로 미뤘던 인증서 수명·갱신 순서는 [ADR0063 제안](../adr/0063-rotate-observability-tls-with-public-ca-bundles.md)으로 기록한다.
- 브랜치: `feat/activate-workload-observability`. 기존 디렉터리 사용, 새 worktree/CI 없음. 사용자가 PR을 머지한다.

## 목표·완료 기준

AWS management/workload의 플랫폼 앱·CNPG·Job을 공통 계약으로 ClickStack에 연결한다. 수동 앱별 token/collector 목록은 추가하지 않는다. 처음 보는 이름의 합성 앱/DB/즉시 종료 Job으로 첫 로그, SDK trace 및 ID 연결, CNPG target up, 삭제 후 target 제거와 비소유 차단을 검증한다. 기존 앱/DB 및 관리 Prometheus/Alertmanager/Grafana가 정상이어야 한다. SDK 없는 앱의 내부 trace, GCP/온프레미스와 보호 namespace 수동 DB는 완료 대상이 아니다.

## 승인된 읽기 점검 결과

계정525091405886/ap-northeast-2, VPC vpc-04b7c6a79ad46fcdc. Secret·앱/감사 로그·exec 조회 및 변경은 하지 않았다. Pod API 원문은 메모리에서만 처리하고 env/command/args는 저장·출력하지 않았다. 요청량은 running sidecar init·일반 init peak·Pod-level requests·overhead를 고려했다.

| 대상 | 노드별 CPU 요청량 여유 | 관측 namespace | 상태 |
| --- | --- | --- | --- |
| management 3노드 | 340m / 520m / 90m | privileged PSA, quota/LimitRange 없음, 기존 queue/PVC Bound, NetworkPolicy 없음 | Pending0, 관측 Pod Ready |
| workload 2노드 | 520m / 490m | privileged PSA, quota/LimitRange/PVC 없음, system-compatibility ingress/egress allow-all | Pending0, agent/gateway/Prometheus 미설치 |

메모리 여유는 management 노드별 약3.7~4.7Gi, workload 약4.5~4.8Gi이다. 요청량 기준이며 실제 사용량이나 이후 동시 배포 여유 보장은 아니다. gp3 StorageClass 존재, private subnet IP 여유 충분, 관련 관측 NLB 없음. gp3는 양 클러스터 모두 ebs.csi.aws.com/WaitForFirstConsumer/확장 허용/Delete이며 현재 확인했다. EBS attachment quota·신규/기존 PV AZ는 실행 승인의 관련 EBS/PV 메타데이터 범위에서 적용 직전 확인한다.

실제 Argo: root는 main/수동 sync·OutOfSync이며 값에 targets/revision만 있다. 기존 관리 ClickStack/agent/gateway는44b3b46에 고정·수동 sync·Healthy. workload baseline은749c88e에 고정·수동 sync, 관리 baseline은 main/수동 sync·OutOfSync. workload KPS 앱은 Missing. 앱 ApplicationSet/플랫폼 앱은 main/selfHeal이다. **root/기존 baseline 전체 sync로 Git의 다른 변경을 반영하지 않는다.**

infra97은 머지됐다. 모임룸8은 점검 시 OPEN이며 새 SDK main image가 아직 없다. 트레이스 단계는 사용자 머지→기존 이미지 게시→실제 배포 digest 확인 후 실행한다. 현재 workload에 moimroom/docker-doom 외 moimroom-mig가 있으며 공통 eks profile을 켜면 이 환경의 플랫폼 앱도 갱신된다. 활성화 직전 대상 namespace 목록을 확정하고 진행 중인 migration/수동 고정 상태이면 rollout을 보류한다.

## 범위·변경 파일

- `clusters/aws-dev-management/values/observability-activation.yaml`: root 전용 observability override. helm/gitops 공유 기본값은 off로 유지한다. C2 root의 valueFiles에 이 파일을 추가하고 기존 target/server·다른 설정·수동 정책은 유지한다.
- `clusters/aws-dev-management/values/clickstack-workload.yaml`: 기존 clickstack.yaml 위의 전용 override로 private ingest subnet2/source10.40.32.0/19, 기존 token key 보존+WORKLOAD_OTLP_AUTH_TOKEN key 참조 추가, HyperDX query CA trust.
- `clusters/aws-dev-workload/values/baseline-observability.yaml`: 기존 baseline.yaml 위의 전용 override로 gateway만 compatibility 대상에서 제외하는 값. 실제 적용은 observability/system-compatibility NetworkPolicy 한 개만 선택한다. 다른 baseline 자원을 sync하지 않는다.
- `clusters/aws-dev-management/values/hopcharlie-apps.yaml`: eks 관측 enabled/metrics, gateway HTTPS443 endpoint/공개 PEM CA/clusterName. GKE는 off 유지.
- `clusters/aws-dev-management/values/hopcharlie-platform.yaml`: automaticJobs=true. 기존 workload·DB 설정 유지.
- 기존 alloy/gateway/boundary/minimum KPS values는 기본적으로 재사용한다. runtime endpoint/공개 CA는 아래 ConfigMap으로 공급하며 실제 token/private key는 Git에 넣지 않는다.
- 검증/운영 문서, 관련 ADR0061·0062의 적용 상태만 보완한다. IAM/Terraform state/EC2 증설/DB topology·replica·SQL·앱 LB/ALB/GCP 변경, root 전체 sync/prune/force/replace는 제외한다.

## 구체적 Secret·설정 계약과 필요한 실행 권한

승인 시 다음에만 get/create/선택 key patch/rollout 권한을 사용한다. Secret API 원문에는 다른 key가 함께 포함될 수 있으므로 필요한 key만 메모리에서 사용하고 원문/값은 출력·파일·PR·로그에 남기지 않는다. SSM parameter나 클러스터 등록 credential은 조회하지 않는다.

- management 관측 기존 `observability-client-auth`의 OTLP_AUTH_TOKEN과 `observability-client-ca`의 ca.crt: 기존 관리 경로 TLS/auth 검증에만 메모리 사용. 기존 값 변경 없음. 기존 gateway TLS/queue Secret·PVC는 그대로 참조하며 private key는 조회할 필요가 없으면 조회하지 않는다.
- management `clickstack-collector-auth`: 기존 key를 보존하며 **WORKLOAD_OTLP_AUTH_TOKEN 한 key만 추가**. 원문 조회 시 기존 CLICKHOUSE_PASSWORD도 함께 수신될 수 있으나 사용/출력/저장하지 않는다. 추가 key가 이미 있으면 덮어쓰지 않고 중단한다. resourceVersion 확인 후 key 추가, 기존 key의 해시/존재 보존 확인. 기존 OTLP_AUTH_TOKEN을 교체하지 않는다.
- workload observability namespace에 새 `observability-client-auth`(OTLP_AUTH_TOKEN), `observability-client-ca`(ca.crt), `observability-gateway-tls`(tls.crt/tls.key), `observability-upstream-auth`(CENTRAL_OTLP_AUTH_TOKEN), `observability-upstream-ca`(ca.crt), `observability-metrics-query-auth`(htpasswd), `observability-metrics-query-tls`(tls.crt/tls.key)를 생성한다. 같은 이름이 이미 있으면 소유/목적 확인 전 덮어쓰지 않는다. local agent bearer/upstream bearer/query password는 서로 다르다.
- management `clickstack-otlp-tls`에서는 중앙 **공개 ca.crt만** upstream trust에 사용한다. Secret 원문에 서버 private key가 포함되면 미사용 폐기하며 출력/저장하지 않는다. 실제 key 이름/CA 체인이 기대와 다르면 중단한다.
- workload `observability-remote-endpoint` ConfigMap에 중앙 NLB HTTPS endpoint. management `observability-metrics-query-ca` ConfigMap에 공개 CA. 앱 namespace CA는 공통 chart가 자동 생성한다.
- CA private key는 일시 메모리에서만 생성하고 leaf 발급 후 폐기한다. local gateway SAN은 observability-gateway.observability.svc(필요한 cluster.local 별칭 포함), query leaf SAN은 **실제 생성된 query NLB DNS**다. leaf90일/CA365일을 초기 유효기간으로 제안한다. CA key를 보존하지 않으므로 갱신 때 새 CA/leaf를 발급해 신뢰 bundle을 먼저 추가하고 leaf 전환 후 이전 CA를 제거한다. 앱 profile의 bundle 교체는 checksum rollout을 유발한다. C에 수명·갱신 책임을 포함해 승인받는다.
- HyperDX 기존 사용자 인증을 메모리에서 사용하고 기존 팀에 workload 전용 connection/source 한 쌍을 추가한다. 기존 source/Prometheus 연결을 삭제하거나 기존 API key를 바꾸지 않는다. connection 조회에 credential이 포함되면 출력/저장하지 않는다. 초기 fixture Secret 수정만으로 기존 팀 반영을 주장하지 않는다.
- 관측 agent/gateway/Prometheus의 metadata RBAC와 Operator의 observability 한정 Secret Role은 준비된 chart대로 적용한다. agent는 root/capabilities drop 및 노드 /var/log read-only·/var/lib/charlie-alloy write hostPath를 쓴다. 파일 reader는 소유 metadata 확정 후 생성하지만 hostPath 자체는 더 넓은 파일 접근 능력이 있다는 한계를 포함한다. Alloy storage.file public-preview 예외를 유지한다.

## 추가 리소스·비용

workload 신규: Alloy DaemonSet2 Pod, gateway1, Prometheus1(생성 StatefulSet), operator1, query proxy1 = 기본6 Pod. 초기 합계 약550m/1344Mi이며 고정 chart가 생성하는 reloader/init를 포함한 render 합계를 승인 전에 다시 기록한다. queue2Gi+Prometheus10Gi PVC2개. management는 agent3 Pod를 교체하고 gateway·중앙 collector·HyperDX를 순차 갱신하며 기존 queue/PVC를 보존한다. agent는 노드당 CPU100m로 기존과 같고 메모리128Mi 증가(총384Mi)다. 일시 surge Pod 때문에 CPU90m 노드가 막히지 않도록 기존 DS 종료 후 새 DS 생성, gateway Recreate를 따른다. 자동 증설하지 않는다.

internal NLB2개: 중앙 관리 subnet-0a35d4f954a4720cd/subnet-0d1aaecfc0e33aae8(source10.40.32.0/19), workload query subnet-01c5e9cb092c45c4d/subnet-08b2320980f9d615c(source10.40.0.0/19). AWS LBC가 신규 frontend/backend SG·target group을 소유한다. 기존 앱 LB/SG에 직접 규칙을 추가하지 않는다.

2026-11-06 공식 AWS 서울 단가 조회: NLB $0.0225/시간/개, NLCU $0.006/시간, gp3 $0.0912/GB-month. 730시간 가정 고정 NLB2개 $32.85 +12Gi gp3 약$1.09 = **월 약$33.94 + NLCU·전송·세금**. 각 LB에서 평균1NLCU를 쓰면 약$8.76/월 추가(합계 약$42.70). 교차 AZ/원격 클라우드 트래픽과 기존 저장소 증가·기존 EC2 비용은 별도다. 추정값이며 실제 청구액이 아니다.

## 순서·의존성·머지 경계

1. 계획 독립 리뷰 후 사용자에게 C 코드 준비 및 아래 실제 대상·권한·rollout·비용·복구를 명시해 승인받는다. 일반 ‘진행’/B merge/읽기 승인을 실제 변경 승인으로 간주하지 않는다. 모임룸8 머지는 사용자가 한다.
2. 승인 후 backend 준비와 최종 앱 활성화를 분리한다. 준비 PR은 eks profile와 automaticJobs를 off로 유지한다. 공개 CA·runtime Secret이 준비되지 않은 채 공통 앱 자동 rollout을 유발하지 않는다. 모든 PR은 사용자가 머지한다. 사용자 승인이 구현만 허용하면 실제 적용은 보류한다.
3. 실제 root target/helm values를 백업하되 비밀값 없는 선택 설정만 기록한다. root는 수동으로 유지한다. 승인 revision을 render해 **charlie-observability AppProject, 관측 Application들, workload 최소 KPS Application만 선택** 반영한다. root 전체 sync 금지. 기존 target endpoint와 관측 외 app syncPolicy를 바꾸지 않는다. 자동 정책이 재활성화되는 렌더 차이가 발견되면 선택 대상을 재검토한다.
4. boundary의 **RBAC/NetworkPolicy만 먼저 선택 적용**한다. query Service/ConfigMap/Deployment는5단계로 분리한다. workload baseline App의 targetRevision만 **승인된 활성화 SHA로 patch하고 기존 수동 syncPolicy/다른 Helm 설정을 보존하고 valueFiles에 baseline-observability.yaml만 추가**한다. 실제 desired diff를 확인한 뒤 observability/system-compatibility NP 한 개만 선택 sync해 gateway-labelled Pod를 제외한다. 이전749c88e revision 그대로 NP sync를 하지 않는다. **차단 정책 준비 전 app listener4319 Pod를 배포하지 않는다.** 관리에는 obs NP가 없으므로 boundary ingress가 gateway에만 적용된다. 기존 obs 동작/egress는 유지한다.
5. leaf SAN 순환 의존성 해결: query **Service만 먼저 선택** 생성해 실제 NLB DNS를 받고, 그 DNS로 leaf를 메모리 발급한 뒤 TLS/auth Secret과 query Deployment를 적용한다. 중앙 ingest Service도 중앙 collector와 같은 인증서를 TLS pass-through하며 workload exporter는 중앙 svc SAN으로 검증한다. 두 실제 NLB의 internal scheme/source SG/target health를 확인한다. LB DNS를 얻지 못하면 placeholder 인증서로 배포하지 않는다.
6. 실제 재점검 결과 workload에는 CNPG CRD가11개 있고 monitoring CRD10개는 없다. 필요한 monitoring CRD10개를 고정 KPS91.8.2 차트로 신규 설치한다. 기존 CNPG CRD는 변경하지 않는다. 적용 직전 monitoring CRD가 새로 발견되면 버전·스키마·소유권을 비교하고 호환되면 재적용하지 않는다. 기존 전역 CRD 업그레이드/소유권 전환은 승인 범위에서 제외하며 차이가 있으면 중단·재검토한다. workload gateway queue PVC/Secret을 준비하고 gateway를 배포한다. 중앙 collector는 기존 token을 보존하며 workload token만 추가하고 재시작한다. agent를 붙이기 전에 로컬/중앙 TLS/auth·잘못된 token 거부 및 큐 상태를 검증한다.
7. 관리 기존 DaemonSet의 **정확한 소유 UID와 이름 observability-agent-agent를 재확인**하고 해당 DS만 foreground 종료(새 승인 실행 범위)한 뒤 새 Alloy DS를 설치한다. 기존 PVC/positions hostPath는 삭제하지 않는다. 기존 agent/gateway의 미전송 queue가 있으면 먼저 drain하고 진행한다. old/new 두 reader의 동시 실행을 허용하지 않는다. Alloy 새 positions는 이전 OTel positions를 재사용하지 않아 최초 retained 로그 replay/중복이 있을 수 있다. 정확히 한 번 전달을 보장하지 않는다. workload는 기존 agent가 없음을 재확인 후 설치한다.
8. backend 정상 확인 후 공개 CA가 포함된 최종 eks profile·Jobs·HyperDX trust 활성화 PR을 준비한다. CA 공개 PEM만 Git에 포함한다. 이 PR merge는 selfHeal로 moimroom/docker-doom/moimroom-mig 등 **확정된 eks 앱 Pod 및 CNPG metadata/monitor**를 갱신한다. 기존 DB replica/externalClusters/certificates ignoreDifferences를 보존하고 topology diff가 있으면 중단한다. 진행 중인 migration이 있으면 활성화를 보류한다. 새 namespace가 발견되면 대상 영향 확인 후 승인 범위를 맞춘다.
9. 모임룸8 머지/기존 image publish 완료와 검증된 SDK digest를 확인한 뒤 기존 플랫폼 배포 경로로 그 앱 이미지를 갱신한다. 게시 action을 수동 실행하거나 새 CI를 만들지 않는다. 아직 머지 전이면 로그·DB metrics만 검증하고 실제 모임룸 trace 완료로 보고하지 않는다.
10. HyperDX 기존 팀에 workload Prometheus connection/source를 한 번 등록한다. 공개 CA hostname 검증·BasicAuth를 유지한다. API/UI 기존 관리 source는 보존한다. 등록 API가 현재 버전에서 지원되지 않으면 우회 DB 수정을 하지 않고 중단한다.

## 검증과 별도 데이터 접근 범위

C 실행 승인에 다음을 포함한다. owned 앱/DB/Job 노드 파일을 Alloy가 읽어 중앙으로 저장한다. 과거 retained 로그가 처음부터 유입될 수 있다. Agent가 앱 API Pod 객체를 읽을 때 inline env가 함께 수신될 수 있는 RBAC 한계를 포함한다.

운영 검증은 합성 event ID·request trace ID와 해당 합성 요청의 로그/trace·Prometheus 수집 상태만 조회한다. 기존 일반 앱 로그/감사 로그/SQL 데이터 전체 탐색은 하지 않는다. HyperDX query는 합성 event/service/시간 범위로 한정한다. Prometheus 운영 target status/큐 scalar metric은 검증한다.

- 새 임의 이름의 SDK 앱/worker 및 새 CNPG를 공통 플랫폼 배포 경로로 생성해 collector·token·monitor 수동 추가 없이 확인한다. 합성 DB는 PVC1Gi, 실제 배포 이미지 digest/requests와 namespace 이름은 렌더 후 승인 목록에 확정한다. 초기 단계는 실제 리소스 생성 승인 없이 dry render만 허용한다. 동적 테스트 자원 명세가 승인 목록에 확정되기 전 생성하지 않는다.
- 즉시 종료 합성 Job의 시작/오류/종료 event ID를 확인한다. network negative test용 합성 unowned Pod는 파일 reader/positions/중앙 export가 없어야 한다.
- metadata 지연/회전/SIGKILL queue 복구는 B 실제 binary proof를 우선 재사용한다. 실환경 agent 한 Pod 재시작은 운영 앱 readiness와 queue가 정상일 때 승인된 유지보수 범위에서 한 번만 실시한다. 노드 장애·디스크 고갈·운영 DB 장애를 주입하지 않는다.
- 실제 앱 비파괴 HTTP 요청으로 모임룸 traceparent·log native ID 연계, DB metrics target up, NLB auth/TLS·CA/hostname·source 제한·admin/write API 거부를 확인한다.
- 신규 테스트 자원 정리는 승인된 합성 namespace/PodMonitor/Job/DB/PVC만 대상으로 별도 명시한다. 기존 앱/DB/PVC 및 신규 관측 PVC는 삭제하지 않는다. 합성 삭제 후 stale target 제거를 확인한다.

## 중단·복구

Pending/attach/quota·TLS/auth·기존 앱/DB readiness·중앙 저장·namespace 정책 검증이 실패하면 다음 단계를 멈춘다. node 증설/권한 확대/인증 우회/기존 Secret 교체로 넘어가지 않는다. 새 IAM/cluster-admin, 상이한 네트워크 경로, 기존 protected DB 수집이 필요하면 새 계획/리뷰/승인을 받는다.

승인 실행 범위에 아래 한정 rollback을 명시한다. 실행 직전 관측 활성화 이전의 immutable SHA와 parent Application 전체 비밀값 없는 선택 spec/자동 정책을 기록한다. app profile/Jobs가 main/selfHeal인 상태에서 live 값만 고치지 않는다.

1. 앱 rollout 장애 시 `charlie-management-hopcharlie-apps` parent의 automated.enabled=false와 source.targetRevision=실행 직전 SHA를 설정하고, 기존 Helm parameters 중 chart.revision만 같은 SHA로 pin한다. root는 수동 유지한다. parent에서 **ApplicationSet 리소스 한 개만** 이전 profile로 선택 sync한다. 기존 확장 AppProject 권한은 cleanup 동안 유지한다. ApplicationSet이 기존 앱 source/profile을 이전 입력으로 되돌리고 각 child의 정상 reconciliation으로 복구하게 한다. parent 외 GitOps repo/child DB replica·migration ignoreDifferences는 변경하지 않는다.
2. Job 설정 문제는 platform parent를 같은 방식으로 일시 manual+이전 SHA에 고정하고 Hopai Job template ConfigMap 한 개만 이전 값으로 sync한다. 플랫폼 DB/전체 Deployment를 sync하지 않는다.
3. 원격 main은 그대로 두어 live pin이 selfHeal로 다시 켜지지 않게 한다. 이후 Git revert PR을 사용자가 머지하고 desired diff를 확인한 뒤 parent의 원래 main/자동 정책을 복원한다. 사용자의 revert merge 전 임의로 자동 정책을 복원하지 않는다.
4. 관측 backend app revision과 gateway compatibility NP 한 개는 각각 이전 승인 revision의 선택 자원으로 복구한다. 이전 baseline pin/수동 syncPolicy를 보존한다.

 compatibility 복구는 ingress 격리를 넓히는 효과가 있어 사용자에게 고지한다. 새 Alloy DS만 종료한 후 이전 OTel DS를 재생성하는 경우 소유 UID/원래 manifest·revision을 확인하고 두 agent를 동시에 켜지 않는다. 기존 token/PVC/DB/앱 데이터는 보존한다. 새 중앙 WORKLOAD key는 기존 수집기를 보존하며 제거할 수 있으나 신규 LB/SG/Secret/PVC 폐기는 별도 승인 없이 실행하지 않는다.

## 검증·기록 산출물과 미확인

**실제 C 실행 승인 전:** 선택 manifest diff, Pod/PVC/LB·권한 합계, 공개 CA 공급 방식, 합성 자원 manifest/삭제 범위, 이 계획의 독립 리뷰 결과를 완성한다. 승인 후: Helm/schema/inventory, 실제 리소스 Ready/target·TLS/auth/trace ID 증거와 실제 적용 SHA를 운영 문서·ADR0061/0062에 남긴다.

미확인: 완전한 Argo 선택 diff·controller 실제 Pod resource 합계, PV AZ·EBS quota, 기존 queue drain, actual query DNS/leaf, 기존 key 충돌 여부(Secret 미조회), 모임룸 merge/digest, migration 중 여부, 실환경 신규 앱/DB/Job acceptance. 미확인 항목을 해결하기 전에 설치 완료/사용 가능으로 보고하지 않는다.

가격 근거: [AWS ELB 가격](https://aws.amazon.com/elasticloadbalancing/pricing/), [서울 ELB 공식 price list](https://pricing.us-east-1.amazonaws.com/offers/v1.0/aws/AWSELB/current/ap-northeast-2/index.json), [EBS 가격](https://aws.amazon.com/ebs/pricing/), Pricing API AmazonEC2/APN2-EBS:VolumeUsage.gp3(2026-11-05 published, 단가0.0912).


## 독립 리뷰와 승인 분리

새 plan-reviewer의 첫 리뷰 YELLOW에서 (1)749c88e baseline pin 갱신 누락, (2)main/selfHeal profile rollback 불명확을 MAJOR로 지적했다. targetRevision만 한정 갱신+수동 정책 보존+NP1개 sync와 parent manual/immutable SHA+이전 ApplicationSet 선택 sync를 반영했다. boundary RBAC/NP 우선 부분 적용, CRD 호환성·소유권 확인/불필요 전역 변경 금지, ADR0063 인증서 운영을 보완했다.

재리뷰 판정은 **C1 코드·선택 manifest 확정 준비에 한정해 GREEN**이다. C1 승인은 실제 Secret 조회·발급·저장/클러스터·AWS 변경이나 합성 앱 생성 승인이 아니다. C1에서 backend 준비 PR(공통 앱/Job은 off), 정확한 변경 목록·증거·합성 명세를 완성한다. 그 뒤 사용자에게 대상·권한·영향이 확정된 C2 실행 승인안을 제시한다.


## C1 구현 결과

공유 기본 파일을 바꾸지 않고 root/ClickStack/baseline에 cluster 전용 override를 추가했다. root 자동 관측 모드에서 workload KPS Application만 manual로 유지해 RBAC/CRD 준비 전 자동 설치를 방지한다. API/apply 기능이 없는 renderer가 immutable SHA와 공개 target metadata를 검증하고 AppProject1+수동 Application8만 출력한다. 다른18개 root 자원은 제외한다. 최종 앱/Job flags는 off다.

`make helm-check`(기존 ClickStack15+자동 관측10+CNPG/기존 addon 검사), inventory와 diff 검사를 통과했다. 별도 적용안에 선택 필드/리소스 순서/복구/합성 namespace·Pod/DB/PVC/fixture·cleanup 명세를 기록했다. 실제 SDK image digest는 모임룸8 머지/게시 후 채워야 하며 placeholder로 실행하지 않는다. 실제 Secret/credential 발급·조회·Argo/클러스터/NLB 변경은 없었다.

관련 적용안: [workload-observability-activation](../operations/workload-observability-activation.md).
