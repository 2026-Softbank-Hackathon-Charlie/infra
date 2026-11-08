# 0098. 멤버를 명시한 DB Cluster의 연결 목록은 멤버 수와 상관없이 GitOps가 관리한다

- 상태: 제안
- 날짜: 2026-11-08
- 관련 ADR: [0078](0078-manage-three-member-db-paths-with-gitops.md), platform ADR 0064(세 환경 DB 그룹에서 환경 빼기)

## 상황

ADR 0078은 레이블 `hopcharlie.dev/db-topology-members`가 `"3"`인 Cluster에서만 `externalClusters`를 Git으로 맞췄다. platform ADR 0064는 세 멤버 그룹에서 한 멤버를 빼고 남은 두 멤버를 `members: [primary, peer]`로 다시 그린다. 차트는 이때 레이블 `"2"`를 쓴다.

기존 jq(`!= "3"`)로는 라이브 Cluster가 `"3"`, Git이 `"2"`일 때 Argo CD가 Git 쪽 목록만 무시하고 라이브 값을 되돌려 놓지 않는다. client-side apply가 last-applied 기준으로 `externalClusters`를 지우려 하고, CNPG는 `replica.source`가 목록에 없다며 거절한다. sync가 계속 실패하고 배포는 Synced와 Healthy를 기다리다 실패한다. 레이블이 없는 두 환경 값으로 다시 그려도 같은 문제가 생긴다.

## 결정

- ApplicationSet의 jq를 `select(.metadata.labels["hopcharlie.dev/db-topology-members"] == null) | .spec.externalClusters`로 바꾼다. 멤버 레이블이 있는 Cluster(`"2"`, `"3"`)는 목록을 Git이 관리하고, 레이블이 없는 기존 두 환경 Cluster만 계속 무시한다.
- 그래서 `"3"`에서 `"2"`로 바뀌는 sync는 레이블과 두 항목 목록을 함께 적용하고, 뺀 멤버의 항목이 지워진다. 플랫폼은 그 전에 primary의 `replica.source`를 남는 멤버로 바꾼다(ADR 0078의 정책 분기, 레이블이 `"3"`인 동안).
- `replica`, `certificates`, `bootstrap` 무시와 `RespectIgnoreDifferences=true`는 그대로다. workload admission 정책은 바꾸지 않는다.

## 결과

- 지금 레이블이 없는 두 환경 Cluster와 `"3"` Cluster의 동작은 같다. `"2"` Cluster는 이 변경 전에는 없었다.
- 적용은 hopcharlie-apps 차트(관리 EKS의 ApplicationSet) 동기화 한 번이다. 앱별 단계는 없다. platform ADR 0064의 배포보다 먼저 적용해야 한다.
- 검증: `scripts/check-helm.py`의 ApplicationSet 기대값을 바꿨다. kind의 실제 Argo CD로 `"3"`에서 `"2"` 전환과 그 뒤 재배포가 Synced인지 확인한 결과는 platform ADR 0064에 있다.
