# 0017. 기존 Helm 리소스를 보존하며 Argo CD 관리로 전환

- 날짜: 2026-11-03
- 상태: 채택 — 자동 동기화 없는 준비 단계 승인·완료, 실제 관리 주체 전환·sync는 미실행
- 관련 ADR: [0016 플랫폼 관리 클러스터 운영](0016-run-hopcharlie-platform-on-management-cluster.md)

## 배경과 제약

Argo CD root 최초 연결 점검에서 Application/ApplicationSet은 없지만 management의 플랫폼 네 Pod와 PVC 세 개가 이미 실행 중임을 확인했다. gp3 StorageClass에 Helm baseline release 소유권 annotation이 있고 플랫폼·Load Balancer Controller에도 Helm managed-by label이 있다. 기존 리소스에는 데이터가 존재할 수 있으며 DB가 비어 있다고 가정할 수 없다. 정확한 Helm 실행 주체와 release 입력은 미확인이다.

## 검토한 대안

- Helm uninstall 후 root로 재배포: 소유권은 단순해지지만 PVC·DB·서비스 중단 위험이 있어 제외한다.
- Helm 운영을 유지하고 Argo 연결을 보류: 현재 서비스는 유지되지만 GitOps 자동 배포 목표를 충족하지 못한다.
- 기존 리소스를 보존하고 차이를 검토한 뒤 Argo에 연결: 사전 검증이 더 필요하지만 기존 배포와 데이터를 유지할 수 있어 제안한다.

## 결정과 이유

전환 준비 단계에서는 root와 하위 Application의 자동 동기화 및 operation을 모두 비활성화한 채 등록·diff 검토만 수행한다. 실제 sync/자동화 활성화와 비파괴 중단 절차는 구체적인 diff 및 별도 실행 승인 후 진행한다.

기존 namespace/name과 PVC/DB를 유지하고 Helm 배포와 Git 렌더링 간 차이를 확인한 뒤, 승인된 리소스에 대해서만 Argo 추적·동기화를 허용한다. Helm uninstall, force/replace sync, PVC/StatefulSet 재생성은 수행하지 않는다. Helm release 기록을 지우거나 기존 annotation을 일괄 제거하지 않는다. 기존 Helm 배포 실행 주체가 동시에 upgrade/rollback하지 않도록 확인하는 것이 전제다.

기존 Pod template·PVC·StatefulSet의 데이터/immutable 필드 등 의도하지 않은 변경은 전환과 함께 처리하지 않는다. 실행 결과에서 기존 볼륨 identity와 Pod 상태를 전후 비교한다. 사용자는 기존 서비스를 유지하고 자동 동기화 없이 등록·비교하는 준비 단계를 승인했으며, 그 범위는 완료했다. 이 ADR의 채택은 실제 관리 주체 전환·sync 완료를 뜻하지 않는다.

## 영향과 후속 작업

- 기대 효과: 기존 데이터를 보존하며 infra GitOps가 선언한 관리 방식에 연결한다.
- 단점과 위험: Argo selfHeal과 별도 Helm 작업이 경쟁하면 설정이 번갈아 바뀔 수 있다. 이름이 같아도 실제 spec이 다르면 동기화가 rollout이나 실패를 유발할 수 있다. Helm 기록은 남으므로 이후 운영자는 Argo 전환 사실을 알아야 한다.
- 후속 작업: 실제 Helm 소유권과 입력 차이 확인, 기존 자동 Helm 실행 유무 확인, 등록 충돌 확인을 위한 한정 조회 승인, 변경 계획 독립 리뷰·사용자 승인, 기존 PVC identity 보존 및 배포 상태 확인. 확인되지 않은 차이가 있으면 동기화하지 않는다.

## 관련 자료

- [root 연결 계획과 변경 계획](../plans/argocd-root-bootstrap.md)
- [root 예제](../../helm/root-application.yaml.example)
- [GitOps Application 템플릿](../../helm/gitops/templates/applications.yaml)

## 준비 단계 결과 — 2026-11-03

management에 수동 Application 14개와 AppProject 3개, management cluster 등록 Secret 한 개를 생성했다. 모든 앱의 자동 동기화/operation 부재와 기존 Pod/PVC identity·상태 유지를 확인했다. 실제 sync·Helm uninstall/upgrade/rollback·PVC/DB 변경은 하지 않았다. 구체적인 비교 결과와 승인 범위는 연결 계획의 실행 완료 기록을 따른다. 최신 main의 기존 ADR 0015/0016을 확인하여 번호 0017을 사용했다.
