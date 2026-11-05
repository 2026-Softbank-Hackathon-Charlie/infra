# 0047. AWS·GCP DB 이전은 전용 Tailscale 프록시와 임시 CNPG 권한으로 연결한다

- 날짜: 2026-11-05
- 상태: 제안 — 코드·PR 준비 범위. 설치·권한 부여·실제 이전은 별도 승인 대상
- 관련 ADR: [0037](0037-connect-aws-gateway-to-gke-apps-with-tailscale-routers.md), [0039](0039-add-gke-app-environment.md), [0044](0044-platform-reads-gke-with-token-kubeconfig.md), [0046](0046-use-strict-network-policy-on-aws-workload.md)

## 배경과 제약

[infra #47](https://github.com/2026-Softbank-Hackathon-Charlie/infra/issues/47)은 DB 복제·역복제·엔진 SQL·Kubernetes API 경로를 구분한다. 기존 두 라우터는 앱 HTTP TCP 80용이다. DB 준비와 웹앱 접속 성공은 실제 데이터 복제 성공을 뜻하지 않는다.

현재 pinned migration-engine `c3366582a955`는 이미 있는 target standby를 재사용한다. `target.manifest`를 생략하면 target이 없을 때 생성하지 않고 실패하며, primary인 target은 거절한다. 이 경로의 Kubernetes 작업은 지정 CNPG Cluster의 `get`, `patch`뿐이다. 엔진은 Secret을 복사하지 않는다. 기존 `hopcharlie-migrator`의 앱·Secret·PVC 삭제 권한까지 부여할 필요가 없다.

확인한 소스 계약은 infra `749c88e`와 위 엔진 버전 기준이다. 실제 DB·클러스터·tailnet 정책·노드 여유·TLS 인증서는 이번 준비에서 조회하지 않았다.

## 검토한 대안

- 기존 앱 라우터에 DB 경로 추가: 설정은 작지만 복제와 HTTP가 같은 micro VM 경로를 공유한다. #47의 분리 원칙에 따라 제외한다.
- DB용 라우터 VM 두 대 추가: 기존 구성을 복사할 수 있으나 추가 VM·디스크 비용과 장애점이 생긴다.
- workload operator 두 개와 API sidecar: API에 TUN/NET_ADMIN을 추가하거나 SQL 클라이언트의 SOCKS 지원을 바꿔야 한다. 이번 범위에서 제외한다.
- 세 클러스터의 operator ingress/egress Service: 앱 프로세스·VM·기존 라우터를 변경하지 않고 local Kubernetes DNS를 사용한다. 선택한다.
- 기존 광범위 migrator 상시 바인딩: standby를 준비해 둔 실행에 필요하지 않은 권한까지 유지하므로 제외한다.

## 결정과 이유

### DB 경로

Tailscale operator Helm chart `1.104.4`를 management EKS, workload EKS, workload GKE에 각각 설치하는 예제를 제공한다. 기본값은 꺼져 있다. GKE 앱용 Argo 권한을 확대하지 않고 승인된 운영자 컨텍스트로 독립 설치한다. Terraform state와 Helm values에 OAuth 비밀값을 넣지 않는다.

- workload마다 CNPG primary label을 선택하는 별도 ingress Service 1개를 둔다. replica cluster에서도 이 라벨은 WAL을 받는 designated primary를 선택한다. CNPG가 만든 `-rw` Service를 채택하지 않는다.
- workload마다 상대 DB로 향하는 egress Service 1개, management에는 양쪽 DB용 egress Service 2개를 둔다.
- API와 DB는 `db-eks.hopcharlie-db-network.svc.cluster.local`, `db-gke.hopcharlie-db-network.svc.cluster.local` 중 해당 local Service로 접속한다. 일반 Pod에 tailnet DNS·라우팅이 생겼다고 가정하지 않는다.
- standalone 기준 operator 3개와 proxy 6개, 합계 9개 Pod다. 새 VM·노드 자동 증설은 포함하지 않는다. resources는 초기 요청값이며 실제 CPU·메모리·직접 연결/DERP 처리량을 설치 전에 확인한다.
- Tailscale transport에는 HTTPS·DNS·UDP가 필요하다. 애플리케이션 데이터 grant는 필요한 DB 쌍과 관리 엔진에서 TCP 5432만 허용한다. 기존 allow-all ACL/grant가 남으면 새 grant를 추가해도 격리가 되지 않는다.
- 신규 namespace `hopcharlie-db-network`는 앱이 사용할 수 없는 보호 namespace에 포함한다. AWS strict CNI를 위해 namespace 정책과 proxy별 허용 정책을 함께 제공한다. DB/API namespace의 기존 DNS·API·CNPG guardrails가 먼저 준비돼 있어야 한다.
- 기존 플랫폼 `egressPorts`의 광범위 5432 허용은 이 변경으로 줄이지 않는다. NetworkPolicy는 합산되므로 새 정책을 전체 플랫폼의 완전한 outbound 격리로 설명하지 않는다.

### 이전 권한과 DB 준비

운영자는 기존 source와 **별도 target standby**를 먼저 준비한다. 기존 GCP primary를 덮어쓰지 않는다. 양방향 topology, CA·replication 인증서, source 앱 비밀번호, DB 사용자 권한은 별도 준비·검증 대상이다. 앱이 소유한 Secret을 엔진이 읽도록 권한을 넓히지 않는다.

`scripts/db-migration-access.py`는 기본 render-only이고, 승인된 운영자가 명시적으로 grant/revoke한다. 기존 `charlie-system/hopcharlie-platform`에 해당 namespace·CNPG 이름의 `get`, `patch`만 부여한다. patch는 promotion 필드만이 아닌 해당 CNPG 객체 전체 필드에 적용된다. Secret·Cluster 생성·cluster RBAC·새 토큰 발급은 제외한다. 실행 세션의 소유권·정확한 규칙과 UID를 확인해 자신의 RoleBinding부터 회수한다. Kubernetes RBAC에 TTL은 없으며 종료·취소 뒤 명시적 revoke가 필요하다.

기존 EKS/GKE kubeconfig와 443 경로를 재사용한다. ADR0044는 읽기 전용 GKE 접근 준비이며, 임시 RoleBinding이 붙어야 위 CNPG patch를 할 수 있다. 코드 병합과 실제 SSM·클러스터 반영을 구분한다.

### TLS·GitOps 경계

프록시는 PostgreSQL TLS를 종료하지 않는다. 복제의 `verify-full`은 실제 접속 이름과 원격 서버 인증서 SAN이 일치해야 한다. 필요하면 승인된 CNPG 설정에 `serverAltDNSNames`로 local egress DNS를 추가한다. CA·client 인증서/키는 운영자가 준비하며 저장소에 넣지 않는다. 검증 우회를 위해 `verify-full`을 낮추지 않는다. 현재 플랫폼 draft DSN의 `sslmode=require`와 복제 연결의 hostname 검증은 별개다.

원본 앱의 Argo self-heal이 엔진의 `spec.replica` 변경을 되돌리지 않도록 실제 이전 전에 조정해야 한다. GitOps에 반영할 최종 topology와 동기화 재개 절차가 없으면 전환하지 않는다. 이번 차트는 CNPG Cluster·데이터·기존 앱 Application을 수정하지 않는다.

## 영향과 후속 작업

- 기대 효과: 기존 앱 트래픽 경로와 DB 복제를 분리하고, 엔진 실행 권한을 해당 DB로 제한한다.
- 위험: operator는 클러스터 자원을 관리하는 컨트롤러이며 별도 설치 승인 대상이다. 고정 버전의 proxy/init 컨테이너 기본값은 privileged이며 전용 namespace의 Pod Security 허용 여부를 검토해야 한다. API Pod 권한을 늘리지는 않지만 노드 CPU·메모리는 앱과 공유한다. OAuth·tailnet policy·CNI SNAT·인증서·노드 여유·단일 proxy 장애와 처리량은 실제 검증이 필요하다.
- 실행 전: 운영자 kubeconfig, operator 인증, 기존 guardrails, 고유 tailnet 이름, 양방향 grant, API DSN 허용 suffix와 catalog를 준비한다.
- 검증: 실제 5432·443 도달성과 TLS → standby catch-up → 제한된 권한 부여 → 시험 데이터 이전·검증·전환 → 원복 가능 경계 확인 → 임시 권한 회수 순서다. 운영 DB 삭제·덮어쓰기·자동 역전환은 포함하지 않는다.
- 본 PR 완료는 경로·권한 **코드 준비 완료**다. #47의 실제 복제·전환 시험 완료와 구분한다.

## 관련 자료

- [실행 절차](../runbooks/aws-gcp-db-migration.md)
- [Tailscale operator 설치](https://tailscale.com/docs/kubernetes-operator/install-operator), [egress](https://tailscale.com/docs/kubernetes-operator/egress/access-tailnet-service)
- [Tailscale 고정 chart](https://github.com/tailscale/tailscale/tree/v1.104.4/cmd/k8s-operator/deploy/chart)
- [CNPG labels](https://cloudnative-pg.io/docs/1.28/labels_annotations/), [인증서](https://cloudnative-pg.io/docs/1.28/certificates/)
- [Kubernetes NetworkPolicy](https://kubernetes.io/docs/concepts/services-networking/network-policies/)
