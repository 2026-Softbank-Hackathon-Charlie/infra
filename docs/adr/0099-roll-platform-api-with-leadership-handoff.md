# 0099. 관리 플랫폼 API를 리더 넘겨주기와 함께 롤링 업데이트한다

- 날짜: 2026-11-08
- 상태: 채택 — 차트는 기본값 꺼짐으로 넣고, 켜는 것은 아래 순서의 별도 PR로 한다
- 관련: platform ADR 0065(리더 자격과 넘겨주기), [0026 게이트웨이 한 대](https://github.com/2026-Softbank-Hackathon-Charlie/platform/blob/main/docs/adr/0026-gateway-single-replica.md)

## 상황

`charlie-management-hopcharlie-platform`은 자동 동기화라서 infra PR이 병합되면 곧 배포된다. API pod 템플릿이 바뀌는 PR(플랫폼 이미지, `hopai.image`의 `checksum/hopai`, `deploy.backendTemplates`, parity와 onprem 설정의 checksum)마다 API Deployment가 `Recreate`로 다시 뜬다. 2026-11-08 관리 클러스터에서는 94분 동안 API ReplicaSet이 6개 생겼고, 그때마다 `https://api.hopcharlie.dev`가 1~2분 503이었다.

지금 차트에는 `terminationGracePeriodSeconds`가 없어 기본값 30초다(실제 Deployment에서 확인). API는 이전 실행을 15분까지 기다리려 하지만 30초 뒤 SIGKILL로 끝나고, 쓰기 보류 안에서 끊긴 실행은 사람이 resume할 때까지 보류가 남는다.

platform ADR 0065는 API 프로세스 둘이 잠시 겹쳐도 되게 했다. 리더 자격(플랫폼 PostgreSQL의 advisory lock)을 가진 프로세스만 실행과 배포와 루프를 돌리고, 다른 프로세스는 읽기를 직접 처리하고 나머지 요청을 리더에게 넘긴다. 리더는 SIGTERM을 받으면 실행을 안전한 지점에서 멈추거나 끝까지 한 뒤 자격을 놓고, 새 리더가 넘겨받은 실행을 이어 간다.

## 결정

### 차트(`helm/charts/hopcharlie-platform`)

`api.rollingHandoff`(boolean, 기본 `false`)를 둔다. 켜면 API Deployment가 이렇게 바뀐다. 끄면 지금과 같다.

- `strategy: RollingUpdate`, `maxSurge: 1`, `maxUnavailable: 0`. 새 pod가 Ready가 된 뒤에 옛 pod가 끝난다. `replicas`는 1 그대로다.
- `podAffinity`(필수, `kubernetes.io/hostname`, API 라벨): 새 pod는 옛 pod와 같은 노드에 뜬다. 실행 상태 볼륨 `hopcharlie-api-data`가 ReadWriteOnce EBS라서 두 pod가 같이 붙이려면 같은 노드여야 한다. 게이트웨이의 `exclusiveState`와 같은 방식이다. 옛 pod가 없으면 자기 자신과의 affinity로 아무 노드에나 뜬다.
- `terminationGracePeriodSeconds: 900`, 인자 `-shutdown-wait 10m -shutdown-delay 20s`. 넘겨주기는 배포 30초, 실행 10분, 취소 뒤 3분, 루프 30초, HTTP 10초 안에 끝나므로 900초 안에 들어간다. `-shutdown-delay 20s`는 SIGTERM 뒤에도 20초 동안 요청을 처리해서 ALB가 옛 대상을 빼는 동안 오는 요청을 받는다.
- env `POD_IP`(downward API)와 `PLATFORM_LEADER_ADDR=$(POD_IP):8080`: 다른 pod가 리더 요청을 넘기는 주소다. 같은 네임스페이스 안의 pod 사이 통신은 baseline NetworkPolicy가 이미 허용한다.

### 네임스페이스(`helm/charts/cluster-baseline`)

`podReadinessGateNamespaces`에 든 네임스페이스에 `elbv2.k8s.aws/pod-readiness-gate-inject: enabled`를 단다. 관리 클러스터 값에 `charlie-platform`을 넣는다. Ingress가 target type ip라서, 이 라벨이 없으면 새 pod가 ALB에서 healthy가 되기 전에 Ready가 되고 옛 pod가 빠지면서 ALB에 healthy 대상이 없어진다. 라벨이 있으면 aws-load-balancer-controller가 새 pod에 readiness gate를 넣어 대상이 healthy일 때만 Ready가 된다. 워크로드 클러스터의 `traefik` 네임스페이스(ADR 0058)와 같은 방식이다. 이 라벨은 지금 `Recreate`인 API, 게이트웨이와 콘솔에도 적용되지만 Recreate에서는 Ready가 ALB healthy까지 늦어질 뿐 끊김은 늘지 않는다. 대신 이 네임스페이스의 pod 생성이 컨트롤러의 webhook에 기대게 된다.

### 켜는 순서

리더 자격을 모르는 옛 이미지와 새 이미지가 겹치면 둘 다 실행과 배포를 돌리므로, 반드시 아래 순서로 한다.

1. 이 PR을 병합한다. 바뀌는 것은 `charlie-platform` 네임스페이스 라벨뿐이다(baseline 동기화). `kubectl get ns charlie-platform --show-labels`로 라벨을 확인한다.
2. platform PR(ADR 0065)을 병합하고 이미지를 만든다. `images.platform`을 그 이미지로 바꾸는 infra PR을 병합한다. 이 배포는 아직 `Recreate`라서 마지막으로 한 번 끊긴다. 새 API 로그에 `"msg":"leading"`이 나오는지 확인한다. 새 API pod의 `spec.readinessGates`에 `target-health.elbv2.k8s.aws/...`가 있는지도 본다.
3. `clusters/aws-dev-management/values/hopcharlie-platform.yaml`에 `"api": {"rollingHandoff": true}`를 넣는 PR을 병합한다. 이 배포부터 끊기지 않는다. 옛 pod 로그에 `handing over`, `leadership released`, 새 pod 로그에 `leading`이 나오는지 확인한다.

되돌릴 때는 3의 값을 `false`로 바꾼다. 리더 자격이 있는 이미지끼리는 Recreate로 바뀌어도 안전하다. 2의 이미지를 되돌리려면 먼저 3을 되돌린다.

## 범위 밖

- 게이트웨이는 바꾸지 않는다. 게이트웨이 pod 템플릿은 `images.platform`, `gateway.*`, `appDomain`, `awsRegion`, `parameterPrefix`, `images.awsCli`만 쓰므로 그 밖의 infra 변경으로는 재시작하지 않는다. 플랫폼 이미지 교체 때는 지금처럼 끊긴다(platform ADR 0026, 약 25~45초). 이미지를 API와 나누는 것이 다음 단계다.
- PodDisruptionBudget은 두지 않는다. pod 하나에서는 노드 drain만 막고 롤링 업데이트에는 효과가 없다.
- API를 상시 두 대로 두는 것, 실행 상태를 DB로 옮겨 노드 제약을 없애는 것.

## 검증

- `scripts/check-helm.py`: 관리 값의 기본 렌더가 `Recreate`이고 affinity와 `POD_IP`가 없는 것, `rollingHandoff: true`의 전략, affinity, grace, env 순서, 인자, 문자열 `"true"`를 거부하는 것, 관리 클러스터 baseline에서 `charlie-platform`만 readiness gate 라벨을 갖는 것.
- kind 검증은 platform ADR 0065에 있다. 같은 전략과 affinity로 세 번 롤아웃하는 동안 GET 1311건, POST 385건이 모두 성공했고, Recreate 한 번에서는 GET 52건과 POST 1건이 실패했다.
- 2026-11-08 관리 클러스터 읽기 전용 확인: 네임스페이스 quota의 `limits.cpu` 사용 2100m/6, `limits.memory` 1856Mi/8Gi라서 롤링 중 API pod 하나(cpu 1, 메모리 1Gi)가 더 들어간다. API가 있는 2c 노드는 둘이고 PV는 2c다.
