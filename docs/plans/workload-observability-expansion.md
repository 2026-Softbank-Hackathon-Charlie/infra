# 플랫폼 워크로드 자동 Observability 연결 계획 v3

- 날짜: 2026-11-06
- 상태: v3 독립 재리뷰 GREEN. 사용자 개발·배포 승인 전.
- 결정 제안: [0060 사설 전송](../adr/0060-private-otlp-ingress-for-aws-workload.md), [0061 자동 연결](../adr/0061-automatically-observe-platform-workloads.md), 기존 [0050 ClickStack](../adr/0050-adopt-clickstack-for-hackathon-observability.md).
- 변경 분류: 공통 배포 계약·Pod/namespace 라벨·수집/인증·CNPG·Prometheus·클러스터 간 네트워크에 걸치는 영향이 큰 작업. 기존 고정 앱 목록 계획의 GREEN/읽기 승인은 이 변경안 구현 승인을 대신하지 않는다.

## 목표와 완료 기준

**플랫폼에 새 앱을 배포하면 앱 이름을 수집기 설정에 추가하거나 앱별 인증 Secret을 사람이 만들지 않아도 관측에 연결된다.** 기존 앱 두 개만 성공하는 것은 완료가 아니다.

1. 새 앱/서비스·복제본·롤링 업데이트의 stdout/stderr 로그가 자동 유입되고 app/service/namespace/cluster/version으로 구분된다.
2. 앱이 생성하는 새 CNPG Cluster도 DB 로그와 메트릭이 자동 연결된다. SQL 전문 로깅을 활성화하지 않는다.
3. 플랫폼 작업 Job/Pod의 시작·오류·종료 로그도 자동 연결된다. 앱 Pod/DB와 `workload.kind`로 구분한다.
4. 공통 chart가 모든 플랫폼 서비스에 OTLP endpoint·protocol·service/version/resource 속성·공개 CA를 자동 주입한다. SDK가 있는 새 앱은 수동 namespace 등록/토큰 복사 없이 trace를 보낸다. SDK 없는 임의 이미지의 내부 span은 자동 생성된다고 주장하지 않는다. 모임룸의 HTTP SDK를 실제 구현/검증하고, 비계측 앱의 trace는 미검증/미지원으로 구분한다.
5. HyperDX에서 앱/DB/작업 로그, 모임룸 실제 HTTP trace와 로그 ID 연결, workload Prometheus의 CNPG 메트릭을 조회한다.

첫 적용은 현재 AWS management/workload에 한정한다. GCP·온프레미스는 같은 배포 계약을 재사용할 후속 대상이며 연결 완료로 보고하지 않는다. 새 CI·임시 EKS 관리자·DB topology/데이터·노드 자동 증설·기존 앱/DB LB 변경·eBPF/자동 계측 Operator·모든 언어의 무수정 trace·DB 쿼리 span은 제외한다.

## A 읽기 점검 결과와 승인 경계

사용자가 AWS workload·관련 VPC 및 배포 앱 소스의 읽기 점검을 명시 승인했다. 환경변수 값을 제외하므로 Pod/Deployment는 서버 Table `includeObject=None`으로만 조회했다. Secret API·일반 env literal·앱/감사 로그·exec·수정은 하지 않았다. runner는 추가 PartialObjectMetadata만 조회했다.

