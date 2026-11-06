# 0061. AWS workload 관측은 전용 사설 전송·조회 경로로 연결한다

- 날짜: 2026-11-06
- 상태: 채택 — C2 사설 backend·agent 배포 및 합성 저장 검증 완료; HyperDX 원격 Prometheus 인증 전달 미완료
- 관련 ADR: [0050 ClickStack](0050-adopt-clickstack-for-hackathon-observability.md), [0062 자동 연결](0062-automatically-observe-platform-workloads.md)

## 배경과 제약

관리 ClickStack은 설치됐지만 workload 수집기와 CNPG Prometheus 연결은 없다. 두 EKS는 같은 VPC이나 ClusterIP DNS를 클러스터 간 주소로 사용할 수 없다. 앱·DB·플랫폼 작업을 자동으로 연결하고 HyperDX에서 로그·트레이스·CNPG 메트릭을 조회해야 한다. 사용자 요청으로 기존 앱2개 한정/앱별 Secret 복사 제안은 철회했다.

## 검토한 대안

- Pod IP 또는 임시 SSM/port-forward: 재시작·세션 수명 의존으로 상시 경로에서 제외.
- 기존 공개 ALB·앱/DB LB·DB Tailscale 경로 변경: 기존 데이터 경로와 노출/장애 범위를 섞어 제외.
- 전용 internal NLB로 신호별 제한 경로 제공: LB 비용이 추가되지만 기존 AWS LBC를 활용하는 안정된 주소·소스 제한이 가능해 제안.

## 결정과 이유

1. **중앙 ingest:** 관리 private subnet의 internal NLB TCP443→중앙 Collector4318, IP target, workload 두 subnet CIDR만 source로 허용한다. TLS는 Collector까지 통과하며 gateway가 기존 CA·중앙 서비스 서버 이름을 검증한다. workload 전용 bearer를 추가하고 기존 management token/ClusterIP는 보존한다.
2. **메트릭 query:** workload 최소 Prometheus 앞에 신규 NGINX unprivileged TLS/BasicAuth/read-only query proxy 및 internal NLB를 둔다. 관리 두 subnet CIDR만 허용하고 lifecycle/write/admin은 거부한다. proxy image는 B에서 공식 안정 버전/digest를 확인해 고정한다. 실제 NLB DNS SAN 인증서와 HyperDX 공개 CA trust로 hostname 검증을 유지한다. HyperDX 기존 팀에는 인증 API로 connection/source를 한 번 등록해 새 DB를 자동 발견한다.
3. NLB/target group/관리 SG는 AWS LBC가 소유한다. 기존 앱/DB LB·공개 ALB·Terraform state는 변경하거나 중복 소유하지 않는다.

로컬 app listener는 TLS+소유 namespace/Pod 정책, agent listener는 TLS+bearer, 중앙 전송은 별도 bearer로 분리한다. 앱에 중앙 토큰이나 앱별 수동 credential을 배포하지 않는다. 상세 자동 연결·baseline compatibility의 gateway 제외와 전용 ingress/targetPort egress는 [ADR0062](0062-automatically-observe-platform-workloads.md)의 결정 제안이다. 강한 다중 테넌트 인증으로 표현하지 않는다.

## 영향과 후속 작업

- 추가: internal NLB2개, workload 최소 Prometheus/operator·query proxy, queue2Gi+Prometheus10Gi, agent 구현 교체. 시간/처리량/스토리지 과금이 추가되고 정확한 비용은 C에서 현재 단가·유입 가정으로 제시한다.
- 위험: 새로운 사설 서비스·인증서/자격 증명·짧은 중앙 rollout, SDK/agent·baseline 정책 전환과 용량·인증서 갱신 책임이 있다.
- 코드 준비 B는 모든 기존 환경 activation off·기존 렌더 불변을 유지한다. C 승인 후 별도 activation PR 머지에 따른 selfHeal rollout까지 실행 승인에 포함한다.
- 실제 internal scheme·생성 frontend SG source·target health·hostname/TLS/auth·금지 경로 거부와 기존 앱/DB/모니터링 readiness를 확인해야 한다. manifest 선언만으로 완료 처리하지 않는다.
- 실패 시 선택 입력/정책/앱·수집기만 이전 revision으로 수동 복구한다. DB/PVC/관측 데이터/기존 Secret은 자동 삭제하지 않는다. 새 LB/SG/Secret 폐기는 소유권·대상을 명시한 별도 승인 범위에서 수행한다.
- 첫 적용은 AWS만 포함한다. GCP/온프레미스 연결 완료를 주장하지 않는다. 새 CI·IAM/cluster-admin·DB topology·노드 자동 증설은 제외한다.

## 관련 자료

- [개발·검증·실행 계획 v3](../plans/workload-observability-expansion.md)
- [AWS LBC Service annotations](https://kubernetes-sigs.github.io/aws-load-balancer-controller/latest/guide/service/annotations/)
- [NGINX BasicAuth](https://docs.nginx.com/nginx/admin-guide/security-controls/configuring-http-basic-authentication/)
- [기존 ClickStack](../../helm/charts/clickstack/), [workload gateway](../../clusters/aws-dev-workload/values/otel-gateway.yaml)

C1 준비 시점의 구현·검증 결과와 실행 전 관문은 [자동 관측 운영 안내](../operations/automatic-workload-observability.md)에 기록했다. 당시 모든 기존 환경의 activation은 off였으며, 이후 C2 결과와 최종 활성화 PR 상태는 아래 최신 적용 기록을 따른다. 새 CI는 추가하지 않았다.


2026-11-06 적용 상태: [C2 결과·최종 활성화 관문](../operations/workload-observability-activation.md#c2-배포-결과와-최종-활성화-pr-2026-11-06)을 따른다. 기존 결정을 구현하며 새 아키텍처나 권한 확대는 채택하지 않았다.
