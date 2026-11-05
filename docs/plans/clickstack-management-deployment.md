# 관리 클러스터 ClickStack 설치 계획 (C)

- 날짜: 2026-11-05
- 상태: 독립 계획 리뷰 GREEN 후 사용자 C 개발 승인. CI 코드 구현 중이며 실제 실행은 미승인/미실행.
- 분류: 새 CI 배포 경로, EKS 권한, Secret, CRD/operator, PVC 및 실제 로그 수집에 영향을 주는 작업. 전체 계획·독립 리뷰·명시 승인을 적용한다.
- 관련 결정: [ADR0050](../adr/0050-adopt-clickstack-for-hackathon-observability.md), [ADR0051 제안](../adr/0051-bootstrap-management-clickstack-from-ci.md), [ADR0047](../adr/0047-run-management-scaleout-through-ci.md).

## 목표·완료 기준과 현황

관리 EKS에 HyperDX·ClickHouse/Keeper·MongoDB·중앙 Collector와 관리 gateway/agent를 설치한다. HyperDX 로그·트레이스 검색과 외부 Prometheus 조회를 확인한다. 실제 앱 분산 trace 계측은 포함하지 않으며 합성 데이터와 구분한다.

- PR #64는 기본 비활성화·수동 sync의 로컬 chart/GitOps 준비다. PR 머지만으로 설치되지 않는다. 사용자 merge 후 정확한 main SHA를 고정한다.
- 증설 CI plan 41991039778/apply 41991786700은 성공했다. 2026-11-05 08:16 UTC 기준 노드3대 Ready, 기존 워크로드 정상/Pending 없음, CPU requests 여유3.11코어다. 현재는 이 기록을 바탕으로 계획하며 실행 전 재확인한다.
- 고정 버전: chart3.5.0 기반, HyperDX/Collector2.39.1, operator1.1.0, MongoDB8.0.17, OTel chart0.175.0/contrib0.161.0. container runtime 검증은 미완료다.
- CI OIDC 역할은 기존 AWS 관리자 역할이다. 로컬 운영자 입력에는 CI 역할이 없으며 실제 EKS access mapping은 미조회다. AWS 관리자 권한을 Kubernetes 권한과 혼동하지 않는다.

완료 기준: 승인한 네 앱만 등록·sync, DB/중앙/gateway/agent Ready와 PVC Bound, HyperDX 로그인 및 Prometheus 쿼리 성공, 합성 로그100개/trace10개 저장·상호 참조 확인, 제한된 Pod 재시작 후 데이터 유지, 기존 서비스 정상, 임시 CI access entry 제거 확인. queue는 PVC Bound·마운트/구성까지만 확인하며 실제 backlog 재생/유실 복구는 별도 검증으로 남긴다. 어느 관문이든 실패하면 이후 설치를 중단하고 미완료로 보고한다.

## 대상과 제외 범위

- 계정525091405886, ap-northeast-2, `charlie-dev-management`. 리소스 namespace는 `observability`, Argo 관리 객체는 `argocd`다.
- AppProject `charlie-observability`; Application은 `charlie-management-clickstack-operators`, `charlie-management-clickstack`, `charlie-management-observability-gateway`, `charlie-management-observability-agent` 네 개만 허용한다.
- workload/GCP/온프레미스 쓰기·연결 변경, public Ingress/LB/DNS, Loki/Grafana 제거, Prometheus 토큰 교체, 기존 앱 SDK/환경변수 변경, CI/Argo 이벤트 adapter, DB HA/백업 서비스 추가는 제외한다.
- 실제 설치는 CI와 Argo가 수행한다. 로컬 Helm install/apply 및 root Application 일괄 sync는 하지 않는다.

## 제안 실행 경로와 권한 경계

기존 GitHub main 전용 OIDC 역할 → 기존 SSM bridge로 EKS API 터널 → 제한된 bootstrap helper → Argo Application operation.sync 순서를 사용한다. 새 Argo API admin token이나 장기 AWS 키를 발급하지 않는다.