- workload 노드2대 Ready(2a/2c), CPU allocatable3.86코어, 메모리 약13.88Gi, Pod32개/Pending0, taint 없음. **기존 requests·quota/storage 여유는 미확인**이며 설치 가능 용량을 판정하지 않았다.
- 현재 `moimroom/moimroom-web`, `docker-doom/docker-doom-web` 모두1/1 Ready. OTel agent/gateway 미설치. 앱 NP는 namespace 내부/DNS/TCP443 egress, observability는 system-compatibility allow-all이다.
- 같은 VPC `vpc-04b7c6a79ad46fcdc`; 관리 subnet `10.40.0.0/20`, `10.40.16.0/20`; workload `10.40.32.0/20`, `10.40.48.0/20`, local 경로 active. 관측 전용 internal LB 없음.
- 관리 ClickStack 네 앱은 마지막 조회 Synced/Healthy. 사용자 로그인·HyperDX PromQL23 target 정상·중앙 합성100logs/10traces·양방향 source 연결 확인. 실제 관리 앱 로그 유입은 아직 미확인.
- GitOps 배포 파일은 `apps/moimroom/eks.yaml`, `apps/docker-doom/eks.yaml`. 모임룸 조사 SHA `ca3c8f8e852ab6b57bd6390bcc7b5c69bee0ddb8`: Go1.26/net-http/slog/pgx5, OTel 의존성 없음. 수정 후보 `cmd/moimroom/main.go`, `internal/httpx/httpx.go`, `go.mod/go.sum`. 실제 배포 digest와 이 SHA의 대응은 미확인이다.
- Docker Doom 조사 SHA `e7e53a23b96a35f2a7cbef9d5a01adf6f6f93c8c`: noVNC·네이티브 게임. 로그는 대상이며 게임 내부 trace는 제공하지 않는다.
- `hop-migrate/moimroom-runner`는 label `app=moimroom-runner`, ownerReferences 없는 독립 Pod다. 실제 명령/이미지/로그·생성 주체는 미조회라 용도 확정 전이다. GitHub Actions runner로 식별한 객체가 아니다.

## B1 자동 수집 계약 — 앱 이름 목록 제거

- `hopcharlie-app`의 Namespace와 앱 Pod template에 `observability.hopcharlie.dev/enabled=true`, app/instance/service 및 `workload.kind=application`의 공통 라벨을 넣는다. 기능 활성화 후 AWS 환경 profile은 모든 플랫폼 앱에 적용하며 개별 앱 목록은 없다. 단, B 준비 PR의 모든 기존 환경 profile과 공유 기본 activation은 off로 유지한다. C 실행 승인 후 별도 activation commit/PR로 AWS 환경을 한 번 켠다. 수집 계층이 없는 환경은 off로 유지한다. 개별 앱/namespace allowlist는 두지 않는다.
- CNPG Cluster는 `spec.inheritedMetadata.labels`로 DB Pod에 공통 관측 라벨과 `workload.kind=database`를 전달한다. 앱/DB는 같은 namespace의 관측 라벨을 공유하며 CNPG operator 자체는 자동 포함하지 않는다. 새 DB마다 사람이 라벨이나 PodMonitor를 붙이지 않는다.
- `hopcharlie-platform/templates/hopai.yaml`의 Job와 Pod template에도 공통 관측·job/app/run 식별 라벨을 넣는다. 관리 agent에도 같은 자동 발견 규칙을 적용한다. runner 수집에서 자격 증명 fetch init 컨테이너는 제외하고 실제 작업 컨테이너의 로그를 수집한다.
- `hop-migrate`는 기존 작업 전용 namespace로 최초 한 번 관측 대상 등록을 제안한다. 이후 이 namespace의 작업은 이름과 무관하게 자동 수집한다. 기존 독립 runner의 실행 명령/생성 주체를 확인하기 전 SDK나 실행 방식을 수정하지 않는다. 새 플랫폼 Job은 공통 템플릿 라벨을 갖는다. `hop-migrate`는 AppProject/플랫폼의 보호 namespace 목록에 넣어 일반 앱이 그 namespace를 채택하지 못하게 한다. 임의 시스템 Pod를 자동 등록하지 않는다.
- 앱/Job/DB 공통 template·Namespace·CNPG metadata의 소유권은 기존 Argo/Application/CNPG에 유지한다. 발견 결과에 맞춰 수집기 ConfigMap을 매번 수정하거나 Pod마다 receiver를 생성하는 새 controller는 도입하지 않는다.

## B2 metadata 발견 후 파일 읽기 — 첫 로그 누락 구조 제거

노드 로그 agent만 **Grafana Alloy1.21.3 / 공식 chart1.15.0**으로 준비한다. gateway·중앙 저장·UI는 기존 OTel Collector/ClickStack을 유지한다. 새 로그 agent는 기존 node log agent와 교체하며 같은 파일을 두 agent가 읽지 않게 한다. workload에는 기존 agent가 없고 management에는 기존 agent가 있으므로 C에서 기존 agent queue drain/중단을 확인한 뒤 Alloy를 시작한다. 이전 소유 DaemonSet 등 비데이터 리소스 제거는 정확한 name/UID/diff를 C 승인에 포함한 경우에만 수행한다. hostPath/legacy positions·queue/PVC/Secret은 삭제하지 않는다. 전환 중복/손실 창을 측정·보고하며 C에서 복구 경로를 제시한다. 정확한 archive checksum/image digest는 B에서 공개 registry와 공식 release로 확인해 lock에 고정하며 부동 tag로 배포하지 않는다.

