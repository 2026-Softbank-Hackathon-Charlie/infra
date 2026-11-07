# K3s 관리 연결 참고

- 날짜: 2026-11-07
- 상태: 운영 식별정보가 없는 재사용 참고 문서

운영 식별정보가 없는 공개 template이다. 기존 Operator에 대상 `<TAILNET_FQDN>` TCP6443 전용 ExternalName egress를 만들고 management 소비자만 허용하는 NetworkPolicy를 추가한다. 기존 DB proxy·credential·ACL은 보존한다.

Argo/HopCharlie 각각의 7일 client certificate를 VM의 client CA로 서명하고 `<APP_NAMESPACE>` 권한만 부여한다. CA private key는 반출하지 않는다. namespace admin은 Secret·SA·RBAC 위임 권한을 포함하며 인증서 만료가 위임 자격 전체를 폐기하지 않는다. 수동 갱신/Binding 회수 절차가 필요하다.

Argo는 namespace 한정 cluster Secret과 수동 proof Application을 사용하고 TLS serverName은 원래 VM FQDN을 유지한다. HopCharlie는 이미 선언된 `<KUBE_CONTEXT>`를 사용한다. API 재생성 후 실제 process의 KUBECONFIG 체인·중복 이름·effective endpoint/identity를 비밀 없는 metadata로 검사하고 같은 체인으로 검증한다.

SSM 기존 값은 RAM에서만 rollback용으로 취급한다. version 재검사는 원자적 CAS 보장이 아니다. concurrent writer·예상 밖 자동 sync·충돌 때 중단한다. main merge·DB/앱 트래픽 전환은 별도 범위다.

## 관련 자료

- [프록시 결정](../adr/0025-reuse-tailscale-operator-for-k3s-api.md)
- [자격 결정](../adr/0026-scope-k3s-consumer-certificates.md)
- [검증 차트](../../helm/charts/onprem-connection-check/Chart.yaml)
