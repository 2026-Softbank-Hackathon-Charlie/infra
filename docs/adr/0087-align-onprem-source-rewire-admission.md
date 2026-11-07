# 0087. 온프레미스 source 준비 권한을 AWS·GCP와 동일하게 제한한다

- 날짜: 2026-11-08
- 상태: 제안
- 관련 ADR: [0078](0078-manage-three-member-db-paths-with-gitops.md), [0082](0082-onboard-existing-compose-apps-infra.md)

## 배경

이전 엔진은 세 멤버 DB의 현재 primary를 강등하기 전에 `replica.source`를 선택한 대상으로 준비한다. 엔진의 patch는 UID·resourceVersion·self·primary를 검사하고 source 하나만 바꾼다. 이 단계는 HTTP Hold와 승격 전에 실행된다. 권한 거절은 이전의 준비를 막으며, 성공한 컷오버 뒤의 정리 경고와 구분해야 한다.

ADR 0078의 AWS·GCP 정책에는 이 변경의 제한적 허용 분기가 있지만, 뒤에 추가된 onprem 정책에는 빠져 있다. 또한 실행 오류가 저장소의 최신 정책보다 오래된 메시지를 표시할 수 있으므로, 코드 수정만으로 운영 정책이 적용되었다고 판단할 수 없다.

## 결정

온프레미스 정책에 AWS·GCP와 같은 분기를 추가한다. 기존 Cluster에 `hopcharlie.dev/db-topology-members: "3"`이 있어야 하고, 기존 `primary == self`여야 한다. 새 source는 기존 `externalClusters`에 존재하는 self 이외의 이름이어야 한다. source 외 replica 필드의 변경·삭제는 이 분기에서 허용하지 않는다.

기존 Namespace의 `hopcharlie.dev/db-topology=true` 또는 정확한 Cluster의 임시 `migrate` 권한 검사는 그대로 유지한다. RBAC 권한이나 자격증명을 추가하지 않는다. primary/promotionToken 전환, 승격된 legacy DB의 source 전환, CNPG의 archive_mode on/always 예외와 다른 spec·labels·annotations 변경 금지도 유지한다. replica의 임의 재연결이나 새로운 externalClusters·연결 정보·인증서 변경을 허용하는 대안은 선택하지 않는다.

세 환경 정책의 정적 동일성 검사를 기본 CI에 추가하고, 별도 임시 kind API 서버에서 공식 CNPG 1.30.1 CRD와 실제 RBAC/CEL을 검증한다. 정상 source patch와 기존 전환은 허용하고, 미승인 Cluster·standby·두 멤버·임의 source·동시 필드 변경·오래된 UID는 거부해야 한다. 이 시험은 실제 PostgreSQL 이전이나 데이터 무손실을 증명하지 않는다.

## 영향과 운영 경계

이 변경으로 onprem을 원본으로 하는 다음 이전에서도 동일한 준비 규칙을 사용할 수 있다. 대상이 현재 source를 직접 복제하는지, 앱이 등록된 세 DB 환경인지, SQL 역할과 복제가 올바른지는 엔진·플랫폼의 기존 검사 책임이다. 이 정책은 등록 앱 레지스트리를 직접 읽지 않으며 운영자의 topology 레이블과 migrate 권한 부여를 신뢰한다.

운영 적용·권한 변경은 이 PR에 포함하지 않는다. 특히 보고된 GKE 거절은 최신 저장소 정책과 다른 이전 메시지였으므로, 승인 후 실제 세 클러스터의 VAP 본문과 바인딩을 비교하고 최신 정책을 적용해야 한다. 같은 실행을 재개한 뒤 성공했어도 예전 FailureError가 남을 수 있는 결과 표시 문제는 별도 엔진 계약 조사 대상이다. 이를 근거로 성공한 컷오버를 자동 롤백하지 않는다.

## 관련 파일과 자료

- [온프레미스 정책](../../clusters/onprem-dev-workload/hopcharlie-platform-access.yaml)
- [실제 admission 회귀](../../scripts/test-cnpg-migration-admission.py)
- [격리 CI](../../.github/workflows/cnpg-migration-admission.yml)
- [CNPG replica clusters 공식 문서](https://cloudnative-pg.io/documentation/current/replica_cluster/)