1. 새 수동 workflow `clickstack-management.yml`을 후속 PR에 구현한다. `action=preflight|deploy|cleanup-access` 중 기본은 preflight, expected_sha 필수, deploy는 명시 confirm을 요구한다. PR/push에서는 AWS 인증 없는 정적 검사만 실행한다. OIDC 전에 main·repo·SHA·대상 확인을 끝내고, AWS Terraform과 동일한 `charlie-terraform-apply` concurrency group을 사용한다.
2. CI 역할 ARN을 계정·repo 설정과 대조하고 실제 access entry 존재 여부를 확인한다. **기존 mapping이 있으면 자동 변경·삭제하지 않고 중단**한다. mapping이 없다면 deploy 승인 범위에서만 STANDARD EKS access entry를 만들고 cluster 범위 AmazonEKSClusterAdminPolicy를 연결한다. principal은 `arn:aws:iam::525091405886:role/charlie-dev-github-terraform` 하나다. Terraform state/주소를 이전하거나 기존 operator/Argo 접근을 바꾸지 않는다.
3. 임시 mapping에 owner/run ID 태그를 붙인다. CI helper의 allowlist는 실제 권한의 상한이 아니다. 연결 중인 CI 역할의 다른 세션에도 Kubernetes cluster-admin이 생기며 **모든 namespace의 Secret 및 리소스에 접근 가능한 유효 권한**임을 명시적으로 승인받는다.
4. 정상/실패 종료에서 자신이 만든 mapping만 제거하고 재조회한다. GitHub always cleanup은 runner 손실·강제 중단 시 보장되지 않는다. 별도 cleanup-access 액션은 숫자 cleanup_owner_run_id를 필수 입력받고, 고정 cluster/principal·owner tag·해당 저장소의 이전 bootstrap workflow run ID를 대조한다. 현재 cleanup job의 run ID와 혼동하지 않는다. 요청한 이전 실행의 자원임이 일치할 때만 제거하며, 불일치하거나 다른 policy/group이 추가돼 있으면 중단한다. 삭제 API의 eventual consistency를 고려해 mapping 부재와 제한된 권한 재확인을 최대120초 수행한다. 잔존/해제 미확인 시 배포 완료로 보고하지 않고 사용자에게 수동 cleanup 호출을 안내한다. 자동 TTL이 있다고 표현하지 않는다.
5. preflight 액션은 access entry를 만들지 않는다. AWS/Kubernetes 조회가 가능한 범위까지만 조사하고, 미인증 클러스터 조회는 미검증으로 남긴다. 서버 dry-run도 deploy 승인 전 실행하지 않는다.

이 임시 cluster-admin 방식은 승인 전 제안이며 [ADR0051](../adr/0051-bootstrap-management-clickstack-from-ci.md)에 대안·위험을 기록한다. 새로운 영구 IAM/EKS 권한은 추가하지 않는다.

## 생성할 Secret과 자격 증명

기존 Secret을 덮어쓰지 않는다. 초기 생성은 고엔트로피 값과 아래 allowlist만 사용한다. 재시도는 자신이 만든 owner label과 key/일관성 확인을 통과한 Secret만 재사용한다. 소유권이 없거나 부분 bundle의 일관성을 복구할 수 없으면 중단하고 자동 회전·삭제하지 않는다. 생성 순서는 CA(bundle ID 확정) → ClickHouse/Mongo/collector canonical 인증 → 이 값으로 파생한 runtime/source/client/TLS → demo login이다. 각 객체의 같은 bundle ID와 key 일관성을 확인한다. 파생 Secret을 먼저 만들어 canonical 값 없이 부분 생성되지 않도록 테스트한다.

