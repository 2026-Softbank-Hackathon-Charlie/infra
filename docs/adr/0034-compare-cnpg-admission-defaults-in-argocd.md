# 0034. AWS CNPG 앱에서 admission 기본값과 생략된 Role 값을 정규화해 비교한다

- 날짜: 2026-11-04
- 상태: 채택 — 2026-11-04 해당 앱 비교 정책 적용·Synced/Healthy와 runtime 불변 확인 완료
- 관련 ADR: [0005 PoC 범위](0005-scope-aws-cnpg-poc.md), [0029 단일 인스턴스 정책](0029-use-unsupervised-updates-for-single-instance-cnpg.md)

## 배경과 제약

AWS source는 병합 revision `87bd4e9ea04b8ba9112b69a5b13e8bca20f002ee`에서 Ready1/1이며 Database2개·DatabaseRole4개의 적용 상태가 최신이다. WAL 보관·예약 백업 성공 조건을 관측했으나 실제 SQL·복원 시험은 하지 않았다. Sync operation은 Succeeded, Health는 Healthy지만 Cluster와 Role4개가 OutOfSync로 표시됐다.

CNPG1.30.1은 Cluster에 admission 기본값을 추가하고 Role의 false boolean과 빈 inRoles는 직렬화에서 생략한다. 다섯 CR의 SSA dry-run 예측은 해당 생략 값 정규화 후 실제 spec과 일치했다. 관측 Argo server/controller는 v3.5.3이며 다른 앱·global 설정·공유 Operator·실제 DB 설정을 변경하지 않는다.

## 검토한 대안

- OutOfSync 유지: 실제 drift와 표현 차이를 구별하기 어렵다.
- 모든 기본값을 Git에 복사: Operator 기본값에 결합되고 실제 false 생략 문제는 남는다.
- Role spec 전체 또는 권한 필드를 무조건 ignore: 권한 상승·membership drift를 숨기므로 제외한다.
- 앱별 server-side diff와 조건부 정규화: admission이 반영된 예상 상태를 비교하면서 예외를 정확한 네 Role의 false/빈 목록으로 제한한다.

## 결정과 이유

Management `argocd/charlie-workload-cnpg-poc`에만 `ServerSideDiff=true,IncludeMutationWebhook=true` 비교 annotation을 적용한다. Role 네 개의 namespace·metadata.name을 지정하고 superuser/createdb/createrole/replication/bypassrls가 false일 때, inRoles가 []일 때만 JQ로 생략해 비교한다. 정확한 선언은 [수동 앱 예제](../../helm/cnpg-poc-applications.yaml.example)를 따른다. 새 Role에는 자동 적용하지 않는다. RespectIgnoreDifferences는 추가하지 않으며 Git의 명시적 false 선언과 실제 Sync 계약을 유지한다.

같은 Role 네 규칙에서 `/metadata/generation`만 JSON pointer로 비교 제외한다. 첫 적용 후 Cluster는 Synced였지만 Role에는 API가 SSA dry-run 중 증가시킨 이 카운터의 차이가 남았다. 원래 비교 정책으로 조건부 원복한 뒤 후속 독립 리뷰를 수행했다. 이 번호는 서버 파생 메타데이터이며 Role spec이 아니다. 실제 live Role의 applied와 metadata.generation/status.observedGeneration 일치 검증은 그대로 유지한다. 전체 객체에서 이 카운터 이외 차이가 발견되면 ignore 범위를 확대하지 않는다.

v3.5.3 소스는 server-side diff에서 사전 ignore를 건너뛰고 원 config로 SSA dry-run을 수행한 뒤 predicted/live를 정규화한다. dry-run의 ForceConflicts는 외부 manager가 소유한 필드에도 desired 값을 예측한다. CNPG1.30.1 CRD의 inRoles에는 list topology 지정이 없어 기본 atomic이며 []는 전체 목록을 대체한다. 따라서 예측 false/[]를 생략해도 실제 true/nonempty는 남아 차이가 된다. 독립 리뷰에서 이 순서·ownership 근거를 확인했고 실제 JQ 후처리의 권한5개·membership·connectionLimit·login·inherit 반례9개도 차이가 유지됐다. 이는 소스·SSA 계약과 JQ 검사 근거이며 실제 엔진 fixture나 production 권한 변경 시험은 아니다.

## 영향과 후속 작업

- 기대 효과: admission 기본값과 생략 표현에 따른 불필요한 차이를 줄이고 실제 설정 drift 감지를 유지한다.
- 단점과 위험: mutation webhook의 규칙 변화가 예측에도 반영된다. 생성 전 자원에는 server-side diff가 적용되지 않으므로 초기 server dry-run 게이트는 유지한다. 정책을 다른 버전·앱에 자동 확장하지 않는다.
- 실행 전 Argo 버전·live CRD inRoles topology·앱 UID/resourceVersion·고정 revision/수동 정책과 operation 부재를 재확인한다. 기존 비교 설정·동시 변경·set/map topology가 발견되면 덮어쓰지 않는다.
- 조건부 patch로 비교 설정과 hard refresh만 수행한다. Sync·SQL·DB 재시작·권한·Secret·Pod/PVC·source SHA·global ConfigMap은 변경하지 않는다. 실패 시 이번에 추가한 비교 정책만 같은 UID·현재값 조건으로 원복한다.
- 완료 기준은 Synced/Healthy와 Cluster/Role/Pod/PVC UID/spec 불변, 기존 revision/수동 Sync 정책 유지다. Role 네 개 각각의 generation 차이 정규화와 generation+기존9개 spec drift 반례 검증을 요구한다. 실제 데이터·복원/RPO/RTO는 별도 시험이다.

2026-11-04 적용 결과 해당 앱은 Synced/Healthy였다. Role 네 개의 전체 객체에서 generation 이외 차이가 없고 각9개(총36개) spec 반례가 계속 구분되는 것을 확인했다. live Role generation/observedGeneration과 applied도 최신이었다. Cluster/DB2/Role4/Pod/PVC 총9개 UID/spec 및 Pod 재시작 횟수가 첫 적용 전과 같았다. source SHA87bd4e9와 수동 정책·Sync operation 이력도 유지됐으며 새 Sync·DB 재시작은 없었다. 자신의 SSM 세션·kubeconfig 정리를 확인했다.

## 관련 자료

- [실행 기록](../plans/cnpg-aws-poc.md), [runbook](../runbooks/cnpg-poc.md), [검사](../../scripts/check-cnpg.py)
- [Argo diff 방식](https://argo-cd.readthedocs.io/en/stable/user-guide/diff-strategies/)
- [v3.5.3 diff 순서](https://github.com/argoproj/argo-cd/blob/v3.5.3/gitops-engine/pkg/diff/diff.go#L69-L192), [SSA 옵션](https://github.com/argoproj/argo-cd/blob/v3.5.3/gitops-engine/pkg/utils/kube/resource_ops.go#L432-L450)
- [CNPG1.30.1 inRoles CRD](https://github.com/cloudnative-pg/cloudnative-pg/blob/v1.30.1/config/crd/bases/postgresql.cnpg.io_databaseroles.yaml#L137-L145), [Kubernetes SSA topology](https://kubernetes.io/docs/reference/using-api/server-side-apply/#custom-resources-and-server-side-apply)
