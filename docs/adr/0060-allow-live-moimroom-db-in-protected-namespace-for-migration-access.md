# 0060. 이전 임시 권한 helper는 보호 네임스페이스 안의 지정한 CNPG 한 쌍만 예외로 허용한다

- 날짜: 2026-11-06
- 상태: 제안
- 2026-11-07 갱신: 손으로 배포했던 GKE moimroom을 지웠다. moimroom은 더 이상 GKE에서 돌지 않는다(EKS `moimroom/moimroom-db`와 onprem에서 돈다). `charlie-apps/moimroom-db-gke`가 없어져 `PROTECTED_EXCEPTIONS`를 코드에서 뺐다. 보호 네임스페이스는 다시 예외 없이 거부한다.
- 관련 ADR: [0048](0048-connect-cloud-databases-with-scoped-migration-access.md), [0057](0057-back-up-gke-moimroom-db-with-workload-identity.md), 0053(운영 moimroom 이전 대상은 새 standby `moimroom-db-gke`다. [PR #72](https://github.com/2026-Softbank-Hackathon-Charlie/infra/pull/72)에 있고 아직 병합되지 않았다)

## 배경과 제약

확인된 사실:

- `scripts/db-migration-access.py`는 이전할 때만 플랫폼 계정(`charlie-system/hopcharlie-platform`)에 지정한 CNPG Cluster 하나의 `get`, `patch`를 임시로 준다(ADR 0048). 이 helper는 `charlie-apps`를 포함한 인프라 네임스페이스를 보호 목록(`PROTECTED`)에 두고 `render`, `grant`, `revoke` 모두에서 거부한다.
- 운영 moimroom DB는 GKE의 `charlie-apps/moimroom-db-gke`이고 EKS `moimroom/moimroom-db`가 이를 따라가는 replica다(2026-11-05 운영 이전 기록, ADR 0053과 #72). `charlie-apps`는 GKE 앱 워크로드의 공용 네임스페이스다.
- 2026-11-06 GKE → EKS 이전이 precheck에서 세 번 멈췄다. 플랫폼 계정이 원본 Cluster를 `get`할 권한이 없었다. 상시 권한은 `list`뿐이다(`clusters/gcp-dev-workload/hopcharlie-platform-access.yaml`). helper가 원본 네임스페이스를 거부해서 런북 절차대로는 원본에 권한을 줄 수 없었다.

가정과 미확인:

- 이 문제를 다룬 PR이나 ADR이 없다는 것은 2026-11-06에 PR·이슈 제목과 본문, ADR 본문을 검색해 확인한 결과다. 사라진 내용이나 리뷰 스레드까지 읽은 것은 아니다(미확인).
- 실제 클러스터의 현재 역할과 lag는 이번 작성에서 다시 조회하지 않았다.
- 허용할 쌍이 앞으로 더 늘어날지는 정해지지 않았다.

## 검토한 대안

- `charlie-apps`를 보호 목록에서 뺀다: 코드 변경이 가장 작다. 그러나 `charlie-apps`의 모든 앱 DB에 임시 권한을 줄 수 있게 되어 보호의 의미가 사라진다. 제외한다.
- helper를 쓰지 않고 같은 범위의 Role과 RoleBinding을 수동으로 만든다: PR이 필요 없다. 그러나 소유권·UID 확인, 부분 실패 롤백, 조건부 회수를 모두 잃고 회수를 수동 `kubectl delete`에 맡긴다. 상시로 쓰지 않는다. 긴급할 때의 우회로만 남긴다.
- 실행 인자로 보호를 푼다(예: `--allow-protected`): 운영자가 실행할 때 보호를 끌 수 있어 승인 절차를 우회하기 쉽다. 제외한다.
- 정확히 `(네임스페이스, CNPG Cluster)` 한 쌍만 코드의 예외 목록에 둔다: 보호는 그대로 두고 이번에 필요한 DB 하나만 연다. 예외를 늘리려면 코드 리뷰를 거친다. 선택한다.

## 결정과 이유

- `scripts/db-migration-access.py`에 `PROTECTED_EXCEPTIONS`를 두고 `('charlie-apps', 'moimroom-db-gke')` 한 쌍만 넣는다.
- 보호 네임스페이스는 이 목록에 정확히 일치하는 `(네임스페이스, Cluster)`일 때만 허용한다. 같은 네임스페이스의 다른 Cluster(예: 낡은 독립 DB `moimroom-gcp-db`), 다른 보호 네임스페이스, `kube-*` 네임스페이스는 계속 거부한다.
- `render`, `grant`, `revoke`가 같은 검사를 거쳐서 부여와 회수가 일관되다.
- 부여되는 권한은 그대로다. 지정한 Cluster 하나의 `get`, `patch`이고 Secret, 다른 Cluster, cluster 범위 RBAC, 새 토큰은 포함하지 않는다.
- [ADR 0063](0063-born-paired-app-databases-for-two-environment-apps.md) 이후 helper의 Role은 같은 Cluster에 사용자 정의 verb `migrate`도 준다. 워크로드의 ValidatingAdmissionPolicy가 레이블 없는 네임스페이스의 Cluster patch를 이 verb가 있을 때만 통과시키기 때문이다. 범위와 회수 방식은 그대로다.
- 이 변경은 코드 준비다. 실제 권한 부여는 ADR 0048과 `AGENTS.md`에 따라 별도 승인과 운영자 실행이 필요하다.

## 영향과 후속 작업

- 기대 효과: 런북 절차대로 GKE 원본 `moimroom-db-gke`에 임시 권한을 줄 수 있고, 소유권 확인과 안전한 회수도 그대로 쓴다.
- 단점과 위험:
  - 예외가 코드에 고정되어 DB가 바뀔 때마다 PR이 필요하다. 의도한 마찰이다.
  - `patch`는 그 CNPG 객체의 전체 필드에 대한 권한이고 Kubernetes RBAC에는 TTL이 없다. 이전이 끝나면 반드시 `revoke`해야 한다(ADR 0048과 같음).
  - `moimroom-db-gke`가 다른 용도로 바뀌거나 이름이 바뀌면 예외를 갱신해야 한다.
- 필요한 후속 작업 및 검증:
  1. 부여 전에 플랫폼 카탈로그(`apps.yaml`)의 gke `cnpgCluster`가 `moimroom-db-gke`로 운영에 반영됐는지 확인한다(infra #94, SSM 반영과 API 재시작 완료).
  2. 부여 후 `kubectl auth can-i migrate`가 해당 Cluster에서만 `yes`이고 Secret은 `no`인지 확인한다. ADR 0063의 ClusterRole이 적용된 클러스터에서는 `get`, `patch`가 모든 Cluster에서 `yes`이고, 실제 patch 범위는 VAP와 `migrate`가 정한다.
  3. 이전 종료 뒤 같은 세션으로 `revoke`하고 `auth can-i`가 `no`인지 확인한다.
  4. 승인 시점에 플랫폼 계정 권한을 미리 확인하는 검사는 platform 쪽 별도 작업이다(미구현).

## 관련 자료

- [scripts/db-migration-access.py](../../scripts/db-migration-access.py)
- [scripts/tests/test-db-migration-access.py](../../scripts/tests/test-db-migration-access.py)
- [docs/runbooks/aws-gcp-db-migration.md](../runbooks/aws-gcp-db-migration.md)
- [이슈 #89](https://github.com/2026-Softbank-Hackathon-Charlie/infra/issues/89): 임시 권한 검증