- 노드별 `discovery.kubernetes`의 pod role을 `spec.nodeName`으로 제한한다. `discovery.relabel`로 소유 Pod 라벨/명시적 작업 namespace를 검사하고 보호 namespace·관측 자체·자격 증명 fetch init를 제외한다. 이 단계가 끝난 target만 namespace/pod UID/container의 정확한 `__path__`로 `loki.source.file`에 전달한다. **file tail 후 metadata filter로 첫 로그를 drop하는 경로는 사용하지 않는다.**
- source는 발견 전 파일을 열지 않는다. metadata가 늦으면 reader 생성이 늦어지고, `tail_from_end=false`로 파일 처음부터 읽는다. Ready/Running 조건으로 대상을 버리지 않아 발견 당시 이미 Succeeded/Failed인 Pod도 파일을 읽을 수 있게 한다. CNPG/Job/app 신분은 reader 생성 때 target label에서 확정하므로 추가 enrichment 지연 때문에 ownership filter가 데이터를 버리지 않는다.
- Pod의 여러 port가 같은 container target을 만들면 namespace/UID/container/path 기준으로 deduplicate해 한 파일 reader만 만든다. CRI timestamp/멀티라인과 app/database/job 식별을 보존하고 `otelcol.receiver.loki`로 표준 OTel log를 변환해 TLS+agent bearer로 gateway에 보낸다. 이는 Alloy 내부 로그 표현 변환이며 Loki 저장소·Grafana UI를 설치하지 않는다.
- **preview 예외:** pinned Alloy1.21.3의 `otelcol.storage.file`은 public-preview다. `--stability.level=public-preview`를 명시하고 이 위험을 B 사용자 승인 및 C 실행 승인에 제시한다. 핵심 discovery/file/conversion의 GA와 혼동하지 않는다. 실제 pinned binary config validation·영속 queue 재시작 proof가 실패하면 PR/배포 완료를 주장하지 않는다.
- positions와 `otelcol.storage.file` exporter queue는 노드 hostPath의 고정 storage.path/component ID 아래에 보존한다. target 생성·롤링 hash·image version 같은 가변 label을 checkpoint 식별자에 불필요하게 넣지 않는다. 기존 OTel filelog checkpoint와는 형식이 달라 자동 호환을 가정하지 않는다. 최초 전환의 시간 경계·초기 최대 읽기량·중복 확인을 C에 명시하며 legacy checkpoint/PVC/hostPath는 삭제하지 않는다.
- 플랫폼 Job template의 완료 Pod TTL은 최소1시간으로 유지해 빠른 종료 후에도 discovery/로그 읽기 시간을 둔다. legacy runner는 삭제 정책을 바꾸지 않는다. agent 단절이 retention/파일 회전 한도를 넘으면 손실될 수 있으며 exactly-once/무손실은 보장하지 않는다. 새 Pod 생성 직후 삭제되는 비관리 작업까지 완전 수집한다고 주장하지 않는다.
- Pod/Namespace API에는 inline env 등이 포함될 수 있는 read 권한 한계가 있으나 Secret API/Pod log API 권한은 부여하지 않는다. 모델/진단에는 target 식별/상태만 출력하며 Pod 원문·인증·앱 본문은 출력하지 않는다. target 라벨/metadata를 먼저 확인하므로 비소유 파일 본문을 열고 나중에 버리는 v2의 접근 확대는 제거한다.
- **B 선행 proof:** 실제 고정 Alloy binary와 로컬 synthetic Kubernetes API/list-watch·임시 로그 파일·mock OTLP sink로 metadata5초 지연, 발견 전에 종료한 보존 Pod, 첫3개 event ID, 여러 port 중복, agent 재시작·queue restart·회전·삭제·비소유 target 미읽기를 재현한다. metadata 미확정 상태에서 reader/positions가 생기면 실패다. 이 proof와 실제 config validate가 통과하기 전 코드 PR을 완료로 보고하지 않는다. C에서는 실제 CNI/노드 파일/Pod TTL로 같은 핵심 시나리오를 재검증한다.
- CNPG JSON의 severity/sqlstate/cluster/pod를 보존하고 SQL/query/파라미터·비밀번호 필드를 제한한다. SQL 전문 로깅은 켜지 않는다. 초기 전환 시 현재 남은 허용 로그 파일을 읽을 수 있으므로 시간 경계/양을 C 승인에 명시한다.