| 이름 | 내용·용도 |
| --- | --- |
| clickstack-runtime | Mongo URI, 세션 비밀, 32바이트 TOKEN_ENCRYPTION_KEY의 base64, HyperDX 내부 API key |
| clickstack-clickhouse-auth | read/ingest/default password |
| clickstack-collector-auth | ingest password와 관리 클러스터 OTLP bearer token |
| clickstack-mongodb-auth | hyperdx 사용자의 password |
| clickstack-ui-config | 실제 read password를 넣은 connections.json 및 sources.json |
| clickstack-otlp-tls | 중앙 Collector TLS 인증서·개인키 |
| observability-client-auth | 관리 gateway/agent용 동일 OTLP token |
| observability-client-ca | CA public certificate |
| observability-gateway-tls | gateway TLS 인증서·개인키 |
| clickstack-bootstrap-ca | 후속 인증서 재발급을 위한 CA 인증서·개인키. chart/agent에 마운트하지 않음 |
| clickstack-demo-login | 최초 HyperDX 데모 계정 email/password. 사용자 로그인 인계용이며 로그에 표시하지 않음 |

- issuer CA30일·leaf7일을 제안한다. SAN은 실제 중앙/gateway Service DNS와 일치시킨다. 갱신 자동화는 이번 범위 밖이며 만료일·후속 회전 필요를 보고한다. TLS 검증을 끄지 않는다.
- Mongo URI와 ClickHouse 연결은 실제 render의 service/user/database와 맞추고 비밀번호를 URL 인코딩한다. datasource fixture의 FIXTURE 문자열을 운영 값으로 사용하지 않는다.
- DB/session/encryption/API/OTLP 비밀값에는 자동 만료가 없으며 이후 명시적인 회전·폐기가 필요하다. 재시도로 자동 회전하지 않으며 TOKEN_ENCRYPTION_KEY 변경은 기존 암호화 데이터의 가독성에 영향을 주므로 별도 작업으로 취급한다.
- 11개 이름의 Secret get/create와 자신이 만든 bundle의 필요한 metadata 확인만 helper에 허용한다. 기존 Grafana/Prometheus/Argo Secret 값은 읽지 않는다. operator의 내부 Secret 생성/관리는 별도 RBAC 권한으로 동작하며 기존 차트에 선언한 범위를 실행 승인에 포함한다.
- Secret 값은 Git·argv·로그·artifact에 넣지 않는다. 가능하면 메모리/파이프를 사용하고 필요한 임시 key 파일은 전용0700 디렉터리·0600으로 제한하고 삭제한다. shell tracing은 끈다. 값이나 raw API 오류를 stdout에 덤프하지 않는다.
- 데모 계정은 고정 버전의 `/register/password`, `/login/password` 구현을 확인해 최초 팀에만 생성한다. 재시도 시 같은 owned demo Secret으로 먼저 로그인한다. 자신의 계정으로 확인되지 않는 기존 팀/계정이 있으면 비밀번호 재설정/기존 사용자 교체 없이 중단한다. 발급된 로그인 세션은 메모리에서만 사용하고 로그아웃한다. Secret 원문 전달은 별도 승인된 사용자 조회로 남긴다.

## 구현할 파일·검증

현재 PR #64 정리는 문서/번호와 최신 main 반영만 수행한다. 아래 **새 구현은 계획 승인 후 별도 후속 PR**로 작성하며 merge는 사용자가 한다.

- `.github/workflows/clickstack-management.yml`: 수동 action·SHA·confirm·동시 실행·OIDC·단계/cleanup.
- `scripts/clickstack-management.py` 및 테스트: 대상/소유권/객체 allowlist, access lifecycle, bundle 일관성, Argo 등록/선택 sync, health/합성 검증, 비밀값 없는 결과 요약. 기존 eks-access helper를 재사용하며 임의 SSM target을 입력받지 않는다.
- `helm/gitops/values.schema.json`, `values.yaml`, `templates/observability.yaml`: `observability.destinationTargets`를 도입해 C1 AppProject 목적지를 management로 제한한다. agent/gateway target이 이 목록 밖이면 render 실패. 기존 다중 대상 opt-in 검증도 갱신한다. 기존 default 관측 비활성화는 유지한다.
- `scripts/observability-smoke.py`: CI loopback port-forward의 연결 주소와 TLS 인증서 DNS 검증을 분리할 경우 검증을 유지하는 좁은 adapter를 구현·테스트한다. insecure/redirect로 token을 넘기지 않는다.
- 인증서 도구/라이브러리와 kubectl/SSM plugin의 Linux 정확 버전·checksum은 구현 전 공개 원본에서 확인하고 고정한다. 기존 Helm/Python pin을 재사용한다.
- README/inventory와 계획·제안 ADR 상태를 갱신한다. 테스트는 fake AWS/kubectl/Argo와 합성 Secret만 사용한다.

