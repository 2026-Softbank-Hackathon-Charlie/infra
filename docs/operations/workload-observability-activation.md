# AWS 자동 관측 활성화 적용안

2026-11-06. **준비 코드 C1**이다. 실제 Secret/Argo/클러스터/NLB 변경 C2는 아직 승인·실행하지 않았다. 준비 PR은 사용자가 머지한다. 기존 공통 앱 profile/Job 및 root·baseline·ClickStack 기본 파일은 off 상태를 보존한다.

## override와 적용 경계

| 파일 | C2에서 연결할 대상 | 효과 |
| --- | --- | --- |
| management values/observability-activation.yaml | charlie-root의 valueFiles 마지막에 `../../clusters/aws-dev-management/values/observability-activation.yaml` 추가 | 관리/workload 관측 앱 렌더 |
| management values/clickstack-workload.yaml | automatic 모드의 ClickStack Application에 기존 clickstack.yaml 다음으로 자동 연결 | 중앙 ingest NLB, workload token key 참조, HyperDX 공개 CA trust |
| workload values/baseline-observability.yaml | workload baseline App valueFiles에 `../../../clusters/aws-dev-workload/values/baseline-observability.yaml` 추가 | obs compatibility에서 gateway Pod만 제외 |

`management values/`는 `clusters/aws-dev-management/values/`, `workload values/`는 `clusters/aws-dev-workload/values/`를 뜻한다. 파일을 추가만 해서는 배포되지 않는다. 기존 root는 수동이고 중앙/agent/gateway는44b3b46, workload baseline은749c88e에 고정돼 있다. 적용 직전 값이 달라졌으면 현재 승인 범위와 다시 비교한다.

```sh
HELM=/path/to/helm python3 scripts/render-observability-activation.py \
  --revision <검증한-전체40자리-SHA> \
  --targets-file /private/tmp/public-observability-targets.json \
  --output-dir .generated/observability-activation
```

입력은 `management`/`workload` 각각 endpoint/name/region/vpc_id만 있는 공개 metadata JSON이다. kubeconfig/credential/Secret 값을 넣지 않는다. 도구는 API 접속 없이 고정 SHA로 render하고 allowlist 밖 자원을 제외한다. 출력은 AppProject1+수동 Application8 및 공개 root render 입력이다. **root-runtime-values.json을 기존 valuesObject 전체에 덮어쓰는 용도가 아니다.** 현재 root의 다른 값을 보존하고 아래 필드만 한정 변경한다.

- root의 source.targetRevision 및 valuesObject.revision: 검증한 immutable SHA. 기존 수동 syncPolicy/targets/parameters 유지. 기존 observability parameter/valuesObject가 새 override를 덮어쓰면 중단한다.
- root valueFiles: 위 override를 추가. root 전체 sync 금지. 아래9개 객체만 선택 반영한다.
- baseline source.targetRevision: 같은 승인 SHA, valueFiles에 baseline override만 추가. 기존 수동 syncPolicy를 보존하고 NP 한 개만 선택 sync한다.

| 허용 root 객체 | 종류 |
| --- | --- |
| charlie-observability | AppProject |
| charlie-management-clickstack | Application |
| charlie-workload-kube-prometheus-stack | Application(automatic 모드에서 수동) |
| charlie-management-observability-agent / charlie-workload-observability-agent | Application |
| charlie-management-observability-gateway / charlie-workload-observability-gateway | Application |
| charlie-management-observability-boundary / charlie-workload-observability-boundary | Application |

이외 root 렌더 자원은 모두 제외한다. ClickStack operator·기존 관리 KPS·일반 addon·플랫폼·앱 ApplicationSet은 root 선택 반영 대상이 아니다.

## 리소스별 적용 순서