## B3 앱 OTLP 자동 연결 — 앱별 Secret 수동 배포 제거

- 공통 chart는 AWS 환경 profile로 모든 새 앱 서비스에 HTTPS local gateway endpoint443, http/protobuf, service/version·cluster/namespace 속성, read-only 공개 CA ConfigMap mount를 생성한다. CA의 유일한 원천은 해당 환경 profile의 버전 관리되는 공개 CA PEM bundle이다. namespace가 생길 때 그 bundle로 공개 CA ConfigMap도 함께 생성된다. private key/토큰은 이 profile에 넣지 않는다. 앱마다 Secret 복사/키 발급·namespace 목록 등록은 없다. 비지원 환경 profile은 기존 Pod를 유지한다.
- local gateway는 수신 경계를 분리한다. **agent listener**는 기존 TLS+bearer4318, **app listener**는 별도 TLS4319를 Service443으로 노출하고 플랫폼 소유 namespace/Pod만 허용하는 NetworkPolicy를 둔다. app listener에는 앱별 bearer를 요구하지 않고 클러스터 내부 소유권/네트워크 경계를 사용한다. 중앙 ingest bearer는 gateway에만 둔다.
- 이 변경은 v1의 앱 bearer 방식을 대체하는 보안 결정 제안이다. app listener의 ingress는 기존 observability allow-all과 합집합이면 제한되지 않으므로, workload baseline 소유의 observability system-compatibility에서 관측 gateway 라벨 Pod만 제외하고 다른 Pod의 기존 허용은 유지한다. gateway 전용 ingress 정책은 agent4318·app4319·필요한 관측 health/metrics 흐름을 각각 허용한다. management에는 기존 allow-all이 있다고 가정하지 말고 실제 정책을 확인해 gateway 전용 정책만 준비한다. 기존 Prometheus·operator·management 수집 흐름의 허용을 먼저 렌더·리뷰하고 승인된 대상만 sync한다. Service443에 의존하지 않고 앱 guardrails에 관측 namespaceSelector와 gateway podSelector를 같은 peer로 묶은 TCP4319 egress를 명시한다. agent4318도 실제 targetPort로 허용한다. Service DNAT/CNI 경로를 C에서 실측하며 DB/앱 경로를 광범위하게 열지 않는다. workload/management 정책 차이를 각각 검증한다.
- k8s metadata에서 확인된 cluster/namespace/workload를 사용하되, app listener의 connection 기반 association과 agent file UID 기반 association을 별도 처리한다. 팀 내부 app이 허위 span을 보낼 수 있는 한계와 프로젝트별 강한 격리 부재를 기록한다. 범용 다중 테넌트 인증으로 설명하지 않는다.
- CA/public 설정 변경은 관련 앱만 정상 rollout되도록 checksum/Pod template에 반영한다. 변수 충돌/잘못된 endpoint는 렌더 단계에서 명확히 거부한다. 플랫폼 producer의 예약 env 검증도 chart와 맞춰야 하면 플랫폼 저장소의 app-spec/배포 builder에 해당 예약 변수 검증만 연계 수정하고 companion PR·계약 테스트를 포함한다. 모든 서비스의 기존 임의 env 지원과 worker/port 없는 서비스 계약을 보존한다.
- OTel SDK가 있는 새 앱은 이 설정을 자동 사용한다. SDK가 없는 임의 바이너리는 내부 span을 만들지 않는다. 모임룸에 Go HTTP SDK·W3C context·bounded batch/flush·slog trace/span ID 연결을 구현한다. 요청/응답 본문·인증 헤더·query·SQL을 span에 넣지 않는다. DB 쿼리 span과 privileged 자동 계측 Operator는 이번 범위 밖이다.

## B4 CNPG 메트릭 자동 연결