검증: 잘못된 account/cluster/repo/SHA/action, 기존 access entry, owner 불일치, 기존 Secret, 부분 bundle 불일치, TTL/SAN/CA 오류, fixture 누출, 허용 밖 객체/삭제/prune/force, 단계 실패 시 다음 sync 금지, cleanup 실패 보고, 비밀값 stdout/argv/artifact 금지, 신규 scope 밖 AppProject 거부. 기존 Helm 전체·workflow gate·inventory 검사를 함께 수행한다. management-only destination과 실제 두 target 메타데이터 입력, cleanup_owner_run_id 검증·이전 run 대조를 fake API로 검증한다.

## 실제 실행 순서와 관문

단계별 wait는 이름 접두사만으로 대상을 선택하지 않는다. 등록한 CR/Deployment의 UID에서 StatefulSet/ReplicaSet→Pod ownerReference 체인을 확인하고, 세대 observedGeneration과 desired/ready를 비교한다. 예상 소유 체인이 없거나 기존 타인 객체를 가리키면 중단한다. CRD/operator120초, DB900초, 중앙600초, gateway/agent 각300초, 재시작 단계 각300초의 상한을 두며 CI 전체90분·helper70분 내에서 정리 시간을 확보한다.

단일 source 중앙 앱은 operation.sync.revision=승인 Git SHA로 고정한다. multi-source operator/gateway/agent는 source 순서에 맞춘 revisions 배열(각 chart pin, values Git SHA)을 사용하고 성공 결과의 revisions도 대조한다. 설치된 Argo3.5.3 CRD가 해당 필드를 지원하는지 dry-run/합성 계약에서 확인하며 단일 revision을 chart source에 잘못 덮어쓰지 않는다. prune/force/replace는 쓰지 않는다.

1. 사용자 개발·실행 범위 승인 → 후속 코드 PR 작성·검사 → 사용자 merge → 승인 main SHA 고정. 실행 전에 노드3대 Ready/실제 requests·affinity·PVC AZ 제약, 기존 workload, CRD/controller·앱/Secret 이름 충돌, CI/Argo 권한 및 기존 live 앱 정책을 다시 확인한다.
2. 필수 GitOps targets 입력은 config/project.json의 고정 management/workload 이름에 대해 AWS EKS DescribeCluster에서 endpoint/name/region/vpc_id만 읽어 구성한다. workload Kubernetes API에는 접근하지 않으며 destinationTargets/agentTargets/gatewayTargets는 management 하나다. fixture나 가짜 endpoint를 쓰지 않는다. `helm template --show-only templates/observability.yaml`을 사용하고 출력이 승인한 AppProject1+Application4 및 management 목적지만 있는지 검사한다. 임시 access 생성 후 SSM 터널을 열고 11개 bundle을 생성한다. AppProject와 **네 Application만** 고정 Git SHA로 등록한다. 외부 chart revision은 기존 pin을 유지한다. GitOps 전체 render를 통째로 apply하지 않는다.
3. operator 앱만 sync → CRD Established 및 두 operator Ready 확인. 실패하면 중앙 앱을 sync하지 않는다.
4. 중앙 앱에서 ClickHouseCluster/KeeperCluster/MongoDBCommunity만 selective sync → 실제 생성 StatefulSet/Pod·PVC·DB readiness 확인. 일반적인 Argo CR health가 Healthy라고 가정하지 않는다. 아직 중앙 Deployment를 기동하지 않는다.
5. 중앙 앱 full sync → HyperDX/Collector startup, DB 인증·schema migration 및 서비스 readiness. MongoDB8/고정 Collector 조합이 실패하면 이미지/권한을 임의 변경하지 않고 중단한다.
6. gateway sync → TLS/token 수신·중앙 전달 확인. 합성 로그100개/trace10개를 전송하고 고유 run_id/event_id로 ClickHouse 저장 개수·중복·trace 연결을 조회한다. 서비스 port-forward는 loopback만 사용하고 TLS CA/DNS 검증을 유지한다.
7. HyperDX 데모 계정 최초 생성·로그인, 기본 로그/트레이스 source 및 외부 Prometheus `up` 조회를 확인한다. 이 조회는 metrics 저장소 이전이나 Prometheus 알림 연동 완료가 아니다.
8. agent 앱 sync는 `charlie-platform`의 신규 로그 수집을 승인한 경우에만 수행한다. start_at=end, 관측 스택 자체 로그 제외, DaemonSet UID0/hostPath 접근을 명시한다. namespace 필터는 호스트 파일 접근의 보안 경계가 아니다. 자동 검증은 합성 데이터/최소 상태를 기본으로 하고 제품 로그 본문을 덤프하지 않는다.
9. 신규 HyperDX, ClickHouse, MongoDB Pod를 각각 한 번씩 순차 재시작한다. owner UID가 확인된 단일 Pod 삭제와 controller 재생성만 허용하고 같은 PVC UID/volumeName 사용·Ready 및 run_id 데이터 개수/데모 로그인·팀/source 상태를 재확인한다. 세 개를 동시에 중단하지 않는다. queue backlog 복구·내구성은 이 검사로 입증했다고 쓰지 않는다. 기존 앱/Prometheus Pod, Keeper, 노드와 PVC는 재시작·삭제하지 않으며 exporter 장애 주입도 제외한다.
10. 기존 workload 정상과 자원 사용량을 재확인하고, 임시 access/터널/port-forward/test 세션을 정리한다. 잔존/미검증 항목이 있으면 명시한다.

