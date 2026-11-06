# EKS 자동 관측 최종 활성화 C3

- 날짜: 2026-11-07
- 상태: 실행 승인 전. PR #119 준비 검토 대상.
- 분류: 기존 앱 Pod rollout, 소유 로그 저장, CNPG metadata/monitor와 NetworkPolicy 변경을 수반하므로 영향이 큰 작업이다. 기존 C1/C2의 후속이며 실제 대상 diff를 확정하고 독립 리뷰 후 승인받는다.
- 관련 결정: ADR0061(사설 전송), ADR0062(플랫폼 자동 등록), ADR0063(TLS 회전). 신규 도구·저장소·권한 결정을 추가하지 않는다.

## 목표와 완료 기준

EKS 공통 profile과 관리 플랫폼의 신규 Job 등록을 활성화해 새 앱에 수집기 수동 등록이 필요 없게 한다. 기존 EKS 앱 3개의 stdout 등록, CNPG 2개의 scrape 상태, 신규 Job template label/TTL을 검증한다. SDK 없는 앱에 trace가 생겼다고 보고하지 않는다. HyperDX 외부 Prometheus UI의 401은 별도 결함으로 분리한다.

## 대상과 실제 영향

- PR #119의 EKS profile: enabled/metricsEnabled=true, 실제 공개 CA, 로컬 TLS gateway endpoint와 cluster 식별 정보. GKE profile와 root 공유 기본값은 off 유지.
- 앱: docker-doom-eks, moimroom-eks, moimroom-mig-eks. 현재 main/selfHeal + prune=true를 사용하므로 **사용자 merge 자체가 실제 rollout 트리거**다. root manual은 이를 차단하지 않는다.
- Deployment 3개: docker-doom-web/moimroom-web/moimroom-mig-web. 관측 Pod label, OTEL 환경변수, 공개 CA volume/mount/checksum 추가. 기존 env 값·image·requests/limits는 유지하는지 메모리에서 비교한다.
- Namespace 3개에 소유 관측 label, CA ConfigMap 3개, 로컬 gateway로만 허용하는 OTLP egress NetworkPolicy 3개.
- moimroom-db/moimroom-mig-db: spec.inheritedMetadata 관측 label만 추가, PodMonitor 2개와 metrics ingress NetworkPolicy 2개. instances/storage/bootstrap/replica/externalClusters/certificates는 변경하지 않는다. 기존 ignoreDifferences와 RespectIgnoreDifferences를 유지한다.
- 관리 플랫폼 automaticJobs=true: ConfigMap의 이후 생성 Job template에 관측 label/보존 TTL을 적용한다. Job 생성 자체를 실행하지 않는다. API의 파일 재읽기/캐시 여부가 확인되지 않으면 신규 Job 자동 등록 완료로 보고하지 않는다.
- workload KPS: 기존 live 임시 Helm override 3개를 수정된 Git ref와 동등하게 수렴시킨다. values ref 갱신과 일치하는 override 제거를 단일 JSON patch로 수행한다. 다른 parameters/manual 정책은 보존한다. Operator Deployment만 필요 시 선택 sync한다.

## 사전 증거와 남은 관문

2026-11-06 15:06 UTC 실제 확인: 앱 3개 Synced/Healthy, Deployment 각 ready1/desired1, 두 CNPG readyInstances1·healthy·currentPrimary=targetPrimary, 진행 중 Kubernetes Job0. 실제 main SHA 10361ee3b7c3c1ba8bb35dd4bc31245a72d21814, GitOps SHA 55ea56a5215bb2ece26ffa6be7551936b41573a7. 시점 자료이므로 merge 직전 재확인한다. 플랫폼 엔진의 진행 중 migration은 Kubernetes Job0만으로 부재를 확정하지 않는다. 별도 상태 확인 또는 운영자의 migration 중지/미진행 확인이 있어야 merge한다. 사용자가 이번 점검에서 진행 중 migration이 없다고 명시적으로 회신했다.

실제 profile을 비밀값 없는 선택 파일로 보존하고 GitOps 값을 메모리에서 render했다. `.generated/observability-c2-76566a3/activation-app-render-diff.json`은 변경 경로만 기록한다. 일반 env 값·Secret·앱/감사 로그·전체 manifest는 출력/저장하지 않는다. 최신 main을 브랜치에 병합해 다른 기능 변경을 보존한다.

## 실행 순서와 필요한 권한

1. 로컬 Helm/계약/inventory 검사, 실제 profile 대조, 앱 diff/신규 리소스 목록과 독립 리뷰를 완료한다.
2. 사용자에게 본 대상·Pod 재시작·소유 로그 수집·실제 권한 범위와 복구를 제시한다. 사용자가 명시적으로 이 영향과 실행을 승인한 뒤 PR #119를 직접 merge한다. Agent는 merge하지 않는다. migration 미진행이 확인되지 않으면 Draft 유지.
3. 승인 후 merge SHA 확인, main/selfHeal 자동 반영 상태 관측. root 전체 sync·prune/force/replace는 하지 않는다. KPS는 위 한정 JSON patch/선택 sync만 한다.
4. Deployment/DB/agent/gateway Ready, namespace label·PodMonitor 2개/Prometheus target status와 queue scalar를 확인한다. Secret 값·일반 앱 로그·SQL/감사 로그는 조회하지 않는다. 신규 token/IAM/SG/NLB/PVC/노드/DB topology 변경은 없다.
5. 앱 3개 기존 stdout의 수집기 target·exporter counter를 확인한다. 중앙 일반 로그 내용을 탐색하지 않는다. 실제 SDK trace는 모임룸 #8 사용자 merge·게시된 image 배포 이후 별도 합성 요청으로 검증한다. 새 합성 namespace/Pod/DB/Job/PVC 생성·삭제는 이 C3에 포함하지 않는다.
6. 이 PR은 API/엔진에서 기존 migration 실행을 시작하지 않는다. 신규 Job 생성 검증이 별도로 승인되기 전 template 등록까지만 보고한다.