- 기존 pinned `kube-prometheus-stack91.8.2`의 workload 앱을 **CNPG 수집용 최소 구성**으로 준비한다. Prometheus1+operator1, Grafana/Alertmanager/node-exporter/kube-state-metrics/control-plane scrape/default rules/admission webhook Jobs는 끈다. 기존 관리 Prometheus/Alertmanager는 보존한다. 자동 장기 SA token Secret 생성은 명시적으로 끄고, 사용하지 않는 ServiceMonitor의 token 참조도 남기지 않는다.
- 공통 CNPG template가 각 Cluster와 함께 `PodMonitor`를 생성한다. namespace/cluster label selector, metrics port9187, 보수적인 scrape interval30s를 고정하고 Prometheus는 공통 관측 namespace/monitor label로 새 DB를 발견한다. deprecated `enablePodMonitor` 자동 생성에 의존하지 않는다. 앱 AppProject에는 PodMonitor 한 종류만 필요한 범위로 추가한다.
- 앱 chart/필요한 기존 DB policy에 obs Prometheus Pod→CNPG metrics9187만 허용한다. PostgreSQL5432·API/replication·DB topology·SQL은 바꾸지 않는다. opt-out/Prometheus CRD 없는 환경에는 PodMonitor를 렌더링하지 않는다.
- Prometheus 초기 보존7일/PVC10Gi, 요청100m/512Mi·메모리 limit1Gi를 제안한다. 실제 고정 chart의 전체 requests/sidecar·CRD/RBAC/volume는 렌더 후 C에 제시하며 quota/여유 미확인 시 설치하지 않는다.
- HyperDX 조회는 workload Prometheus 앞의 **TLS+BasicAuth query proxy**(NGINX unprivileged 최소 query proxy; 새 의존성이며 B에서 공식 안정 버전·image digest를 확인해 lock에 고정)와 전용 internal NLB를 사용한다. NLB source는 관리 subnet 두 CIDR만 허용, proxy는 쿼리 API 허용 경로만 노출하고 admin/lifecycle/write는 거부한다. 최소 proxy 요청50m/64Mi·limit128Mi를 제안한다.
- query 인증은 한 번의 클러스터 onboarding에서 Secret으로 만들고 HyperDX connection에 등록한다. 새 DB마다 connection/credential을 추가하지 않는다. private NLB DNS를 얻은 후 실제 DNS SAN의 서버 인증서를 발급하고 중앙 HyperDX에 공개 CA를 mount해 기본 hostname 검증을 유지한다. 인증서 검증 해제/무인증 공개는 하지 않는다. 기존 HyperDX 팀에는 인증 API로 새 connection/source를 등록하며 초기 fixture Secret만 바꿔서 반영됐다고 주장하지 않는다.

## B5 사설 전송·변경 파일과 권한

- **internal NLB 총2개**: 관리 중앙 OTLP443→4318(source workload10.40.32.0/19), workload metrics-query443→proxy TLS(source 관리10.40.0.0/19). AWS LBC가 target group/SG를 관리한다. 기존 ClusterIP/앱·DB LB는 유지한다. 두 NLB의 시간·처리량 요금, PVC 총12Gi(queue2Gi+Prometheus10Gi)가 추가된다. 정확한 비용은 C 실제 리전/단가/처리량 가정으로 제시한다.
- workload upstream ingest 토큰만 중앙 기존 collector Secret에 추가하고 기존 관리 token을 보존한다. agent local bearer와 upstream token은 분리한다. app CA/metrics query CA·TLS Secret·query credential은 역할별로 구분하며 앱에는 공개 CA만 배포한다. CA private key는 메모리에서만 쓰고 서버 발급 후 폐기한다. 실제 Secret 이름/key·일관성·유효기간·query SAN·rotation은 구현 결과와 C 승인 목록으로 제시한다.
- infra 수정: `helm/gitops/templates/observability.yaml`/schema, `helm/charts/clickstack/` private Service/auth, 관리/workload gateway OTel values 및 신규 alloy.yaml values, `helm/versions.json`·`helm/images.lock.json` Alloy pin, AppProject의 공식 Grafana Helm repo 한 개 추가, workload CNPG 최소 Prometheus 전용 신규 values(기존 kube-prometheus values는 B에서 유지), 최소 metrics-query chart/values, `hopcharlie-app` Namespace/Deployment/CNPG/PodMonitor/guardrails/schema, `hopcharlie-apps` profile/AppProject, `cluster-baseline` 관측 namespace 정책, `hopcharlie-platform` Job labels 및 관리 자동 수집 설정. 관련 tests/README/ADR.
- 앱 수정: moimroom 별도 브랜치의 `go.mod/go.sum`, main/httpx, 새 internal/telemetry 및 테스트. 새 CI는 만들지 않고 기존 이미지 게시 경로를 사용한다. 소스 최신 SHA/동시 작업과 실제 digest 대응을 다시 확인한다.
- agent/Prometheus는 필요한 pod/namespace/service metadata list/watch를 사용한다. Kubernetes Pod 객체에는 inline env가 섞일 수 있는 read 권한의 실제 한계를 C에 명시하며 agent/Prometheus server에 Secret API 직접 read는 자동 부여하지 않는다. 별도 Prometheus Operator controller의 기본 RBAC에는 Secret 읽기가 필요할 수 있으므로 B에서 고정 chart의 실제 범위와 namespace 제약을 분석하고 C에 별도 권한으로 제시한다. 전체 구성에 Secret read가 없다고 보고하지 않으며 불필요한 전체 namespace Secret 권한을 그대로 배포하지 않는다. 새 IAM/cluster-admin이 필요하면 중단/재검토한다.
- 첫 workload cluster 연결과 기존 앱·작업 namespace의 관측 등록은 한 번 수행한다. 이후 플랫폼 새 앱/DB/Job은 **공통 template만으로** 붙으며 Collector 설정·수동 credential·개별 PodMonitor/HyperDX connection 작업이 없어야 한다.

