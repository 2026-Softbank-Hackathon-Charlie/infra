# 0029. 단일 인스턴스 CNPG에는 unsupervised 업데이트 정책을 사용한다

- 날짜: 2026-11-03
- 상태: 채택 — 수정 개발 계획 승인. 새 revision의 실제 배포·검증 대기
- 관련 ADR: [0005 PoC 범위](0005-scope-aws-cnpg-poc.md), [0006 백업·복구](0006-back-up-cnpg-poc-with-barman.md)

## 배경과 제약

AWS CNPG PoC는 `instances: 1`이다. 2026-11-03 실제 설치에서 고정 Operator1.30.1의 `vcluster.cnpg.io` webhook이 `primaryUpdateStrategy: supervised`와 단일 인스턴스 조합을 거부했다. DB 앱의 선언16개 중15개는 적용됐지만 Cluster와 source PVC는 생성되지 않았다. 기존 구조 검사는 이 교차 필드 제약을 확인하지 못했다. 복원 예제에도 같은 조합이 있었다.

공식 고정 릴리스 문서는 `supervised`를 replica 업데이트 후 수동 전환/재시작을 기다리는 정책으로 설명한다. 현재 단일 인스턴스에는 전환할 replica가 없다. 기존 Operator·Project·이미지·기반 설정과 이미 생성한 인증정보를 보존해야 한다.

## 검토한 대안

- `supervised` 유지: 실제 admission에서 거부되므로 사용하지 않는다.
- replica 추가·HA 구성: 용량·비용·장애 모델을 바꾸므로 현재 PoC 범위에서 제외한다.
- webhook 해제 또는 Operator 변경: 검증·공유 운영 경계를 우회하므로 제외한다.
- 기본값에 맡기거나 `unsupervised` 명시: 지원되는 기본 정책이며, 템플릿에서 의도를 드러내도록 명시한다.

## 결정과 이유

source와 full/PITR restore 템플릿의 단일 Cluster에 `primaryUpdateStrategy: unsupervised`를 사용한다. 인스턴스 수·이미지 digest·나머지 runtime spec은 유지한다. 구조 검사와 별도로 source/full/PITR 모든 Cluster에서 `instances: 1 + supervised` 조합을 거부하는 검사를 추가한다.

Argo 앱은 계속 수동 Sync와 검토한 Git SHA를 사용한다. 이 설정은 공유 Operator가 외부 작업으로 업그레이드되는 것까지 막지 않는다. 기존 ADR0005의 단일 인스턴스·수동 복구 범위는 그대로 유지한다.

## 영향과 후속 작업

- 기대 효과: 지원되지 않는 조합을 제거해 source와 restore가 admission을 통과할 수 있다.
- 단점과 위험: 향후 이미지·자원·설정·Operator 변경으로 rollout이 시작되면 Primary가 추가 수동 전환 대기 없이 재시작될 수 있다. 단일 DB는 그동안 중단되며 무중단을 보장하지 않는다.
- 필요한 후속 작업 및 검증: 기존 렌더와 비교해 각 strategy 외 runtime spec 불변 확인, 회귀 검사, 최종 병합 SHA의 manifest 재검증, 별도 실행 승인 후 source Cluster server dry-run과 실제 DB Ready/Role/PVC/Pod Identity/공개 CA/archiving 상태 확인. 실제 restore·SQL·백업/RPO/RTO 시험은 별도다.
- 이미 생성한 키·암호·Secret과 부분 적용 자원을 재사용한다. 삭제·회전·Operator 수정으로 우회하지 않는다. archiving 실패나 미확정 상태를 성공으로 보고하지 않는다.

## 관련 자료

- [source Cluster](../../helm/charts/cnpg-poc/templates/cluster.yaml), [restore Cluster](../../examples/cnpg-restore/templates/restore.yaml), [검사](../../scripts/check-cnpg.py)
- [실행 계획과 상태](../plans/cnpg-aws-poc.md), [runbook](../runbooks/cnpg-poc.md)
- [고정1.30.1 공식 rolling update 설명](https://github.com/cloudnative-pg/cloudnative-pg/blob/v1.30.1/docs/src/rolling_update.md)