## 비용·실패·복구

새 PVC 의도는 중앙33Gi+gateway2Gi=35Gi다. 서울 gp3 기본 단가 $0.0912/GB·월로 단순 저장분 약 $3.19/월이며 operator 추가 볼륨·IOPS·트래픽은 별도다. 노드 증설 비용 약 $88.66/730시간은 이미 별도 조치로 발생한다. 이번에는 새 노드·LB·NAT·EIP를 추가하지 않는다.

실패 시 이후 단계 sync만 중단한다. CRD/operator/DB/PVC/Secret을 자동 삭제·prune하거나 노드를 축소하지 않는다. 성공한 단계와 잔존 객체/volume·추가 비용을 보고한다. DB/queue를 보존한 채 원인 검토 후 재시도한다. 후속 별도 시나리오 없이는 queue backlog 복구를 완료로 보고하지 않는다. 부트스트랩 access만 자신이 생성한 것에 한해 정리한다. Secret 회전, 데이터 복구, 제품/이미지 변경은 별도 범위다.

## 승인 요청의 정확한 범위

계획 승인 전: 문서와 PR #64 정리·로컬 조사/합성 검사만 수행한다. 승인 요청은 후속 CI/범위 제한 코드 구현·PR 준비와, 사용자 merge 후 정확한 SHA의 수동 preflight/deploy·실패 시 own access cleanup 실행을 구분해 제시한다.

실행 승인에는 두 EKS(management/workload)의 DescribeCluster endpoint/name/region/vpc_id 읽기, 관리 EKS에 한정한 Kubernetes 메타데이터·권한 mapping 조회, 위 CI 역할의 임시 cluster-admin mapping 생성/삭제, 11개 지정 Secret 발급/저장/own 값 조회, operator CRD/RBAC와 내부 Secret 생성, 약35Gi PVC와 네 Argo 앱 등록/sync, charlie-platform 신규 로그 수집, 데모 계정·합성 데이터 생성/최소 조회 및 owner가 확인된 신규 HyperDX/ClickHouse/MongoDB Pod의 순차 재시작(각1회)을 포함한다. 필요 진단은 새 observability Pod의 최근100줄을 메모리에서 비밀값 필터링하는 범위와 명시한 서비스 포트 확인으로 제한한다. 기존 앱 로그·감사 로그 전체·다른 클라우드·기존 Secret 원문·임의 exec는 제외한다.