## B6 구현 순서와 자동화 검증

1. 사용자 B 개발 승인 후 기존 infra 디렉터리 내 구현 브랜치에서 작업한다. B는 모든 기존 activation/profile을 off로 유지하고 기존 앱·Namespace·정책·PodMonitor 렌더가 불변이어야 한다. 새 collector/metrics 앱도 off gate 뒤에 둔다. main selfHeal과 ApplicationSet이 B merge만으로 rollout하지 않도록 계약 검사를 둔다. 추가 git worktree 없이 모임룸 source도 별도 브랜치로 준비한다. 리뷰 범위를 넘어서는 새로운 IAM/tool/state 변경은 하지 않는다.
2. 공통 labels/CA·OTLP 설정과 CNPG PodMonitor/최소 Prometheus, 수집기 ownership filter·query proxy/사설 경로를 코드로 준비한다. 기존 default/비지원 환경 Pod template·앱/DB contract 회귀 검사를 한다. baseline 정책 교체는 기존 관측 통신 허용표와 함께 리뷰한다.
3. 고정 SDK/Alloy/Collector/NGINX/Prometheus 버전으로 실제 config validation을 포함한다. 기존 Helm·ClickStack·schema·inventory, Go in-memory exporter·context propagation·flush·ID 연결·Secret 미렌더링·CA mount 검사를 수행한다. proxy는 query 성공과 무인증/잘못된 인증/TLS/금지경로 거부를 검사한다.
4. 핵심 acceptance: **한 번도 등록한 적 없는 앱 이름**으로 배포→즉시 첫 로그/trace(SDK 앱) 확인; 같은 앱에 service 추가/scale/rollout; 새 CNPG→로그+PodMonitor target+DB metrics; 즉시 종료 Job→시작/오류/종료 event ID; agent 재시작·metadata 지연·회전; metadata 없는 비소유 Pod/system namespace 데이터의 export/queue 부재; 삭제 후 stale scrape/중복 부재를 확인한다. 이를 기존 앱2개 검사로 대체하지 않는다.
5. B 코드·검증·PR 준비까지 완료하고 사용자가 머지한다. 이 merge는 기능 준비만 포함한다. C 실행 승인 후 별도 activation commit/PR을 만들며, 사용자가 그 activation PR을 머지하는 순간 발생할 main 추적/selfHeal rollout도 C 승인 대상이다. C 실행은 실제 manifest diff·Secret/key 목록·Pod/PVC/LB 수·비용·권한·기존 정책 영향·테스트 경로/데이터를 제시해 별도 승인받는다. **B는 실제 설치·credential 생성 승인이 아니다.**

## C 실행 선행 조건·순서·복구