1. backend boundary에서 **metadata/metrics RBAC와 NetworkPolicy만** 먼저 선택 sync한다. workload baseline pin/override를 맞춘 뒤 observability/system-compatibility NP만 적용한다. 다른 baseline drift는 적용하지 않는다. management obs에는 기존 NP가 없어 boundary가 gateway에만 적용된다.
2. 중앙 ingest와 workload query Service만 먼저 생성해 두 internal NLB의 실제 DNS를 확인한다. query NLB DNS SAN의 TLS leaf·auth Secret 준비 후 query ConfigMap/Deployment를 배포한다. 실제 Secret 생성은 C2 승인 대상이다.
3. 중앙 ClickStack에서는 private Service, custom-config ConfigMap, collector Deployment만 선택한다. 기존 token key를 보존하고 WORKLOAD_OTLP_AUTH_TOKEN만 추가한다. MongoDB/ClickHouse/Keeper/PVC/operator를 전체 sync하지 않는다. query CA ConfigMap 준비 후 HyperDX Deployment만 선택해 신뢰를 추가한다.
4. workload monitoring CRD10개가 실제 없는 것을 확인했다. 고정 KPS 차트로 필요한 CRD를 신규 설치하며 기존 CNPG CRD11개는 건드리지 않는다. 그 사이 monitoring CRD가 생겼으면 소유권/스키마 확인 전 적용하지 않는다. metrics Role/SA가 준비된 뒤 operator/Prometheus를 적용한다.
5. workload gateway PVC2Gi/Secret 및 gateway를 준비한다. 중앙/로컬 TLS·auth·query 경로가 정상인지 확인한다.
6. 관리 old `observability-agent-agent` DS의 queue drain/UID를 확인하고 해당 DS만 종료한다. 새 `observability-alloy` DS를 배포한다. old/new를 동시에 실행하지 않는다. 이전 positions format은 재사용하지 않으므로 retained 파일 replay/중복이 있을 수 있다. workload에는 새 Alloy를 설치한다. gateway 기존 PVC는 유지한다.
7. backend 정상 후 **별도 앱 활성화 PR**에 공개 CA와 eks profile/automaticJobs를 담는다. 이 PR이 main/selfHeal로 기존 moimroom/docker-doom/moimroom-mig와 그 시점 확정된 eks 앱을 갱신한다. migration 중이면 보류한다. 이 준비 PR에는 앱 활성화가 없다.
8. 모임룸8 사용자 머지→기존 게시 workflow→SDK image digest 확인 후 모임룸을 배포한다. HyperDX에 새 workload connection/source를 한 번 등록한다. 기존 관리 source는 유지한다.

## 합성 검증 명세

C1은 아래 제한된 명세를 준비한다. 실제 C2 승인을 요청할 때 SDK digest·공개 CA·적용 SHA로 최종 render한 manifest를 첨부한다. **미확정 image/CA placeholder가 남은 manifest는 생성/배포하지 않는다.**

| 자원 | 고정 이름·사양 | 접근·데이터 |
| --- | --- | --- |
| 공통 chart Argo Application | obs-proof-20261106, project hopcharlie-apps, 수동 sync, workload destination | 실제 common hopcharlie-app chart와 공개 profile 사용 |
| owned Namespace | obs-proof-20261106 | 없을 때만 생성, 원래 있으면 중단; common chart label/NetworkPolicy/CA |
| 신규 SDK web | app obs-proof, service web, port8080, healthCheck /readyz, replicas1→2, CPU50m/memory128Mi 각 replica | 모임룸8 게시 image의 실제 immutable digest를 필수 입력. SEED_SPACES=false, S3_ENDPOINT=http://obs-proof-s3:8080, S3_BUCKET=obs-proof, S3_REGION=us-east-1, S3_PATH_STYLE=true. 실제 S3 credential/운영 데이터 미사용 |
| 추가 SDK service | 같은 common app의 worker, port 미지정, replica1, CPU50m/memory128Mi | 같은 image와 합성 DB/S3 설정. Service/probe 없이 동작하는 공통 계약 검증 |
| 신규 CNPG | obs-proof-db, instance1, CPU10m/memory256Mi, gp3 PVC1Gi | common chart postgres resource; 자동 inherited labels/PodMonitor/9187 정책. 새 합성 DB에 모임룸 초기 schema만 생성 |
| S3 readiness fixture | obs-proof-s3 ConfigMap/Deployment/ClusterIP Service, 1replica, CPU10m/memory32Mi(limit64Mi), nonroot101, SA token 미mount | 기존 pinned NGINX1.30.6 digest 재사용. listen8080, HEAD /obs-proof만200, /healthz200, 기타 method/path405/404. 실제 저장·클라우드 호출 없음. read-only config, /tmp emptyDir. 모임룸 BucketExists/readiness를 위한 mock임을 명시 |
| 즉시 종료 Job | obs-proof-immediate, backoff0, ttl3600, CPU10m/memory16Mi(limit32Mi) | pinned busybox1.38.0 digest(기존 image lock)로 event_id 세 개 start/error/end stdout 후 exit1; owned job label, 토큰 없음 |
| negative Namespace/Pod | obs-unowned-proof-20261106/negative, CPU10m/memory16Mi(limit32Mi) | 동일 busybox, 관측 ownership label 없음. 명확한 합성 event만 출력. gateway4319 통신 거부를 확인할 때 test namespace에 DNS/해당 gateway로만 egress 허용 NP를 명시해 client-side default deny와 혼동하지 않는다. 일반 앱 로그/Secret 사용 없음 |