## 위험과 복구

앱 RollingUpdate에서 임시 surge가 생긴다. 처음 등록된 소유 앱의 retained stdout은 처음부터 replay되어 중복이 유입될 수 있으며 exactly-once를 보장하지 않는다. Pending/Ready 감소·DB 상태 변화·TLS 실패·queue 증가가 있으면 후속 단계를 중단하고 승인한 대상만 복구한다. 요청량/replica 증가가 없더라도 동시 배포 capacity는 merge 직전 재확인한다.

C3 승인에는 한정 복구를 포함해 요청한다: main/selfHeal parent의 비밀값 없는 source/자동 정책을 기록하고 관측 활성화 이전 SHA에 manual pin한 뒤 ApplicationSet 1개와 해당 EKS 앱의 관측 설정만 이전 profile로 되돌린다. 플랫폼은 automaticJobs 이전 값으로 해당 ConfigMap만 복구한다. 기존 DB/PVC/Secret/관측 데이터 및 GKE 자원은 삭제하지 않는다. rollback의 ApplicationSet 렌더에서도 EKS 관측 설정 외의 필드와 GKE 설정이 불변인지 검사하고 차이가 있으면 중단한다. rollback에서 protected DB 필드 차이가 보이면 적용하지 않는다. 사용자가 Git revert를 merge한 뒤에만 원래 main/자동 정책을 복원한다. 상세 기존 복구 절차는 workload-observability-activation.md를 따른다.

## 별도 미완료: HyperDX 외부 Prometheus 인증

고정 2.39.1 API는 외부 Prometheus 분기에서 connection.username/password를 Authorization으로 전달하지 않는다. 최신 main은 URL userinfo를 Basic header로 변환하지만 저장된 credential 필드 전달을 해결한다고 확인하지 못했다. 따라서 단순 업그레이드나 URL에 password 삽입을 C3에 포함하지 않는다.

권장 후속 최소 해결안은 현재 고정 버전에서 connection credential을 Basic header로 전달하는 API 수정, 합성 인증/오류 비밀값 비노출 테스트, 고정 base 기반 패치 이미지 작성이다. 이미지 게시 위치·빌드/배포 방식·권한과 재시작 범위를 확정한 별도 계획 리뷰·승인 후 구현한다. 새 CI·sidecar·인증 해제는 이번 계획에 포함하지 않는다. 이 계획은 해당 이미지 변경 실행 승인을 요청하지 않는다.


## 확정 검증 및 독립 리뷰

- 새 plan-reviewer `review_observability_c3_merge`: GREEN, CRITICAL/MAJOR 없음. 최초 retained 로그 replay 안내와 rollback의 EKS 외/GKE 불변 검사 MINOR를 반영했다. 사용자 실행 승인을 대신하지 않는다.
- 실제 profile의 observability block은 off일 때 ApplicationSet에서 생략됨을 확인했다. 이를 제외한 live profile과 main profile은 일치한다. GitOps SHA55ea56a에서 기존 일반 env·image·requests/limits 불변, CNPG spec.inheritedMetadata 외 spec 불변, credential 리소스 불변을 확인했다. 앱 image 변경은 이 활성화에 포함하지 않는다.
- 신규 리소스: CA ConfigMap3, OTLP egress NetworkPolicy3, CNPG metrics NetworkPolicy2, PodMonitor2. 변경: Deployment3, Namespace3, CNPG inheritedMetadata2와 플랫폼 Job template ConfigMap. DB topology·Secret/GKE는 불변이다.
- 요청량 여유: workload 노드별 CPU170m/190m, 메모리3626.5Mi/4330.5Mi, Pod slot11/17. 앱 3개 동시 surge는 CPU150m/192Mi/Pod3이다. 이는 시점 요청량이며 실제 usage 보장은 아니다. merge 직전 새 배포가 생겼으면 재확인한다.
- 최신 main10361ee 병합 후 전체 Helm·ClickStack15·자동관측10·CNPG 및 inventory 검사가 통과했다. 사용자에게 진행 중 migration 없음 확인을 받았다.
- #119 사용자 merge 요청에는 이 문서의 앱 자동 rollout·소유 로그 전송과 KPS 한정 수렴·rollback 승인을 함께 명시한다. 사용자가 이 내용을 확인하고 직접 merge하면 해당 공개된 범위에서 진행한다. 이 요청은 HyperDX 이미지 변경이나 별도 합성 자원 생성 승인을 포함하지 않는다.