- 승인 대상: 양 클러스터 resource requests(init/상주 sidecar/overhead 포함)·quota/LimitRange/StorageClass/PVC·PodSecurity/hostPath·인증서 만료/노드 여유 읽기, 선택 Secret get/create/key 추가, 공통 chart·정책·관측 수동 Argo sync, NLB/SG reconcile, 실제 앱 비파괴 요청/합성 Job·검증. Kubernetes API 원문 일시 수신 및 허용된 노드 파일·초기 로그 읽기 범위를 함께 설명한다. 기존 A로 확대 실행하지 않는다.
- baseline compatibility 변경 전 기존 control-plane/API·DNS·Prometheus·operator·관리/앱 dataflow별 허용/차단표와 복구 manifest를 마련한다. 어떤 기존 flow인지 확인하지 못하면 배포하지 않는다. source range만으로 내부 ingress 차단이 된다고 주장하지 않는다.
- CRD/Prometheus 준비→공개 CA/선택 Secret→app label/PodMonitor/제한 정책(필수기존허용유지)→중앙/로컬 gateway/query proxy→실제 internal NLB/SG/target health/TLS→agent→공통 앱/DB/Job rollout→HyperDX connection→신규이름 acceptance 순서로 적용한다. 세부 sync 순서는 실제 dependency render로 확정한다. root 전체 sync/prune/force는 하지 않는다.
- 새 앱 자동 연결에 사람이 app 이름/토큰/PodMonitor를 추가해야 하거나 첫 Job 로그/비소유 차단을 통과하지 못하면 완료 처리하지 않는다. node 여유/스토리지·TLS·auth·기존 readiness가 실패하면 다음 sync를 중단한다. 노드 자동 증설은 없다.
- 장애 시 해당 앱/관측/정책을 이전 승인 Git 입력으로 수동 복구한다. 특히 이전 관측 compatibility를 되돌리는 것은 격리를 넓힐 수 있으므로 복구 영향도 C 승인에 포함한다. DB/앱 데이터·PVC·기존 관리 Secret은 삭제하지 않는다. 새 LB/SG/Secret/PVC 삭제는 별도 승인 후 소유 리소스만 처리한다.

## 현재 미확인·리뷰 기록

현재 requests/quota/storage 여유, Alloy metadata 발견/짧은 Job positions·queue 실제 동작, 보호 namespace 실제 목록, baseline flow inventory, NGINX auth/CA config와 HyperDX query trust, 최종 immutable image pins/SDK 버전, CNPG inherited labels/monitor schema 및 신규 앱 acceptance는 미검증이다. B에서 코드/설정 실현 가능성을 검증하고 C 실환경 관문을 통과하기 전 설치/완료를 주장하지 않는다.

v2 독립 리뷰 YELLOW의 targetPort·B merge 경계·첫 로그 누락 지적을 반영해 v3로 수정했다. v3 재리뷰 GREEN: 세 MAJOR와 CA 원천·agent 전환·storage public-preview 고지 지적을 반영했고 중대한 미해결 지적은 없다. B 실제 binary proof 실패 시 코드 준비 완료/자동 수집 완료로 보고하지 않는 조건을 유지한다. 사용자 요청은 계획 재작성·리뷰이며 구현은 시작하지 않았다.

## v3 검토 근거

v0.161.0 k8s_observer의 `pod_endpoint.go`는 Running이 아닌 Pod의 endpoint를 반환하지 않아 즉시 종료 작업의 기본 경로로 채택하지 않았다. k8sattributes wait_for_metadata는 초기 동기화 관문이며 새 Pod마다 파일 읽기를 보류하는 메커니즘이 아니다. 따라서 소유 metadata 확인 후 파일 reader를 생성하는 Alloy의 GA discovery/file/conversion을 선택하고 queue storage만 public-preview 예외로 명시한다. 기존 image lock에 Envoy가 있다는 v2 서술은 잘못돼 제거했으며 query proxy는 새로운 NGINX 의존성으로 명시한다.

- [Alloy Kubernetes discovery](https://grafana.com/docs/alloy/latest/reference/components/discovery/discovery.kubernetes/)
- [Alloy file source](https://grafana.com/docs/alloy/latest/reference/components/loki/loki.source.file/)
- [Alloy OTel log conversion](https://grafana.com/docs/alloy/latest/reference/components/otelcol/otelcol.receiver.loki/)
- [Alloy OTel HTTP exporter/storage](https://grafana.com/docs/alloy/latest/reference/components/otelcol/otelcol.exporter.otlphttp/)
- [Alloy1.21.3 release](https://github.com/grafana/alloy/releases/tag/v1.21.3)