S3 fixture는 신규 프로덕션 backend가 아니라 테스트용 모의 HEAD endpoint다. /readyz를 /healthz로 대체해 의존성 실패를 숨기지 않는다. 추가 테스트 요청량은 web2+worker1(150m/384Mi), DB10m/256Mi, fixture10m/32Mi, Job+negative20m/32Mi = 최대190m/704Mi와 임시 PVC1Gi다. 실제 image init/Pod overhead는 최종 render에서 다시 합산한다. backend550m를 설치한 뒤 남는 약460m CPU 요청량 안에서 실행하되 다른 동시 배포가 생기면 재확인한다.

모임룸 SDK image는 현재 PR8 미머지라 digest 미확정이다. SDK fixture가 준비되지 않아도 backend 자체는 준비할 수 있으나 **실환경 신규 SDK 앱 검증 완료로 보고하지 않는다.** 고정 image는 renderer/manifest의 실행 전 검증에서 `@sha256:<64hex>`와 해당 앱 repository 대응을 확인한다.

합성 event ID·trace ID로 시간/서비스를 좁혀 HyperDX에서 조회한다. log/trace native ID와 CNPG scrape up, 최초 Job 세 event 및 삭제 후 stale target을 확인한다. negative Pod의 stdout은 Kubernetes의 해당 합성 Pod log만 읽어 발생을 확인하고 중앙에서는 미유입을 확인한다. 운영 앱/감사 로그를 전체 조회하지 않는다. Pod exec와 노드/운영 DB 장애 주입은 하지 않는다.

테스트 삭제 승인 범위는 **없던 두 합성 namespace, obs-proof Application 및 그 안의 합성 DB/PVC/PodMonitor/fixture/Job/Pod**뿐이다. 생성 시 UID/owner/manifest hash를 기록하고 다른 자원이 섞이거나 원래 존재한 namespace이면 삭제하지 않는다. 운영 관측 PVC·기존 DB/Namespace를 삭제하지 않는다.

## 용량·비용·복구

추가 workload backend는6 Pod/CPU550m/메모리1344Mi +PVC12Gi다. 관리 agent3개 교체는 CPU 변화0, 메모리384Mi 증가다. 현재 요청량 기준으로 노드 증설 없이 준비한다. 실제 usage/추가 동시 배포 여유는 보장하지 않는다. 생성 reloader는 고정 operator의 실제 CPU/memory request args를 확인한다.

NLB2개+12Gi gp3는730시간 가정 고정 약$33.94/월, NLCU·전송·세금 별도다. 각 NLB 평균1NLCU 가정이면 약$42.70/월이다. 임시 합성 PVC1Gi는 유지한 시간만 추가된다. [공식 ELB 가격](https://aws.amazon.com/elasticloadbalancing/pricing/), [EBS 가격](https://aws.amazon.com/ebs/pricing/).

실패 시 후속 단계를 중단한다. 수동 관측 Apps는 이전 pin으로 선택 복구한다. main/selfHeal 앱은 parent를 manual+이전 immutable SHA/chart.revision으로 pin한 뒤 ApplicationSet1개를 이전 profile로 선택 sync한다. 기존 확장 AppProject는 cleanup 동안 보존한다. 사용자 Git revert merge 전 원래 main/자동 정책을 복원하지 않는다. 상세 범위는 [리뷰된 계획](../plans/workload-observability-activation.md)을 따른다. DB/PVC/기존 token은 보존하고 신규 LB/SG/Secret/PVC 폐기는 별도 승인 없이 하지 않는다.
