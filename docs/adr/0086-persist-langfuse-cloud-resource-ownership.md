# 0086. Langfuse 전용 cloud 리소스를 공통 Terraform 소스에서 지속 관리

- 날짜: 2026-11-07
- 상태: 제안 — 동일 자원은 실환경에 복원돼 있음. 이 소스 PR의 main merge·자동 apply는 별도 승인/계획 검토 전
- 관련 ADR: [0050 ClickStack](0050-adopt-clickstack-for-hackathon-observability.md)

## 배경과 제약

기존 PG·ClickHouse를 재사용하는 Langfuse를 관리 클러스터에 Helm으로 최초 설치했다. 최초 생성했던 전용 S3bucket/IAMrole/PodIdentity association이 실제 수집 점검에서 모두 없었고, 새 Pod에 자격이 주입되지 않아 OTLP 수집이 S3 credential500으로 실패했다. 삭제 원인/행위자는 감사 로그를 조회하지 않아 미확정이다.

동일 cloud7개를 기존 foundation/management state에서 신규create만 허용하는 saved plan으로 복원했다. Web/Worker 재시작 뒤 synthetic trace1개(AGENT/GENERATION2span)의 실제 OTLP200·API 입력/출력/부모연결일치·ClickHouse2span 저장을 확인했다. main에는 이7개 정의가 없고 Terraform 변경 main push가 all-runtime apply를 실행하므로 미정의 리소스 재삭제 위험이 있다.

## 검토한 대안

- 수동/API 복원만 반복: 공통 state와 main 소스의 불일치를 남기므로 제외.
- Helm/vendor/초기화/평가 전체 변경을 한꺼번에 반영: 현재 cloud 소유권 문제를 최소 범위로 해결하기 위해 후속 PR로 분리.
- 실제 복원한 동일7개 정의만 기존 두 root에 반영: 주소·소유 state를 유지하고 다른 앱/모듈을 수정하지 않아 선택.

## 결정과 이유

foundation에 전용 S3bucket·SSE-S3·public block·TLS강제 bucketpolicy·IAMrole·events/ List/Get/Put policy6개를, management에 namespace/SA langfuse/langfuse의 PodIdentity association1개를 정의한다. dev환경의 enable_langfuse_storage 기본은 true로 두며 false 전환은 승인된 폐기 작업으로 다룬다. force_destroy=false이며 정적 AWS키·Delete·다른bucket 권한은 없다.

association은 module.eks.name을 통해 cluster 존재에 암묵 의존한다. 전체 module depends_on은 관련 없는 nodegroup update까지 복원 targetplan에 포함시켜 제거했다. 다른 nodegroup/addon/공유module 코드는 변경하지 않는다. 기존 EKS mock에는 Langfuse를 명시false로 분리하고 전용 mock 계약은 enable/disable·role trust/prefix 경계를 검사한다.

## 영향과 후속 작업

이 PR은 기존 cloud 리소스의 지속 소유권만 반영한다. Helm release·DB/user/Secret·owner/project/APIkey·LLM계측·GitOps 인계는 변경하지 않는다. 기존 공유 PG/CH와 다른 bucket/role은 수정하지 않는다.

main merge 전에 최신 main+PR의 실제 all-runtime 계획에서 다른 기존 자원 변경/삭제/교체가 없는지 반드시 검토한다. 원문 plan/state/비밀값은 로그·artifact에 출력/첨부하지 않는다. 예상 외 변경은 별도 승인 전 merge하지 않는다. main merge가 기존 all-runtime apply를 실행하므로 정확한 대상·권한 범위의 실행 승인 뒤에만 병합한다. PR 생성·검사 통과를 main 적용 완료라고 표현하지 않는다.

## 관련 자료

- [foundation 정의](../../terraform/environments/aws/dev/foundation/langfuse.tf)
- [management association](../../terraform/environments/aws/dev/management/langfuse.tf)
- [기존 Terraform 배포 workflow](../../.github/workflows/terraform-check.yml)
- [Langfuse self hosting](https://langfuse.com/self-hosting)