사용자가 구현만 승인하면 실제 실행을 하지 않는다. 이 계획 또는 ADR의 제안/채택 상태가 실행 승인을 대신하지 않는다.

## 근거

- [AWS EKS access policy 권한](https://docs.aws.amazon.com/eks/latest/userguide/access-policy-permissions.html)
- [AWS access entry 제거](https://docs.aws.amazon.com/eks/latest/userguide/deleting-access-entries.html)
- [Argo Application operation으로 sync](https://argo-cd.readthedocs.io/en/stable/user-guide/sync-kubectl/)
- [기존 배포 검토](../clickstack-deployment-review.md)

## 독립 계획 리뷰 결과

새 plan-reviewer `review_clickstack_management_deployment`에서 재리뷰 GREEN 판정을 받았고 중대 미해결 지적은 없다. queue backlog 복구를 완료 기준에서 제외하고, owner UID/PVC 기반 순차 재시작·timeout, multi-source revisions, Secret canonical 생성 순서, workload 메타데이터 입력과 management-only 쓰기, 이전 run ID 기반 cleanup을 보완했다.

이는 C 기술 계획의 리뷰 결과이며 사용자 개발/실행 승인이 아니다. 기존 A의 ClickStack15개·전체 Helm 및 inventory460개 검사는 통과했지만 신규 C workflow/helper는 아직 구현하지 않았다. 실제 권한·Secret·sync·런타임 검증도 하지 않았다. 이번 PR 정리 범위의 계획 대비 차이는 최신 main 보존과 ADR0050 번호 정리, 성공한 노드 증설 기록 반영뿐이다. ADR0051은 제안으로 남긴다.

## C 구현 상태

사용자가 `진행해줘`로 CI 코드 구현까지만 승인했다. 별도 `feat/clickstack-management-ci` 브랜치에서 merge된 PR #64를 포함한 main 기준 후속 PR을 준비한다. 새로운 workflow_dispatch·EKS access 변경·운영 Secret 생성·Argo sync는 호출하지 않는다.

버전: Python cryptography50.0.2/cffi2.1.1/pycparser3.12/PyYAML6.0.3의 release hash, Linux kubectl1.35.8 및 SSM plugin1.2.896.0 checksum을 고정했다. synthetic loopback TLS에서 SAN/SNI/CA 검증과 redirect 시 bearer 미전달을 확인한다. run attempt별 태그·event ID는 승인한 소유권/재시도 정책을 구현한 세부 보완이다.

### C 로컬 구현 검증 결과

- C 합성 테스트26개 및 기존 eks-access 테스트7개 통과: preflight 무변경, 잘못된 context/SHA/계정 거부, Secret create-only/부분 재시도/owner 불일치, 임시 mapping 소유권·attempt, 단계 실패 시 다음 sync 차단, cleanup 실패, multi-source revision, strict loopback TLS와 redirect 차단 포함.
- 기존 ClickStack 계약15개 및 전체 Helm 검사 통과. inventory468개·fmt/config·Python 구문·diff 검사 통과.
- 실제 임시 EKS mapping·운영 Secret·Argo operation은 호출하지 않았다. DB/operator 이미지 조합과 live 경로는 실행 승인 후 검증 대상이다. 실제 브라우저 조작 및 queue backlog 복구는 이번 자동 확인 범위 밖이다.
- 새 PR/push workflow는 AWS 인증 없이 client binary 버전/체크섬 및 합성 계약을 검증한다. preflight/deploy/cleanup 수동 CI는 실행 승인 전 호출하지 않는다.

cleanup-access는 chart/Helm 회귀 검사가 실패하거나 외부 chart 다운로드가 불가능해도 별도로 실행할 수 있도록 해당 검사 job을 건너뛴다. main/SHA/confirm/owner 검증과 고정 Python 의존성 검증은 유지한다. preflight/deploy는 합성 계약 및 전체 Helm/inventory/config 검사 성공을 요구한다.
