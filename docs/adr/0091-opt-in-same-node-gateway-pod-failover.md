# 0091. 게이트웨이 동일 노드 파드 복구를 명시 설정으로 준비한다

- 날짜: 2026-11-08
- 상태: 제안 — 운영 values는 변경하지 않음

## 배경과 제약

트래픽 게이트웨이는 한 Deployment 파드와 gp3 ReadWriteOnce PVC에 의존한다. 파드를 둘로 늘리면 각각의 hold/route가 독립되어 관리 요청을 받은 파드만 쓰기를 막을 수 있다. 플랫폼의 `-exclusive-state`는 동일 노드 로컬 PVC의 flock 소유자만 경로 상태를 실행하고 다른 파드는 Unix socket으로 전달하도록 한다(platform ADR 0061).

Terraform 소스는 AWS management 노드를 `ap-northeast-2a:2, ap-northeast-2c:2`로 정의한다. GKE workload 소스도 있다. 실제 살아 있는 노드/남은 자원은 이 변경에서 조회하지 않았다. RWO 볼륨과 로컬 커널 잠금은 이 노드들을 동시에 사용하는 다중 노드·지역 HA의 근거가 아니다. 새 LB/노드·IAM·Secret·RBAC를 만들지 않는다.

## 검토한 대안

- 독립 gateway replicas=2: hold 우회와 분리된 write backend 때문에 거부한다.
- standby readiness=503: 두 파드 중 하나만 Ready라 Deployment/Argo가 영구 Progressing이 되고, Service의 leader label 갱신에는 별도 권한이 필요하다.
- PostgreSQL shared state/leader lease: 다중 노드로 확장할 수 있지만 durable control 합의와 실제 backend fencing이 함께 필요하다. 기존 credential 노출 범위·권한을 추가하는 별도 설계로 남긴다.
- 동일 노드 릴레이+단일 소유자: 기존 PVC/Service/ALB를 유지하면서 파드 장애와 상태 분리를 검증할 첫 단계로 선택한다.

## 결정

- 기본 `gateway.replicas=1`, `gateway.exclusiveState=false`를 보존한다. production values는 수정하지 않는다.
- replicas는 1 또는 2만 허용하고, 2는 exclusiveState=true 없이 렌더를 실패시킨다. boolean 타입도 검사한다. 활성화 전에 `-exclusive-state` 구현이 포함된 platform 이미지를 별도로 확인해야 한다.
- exclusiveState일 때 동일 gateway label의 required podAffinity를 hostname에 걸어 같은 노드에 둔다. scheduler의 최초 self-affinity 예외로 첫 파드가 배치되고 다음 파드는 같은 노드에서 RWO PVC와 socket을 공유한다. 남는 노드 자원은 미확인이다.
- 전략은 Recreate를 유지한다. 첫 활성화/다운그레이드 때 잠금을 모르는 이전 바이너리와 새 소유자가 동시에 실행되는 것을 막는다. 업그레이드 무중단을 주장하지 않는다.
- 종료 유예는 60초로, platform의 4개 HTTP server drain 각 10초보다 길게 한다. 시간 초과/강제 종료는 unclean marker를 남기며 읽기만 복구하고 쓰기·관리 변경은 운영자 복구까지 차단한다.
- 두 TCP 파드는 소유자의 health를 전달한다. 리더가 없으면 503으로 Ready에서 빠지고, 소유자가 복원되면 둘 다 Ready다. liveness로 복구 marker를 지우거나 강제 재시작시키지 않는다. service account와 automount=false를 보존한다.

## 영향과 운영 승인

이 PR은 코드/차트/회귀 준비만 하며 배포하거나 파드를 늘리지 않는다. 운영 이미지 확인, 같은 PVC의 모든 gateway 교체, replicas/exclusiveState 값 변경과 롤아웃 검증은 AGENTS.md의 구체적인 운영 변경 승인이 필요하다. 새로운 노드/LB 비용을 승인한 것으로 간주하지 않는다.

비정상 소유자 종료 시 원격 DB commit이 여전히 진행 중일 수 있다. 운영자는 실제 DB primary·이전 단계·기존 쓰기 결과와 DB fence를 확인하고, 모든 공유 프로세스를 중지한 다음 `.running`만 제거하고 재시작한다. `.lock`과 상태 JSON은 보존하고 앱별 저장된 hold를 확인 후 release한다. source fence를 끄거나 `PromoteRequested` 이후 자동 DB 롤백으로 우회하지 않는다.

노드/EBS/AZ/리전/AWS 진입점 장애는 남는다. 별도 AI gateway는 변경하지 않으며 기존 AI canary CAS를 다중 인스턴스로 우회하지 않는다. 다중 노드 shared control store+실제 fencing과 지역 밖 진입점은 다음 설계 범위다.

## 검증과 자료

Helm 회귀는 기본 단일 파드/PVC 보존, opt-in 2개/affinity/Recreate, 금지한 0/3/소수/독립2 설정, 신규 서비스·권한 없음과 기존 parity 검사를 실행한다. 기존 parity 테스트를 `make ci-test`에 연결한다. platform PR의 실제 두 프로세스 SIGTERM/SIGKILL/unknown-write 검증이 구현 증거이며 운영 장애 재현 증거는 아니다.

- [gateway chart](../../helm/charts/hopcharlie-platform/templates/gateway.yaml)
- [회귀](../../scripts/tests/test-platform-parity.py)
- [management node 소스](../../terraform/environments/aws/dev/management/main.tf)
- [Kubernetes volume access modes](https://kubernetes.io/docs/concepts/storage/persistent-volumes/#access-modes)
- [Kubernetes self pod affinity](https://kubernetes.io/docs/concepts/scheduling-eviction/assign-pod-node/#inter-pod-affinity-and-anti-affinity)
