# 0063. 관측 TLS는 짧은 leaf와 공개 CA bundle 전환으로 갱신한다

- 날짜: 2026-11-06
- 상태: 제안 — C 실행 계획 리뷰·승인 대상, 실제 인증서 미발급
- 관련 ADR: [0061 사설 전송](0061-private-otlp-ingress-for-aws-workload.md), [0062 자동 관측](0062-automatically-observe-platform-workloads.md)

## 배경과 제약

AWS workload local gateway와 사설 Prometheus query에 TLS가 필요하다. 기존 계획은 CA private key를 발급 중 메모리에서만 사용하고 서버 leaf 발급 후 폐기하도록 정했다. query leaf는 실제 NLB DNS SAN이 필요하다. 인증서 수명과 CA key를 보존하지 않는 조건에서의 갱신 순서를 확정해야 한다. 앱에는 공개 CA만 공통 profile로 배포한다.

## 검토한 대안

- 장기 leaf와 CA key 파일 보존: 발급·갱신이 쉽지만 private key 영구 저장을 도입하므로 제외.
- cert-manager/Vault/ACM Private CA 추가: 자동 갱신에 유리하지만 새 발급 권한·비용·구성 범위가 생겨 이번 활성화에서는 제외.
- leaf90일/CA365일, CA key 메모리 전용, 신규 CA bundle을 먼저 신뢰시킨 후 leaf 전환: 초기 선택 제안. 자동 갱신을 제공하지 않으며 운영자가 만료 전에 재발급해야 한다.

## 결정 제안과 이유

workload에서 새로 발급하는 local gateway 및 query leaf는90일, 자체 CA는365일로 제안한다. CA key는 메모리에서 leaf 발급에만 사용한 뒤 폐기한다. 중앙 기존 인증서와 관리 gateway leaf는 교체하지 않으며 실제 만료/SAN은 검증한다.

갱신할 때 새 CA/leaf를 발급하고, 기존+신규 공개 CA bundle을 agent/앱 profile/HyperDX에 먼저 공급해 readiness를 확인한다. 서버 Secret을 새 leaf로 전환한 뒤 새 trust로 TLS 검증하고 이전 CA를 제거한다. 앱 profile CA checksum은 Pod rollout을 유발한다. 실제 SAN과 trust propagation이 확인되지 않으면 이전 CA를 제거하지 않는다.

agent/upstream bearer와 query password는 별개 credential이며 TLS 유효기간으로 자동 만료되지 않는다. 자동 rotation/폐기는 구현됐다고 주장하지 않는다. credential 교체는 새 실행 범위를 확인해 수행한다.

## 영향과 후속 작업

- 효과: 앱에 CA private key나 bearer를 공급하지 않고 hostname 검증을 유지한다.
- 단점·위험: 자동 갱신이 없고 CA key를 재사용할 수 없다. 운영자가 만료를 관리해야 하며 trust bundle/서버 인증서 교체 순서를 어기면 수집·조회가 중단된다. 공개 CA 교체에도 앱 rollout이 필요하다.
- 후속: C 승인 후 실제 notBefore/notAfter/SAN과 갱신 기한만 기록하고 key 원문은 기록하지 않는다. 장기 운영 시 전용 자동 발급 방식은 별도 ADR로 검토한다.

## 관련 자료

- [C 활성화 계획](../plans/workload-observability-activation.md)
- [운영 안내](../operations/automatic-workload-observability.md)
