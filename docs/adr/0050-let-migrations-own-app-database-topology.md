# 0050. 앱 DB의 이전 토폴로지 필드는 Argo CD가 되돌리지 않는다

- 날짜: 2026-11-05
- 상태: 제안
- 관련 ADR: [0023](0023-deploy-github-apps-with-applicationset.md), [0036](0036-app-routing-and-argo-apps.md), [0048](0048-connect-cloud-databases-with-scoped-migration-access.md)

## 배경과 제약

앱 ApplicationSet(`hopcharlie-apps`)이 만든 Application은 `selfHeal: true`다. 앱 차트(`hopcharlie-app`)의 CNPG Cluster에는 `spec.replica`가 없다.

이전 엔진은 원본을 강등하고 대상을 승격할 때 두 Cluster의 `spec.replica`를 patch한다. selfHeal은 이 변경을 몇 초 안에 차트 값(빈 값)으로 되돌린다. 강등한 원본이 다시 primary가 되고 대상도 승격되면 두 DB가 모두 쓰기를 받는다(split-brain). 2026-11-05 운영 클러스터를 조회했을 때 `moimroom-eks` Application이 `selfHeal: true`, 원본 `moimroom/moimroom-db`에는 `replica`·`externalClusters`가 없었다.

이전을 준비하는 운영자는 원본에 상대 DB(`externalClusters`)와 상대가 접속하는 이름(`certificates.serverAltDNSNames`, ADR 0048의 프록시 DNS)을 더해야 한다. 이 값들도 selfHeal이 지운다.

CNPG는 승격된 primary를 smart shutdown으로 재시작하며 기본 180초 동안 클라이언트 세션을 기다린다. 앱의 커넥션 풀 때문에 새 primary가 몇 분 내려가 있을 수 있다(migration-engine ADR 0009).

## 검토한 대안

- 이전하는 동안 Application의 자동 sync를 끄기: Application은 ApplicationSet이 템플릿으로 다시 덮으므로 Application만 고치면 유지되지 않는다. ApplicationSet 전체를 끄면 다른 앱의 배포도 멈춘다.
- 차트가 이전 토폴로지를 값으로 그리기: GitOps가 최종 상태를 갖는 정석이다. 그러나 엔진이 승격 순간에 바꾸는 `spec.replica`는 커밋보다 빨라야 하고, 커밋을 기다리면 그 사이 selfHeal이 되돌린다. 피어·인증서 값은 이후 차트로 옮길 수 있다.
- 세 필드만 무시: 선택한다. 엔진과 운영자가 바꾸는 범위만 Argo CD가 보지 않고, 나머지 Cluster 설정(인스턴스 수, 스토리지, 리소스)은 그대로 GitOps가 지킨다.

## 결정과 이유

- ApplicationSet 템플릿에 `postgresql.cnpg.io/Cluster`의 `.spec.replica`, `.spec.externalClusters`, `.spec.certificates`를 `ignoreDifferences`로 둔다. 차트는 세 필드를 그리지 않으므로 무시해도 GitOps가 잃는 값이 없다.
- `syncOptions: [RespectIgnoreDifferences=true]`를 켠다. 무시만 하면 diff에서 빠질 뿐 새 배포의 sync가 세 필드를 덮을 수 있다.
- 앱 차트의 Cluster에 `smartShutdownTimeout: 5`를 넣는다. 이전과 상관없이 항상 같은 값이 맞으므로 무시하지 않고 차트가 갖는다.

## 영향과 후속 작업

- 기대 효과: 이전 중 selfHeal 때문에 강등·승격이 되돌아가지 않는다. 운영자가 원본에 피어와 인증서 이름을 넣을 수 있다.
- 단점과 위험:
  - 세 필드는 GitOps로 복구되지 않는다. 이전이 끝난 뒤 남는 토폴로지(옛 원본이 새 primary를 따라가는 복제본)는 Git에 없다. 사람이 Cluster를 직접 보고 판단해야 한다.
  - 이 PR을 merge하면 Argo CD가 모든 앱 Application을 고친다. `smartShutdownTimeout` 변경은 CNPG가 재시작 없이 반영한다. 지금 앱은 moimroom 하나다.
  - 운영자가 실수로 세 필드를 잘못 바꿔도 Argo CD가 고치지 않는다.
- 필요한 후속 작업 및 검증:
  1. merge 뒤 `moimroom-eks` Application에 `ignoreDifferences`와 `RespectIgnoreDifferences`가 있는지, Synced·Healthy인지, `moimroom-db`에 `smartShutdownTimeout: 5`가 들어갔는지 확인한다.
  2. 원본에 `spec.replica`를 patch한 뒤 몇 분 동안 Argo CD가 되돌리지 않는지 확인한다.
  3. 차트가 대상 standby와 이전 뒤 토폴로지를 값으로 그리는 방식은 플랫폼 배포와 함께 별도로 정한다.

## 관련 자료

- [helm/charts/hopcharlie-apps/templates/applicationset.yaml](../../helm/charts/hopcharlie-apps/templates/applicationset.yaml)
- [helm/charts/hopcharlie-app/templates/postgres.yaml](../../helm/charts/hopcharlie-app/templates/postgres.yaml)
- Argo CD: [Diffing customization](https://argo-cd.readthedocs.io/en/stable/user-guide/diffing/), [RespectIgnoreDifferences](https://argo-cd.readthedocs.io/en/stable/user-guide/sync-options/#respect-ignore-difference-configs)
- CloudNativePG: [Replica clusters](https://cloudnative-pg.io/documentation/current/replica_cluster/)
