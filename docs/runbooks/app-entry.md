# 워크로드 EKS 앱 진입점(Traefik) 실행 순서

[ADR 0058](../adr/0058-share-one-app-entry-per-workload-cluster.md)을 단계마다 승인받아 실행한다. 단계마다 끊김 감시를 켜 두고, 결과를 아래 실행 기록에 남긴다.

준비:

- 관리 클러스터 kubeconfig(`make aws-tunnel`, 기본 `.generated/access/management.kubeconfig`)와 워크로드 kubeconfig(`make aws-tunnel CLUSTER=workload`)
- `kubectl`, `helm` 3.19.1, Python과 PyYAML, origin/main을 체크아웃한 infra 저장소

## 0. 끊김 감시

공개 주소만 읽는다. 1초마다 docker-doom 화면, moimroom 헬스 체크, docker-doom WebSocket 업그레이드(101)를 확인하고 실패만 남긴다.

> 2026-11-07: moimroom 앱은 지웠다. 이 감시를 다시 쓸 때는 moimroom 줄을 빼거나 살아 있는 앱 주소로 바꾼다.

```bash
while true; do
  t=$(date +%H:%M:%S)
  a=$(curl -s -o /dev/null -w '%{http_code}' --max-time 5 https://docker-doom.hopcharlie.dev/vnc.html)
  b=$(curl -s -o /dev/null -w '%{http_code}' --max-time 5 https://moimroom.hopcharlie.dev/healthz)
  c=$(curl -s -o /dev/null -w '%{http_code}' --max-time 2 --http1.1 -H 'Connection: Upgrade' -H 'Upgrade: websocket' \
      -H 'Sec-WebSocket-Version: 13' -H 'Sec-WebSocket-Key: dGhlIHNhbXBsZSBub25jZQ==' -H 'Sec-WebSocket-Protocol: binary' \
      https://docker-doom.hopcharlie.dev/websockify)
  [ "$a$b$c" = 200200101 ] || echo "$t FAIL doom=$a moimroom=$b ws=$c"
  sleep 1
done
```

## 1. Traefik 등록

infra PR(ADR 0058의 Traefik 애드온)이 main에 들어간 뒤 실행한다. merge로 바뀌는 운영 객체는 하나다. 자동 sync인 `charlie-management-hopcharlie-apps`가 AppProject `hopcharlie-apps`에 막는 대상 `!traefik`을 클러스터마다 더한다(앱이 `traefik` 네임스페이스를 쓰지 못하게 하는 것뿐이고 지금 앱에는 영향이 없다). Traefik 자체는 아래 명령으로만 생긴다.

```bash
python3 scripts/register-traefik.py preview   # AppProject diff, Application 서버 dry-run
python3 scripts/register-traefik.py create    # AppProject apply, Application create(자동 sync 없음)
python3 scripts/register-traefik.py sync      # sync 한 번
```

- preview와 create는 live AppProject와 렌더를 비교해, source 저장소 `https://traefik.github.io/charts`와 대상 네임스페이스 `traefik` 추가 말고 다른 차이가 있으면 멈춘다(목록 순서는 보지 않는다).
- 확인:

```bash
kubectl -n traefik get deploy,pods,svc,pdb -o wide
kubectl get ns traefik --show-labels            # elbv2.k8s.aws/pod-readiness-gate-inject=enabled
kubectl get ingressclass traefik
kubectl -n traefik get ingress traefik-no-route   # ADDRESS가 NLB 이름(Traefik이 status를 씀)
aws elbv2 describe-load-balancers --names hopcharlie-apps-eks --query 'LoadBalancers[0].[State.Code,Scheme,DNSName]'
aws elbv2 describe-target-health --target-group-arn <대상 그룹 ARN>   # pod 2개 healthy
```

## 2. 롤링 재시작 시험(NLB를 거쳐)

앱을 옮기기 전에, 3단계 PR보다 먼저 한다. NLB는 관리 서브넷(`10.40.0.0/19`)에서만 받는다. SSM 브리지는 보안 그룹이 443과 DNS만 내보내서 NLB 80에 닿지 않으므로, 관리 클러스터 `default` 네임스페이스에 잠깐 쓰는 probe pod를 띄운다(관리 서브넷 주소, NetworkPolicy 없음). 이미지는 digest로 고정하고 root가 아니며 권한이 없고, `activeDeadlineSeconds`로 15분 뒤 끝난다. 시험마다 지운다.

```yaml
apiVersion: v1
kind: Pod
metadata: {name: traefik-nlb-probe, namespace: default, labels: {app: traefik-nlb-probe}}
spec:
  restartPolicy: Never
  activeDeadlineSeconds: 900
  automountServiceAccountToken: false
  securityContext: {runAsNonRoot: true, runAsUser: 65532, runAsGroup: 65532, seccompProfile: {type: RuntimeDefault}}
  containers:
    - name: probe
      image: curlimages/curl:8.11.1@sha256:c1fe1679c34d9784c1b0d1e5f62ac0a79fca01fb6377cdd33e90473c6f9f9a69
      command: ["/bin/sh", "-c"]
      args:
        - |
          NLB=<NLB DNS 이름>; n=0; fail=0; end=$(( $(date +%s) + 840 ))
          while [ "$(date +%s)" -lt "$end" ]; do
            c=$(curl -s -o /dev/null -w '%{http_code}' --max-time 3 -H 'Host: nonexistent.eks.hopcharlie.internal' "http://$NLB/")
            n=$((n+1)); [ "$c" = 503 ] || { fail=$((fail+1)); echo "$(date -u +%H:%M:%S) FAIL $c"; }
            [ $((n % 60)) -eq 0 ] && echo "$(date -u +%H:%M:%S) rounds=$n failed=$fail"; sleep 1
          done; echo "done rounds=$n failed=$fail"
      resources: {requests: {cpu: 10m, memory: 16Mi}, limits: {memory: 32Mi}}
      securityContext: {allowPrivilegeEscalation: false, readOnlyRootFilesystem: true, capabilities: {drop: ["ALL"]}}
```

1초마다 없는 호스트로 요청하고, 503(어느 Ingress에도 없는 호스트의 답)이 아니면 실패로 센다. `000`은 연결 거부, 끊김, 시간 초과다. 30초쯤 실패가 없는 것을 본 뒤 재시작한다. 첫 설치 pod에는 readiness gate가 없으므로, 재시작 뒤 새 pod에 `target-health.elbv2.k8s.aws/...` readiness gate가 붙었는지와 두 pod가 다른 노드에 있는지도 본다.

```bash
kubectl -n traefik rollout restart deploy/traefik
kubectl -n traefik rollout status deploy/traefik --timeout=15m
kubectl -n traefik get pods -o wide
kubectl -n traefik get pods -o jsonpath='{range .items[*]}{.metadata.name}{" "}{.spec.readinessGates}{"\n"}{end}'
kubectl -n default logs traefik-nlb-probe      # 옛 pod가 끝나고 1분 더 기다린 뒤
kubectl -n default delete pod traefik-nlb-probe
```

실패가 0이어야 한다(2026-11-06 00:54 실행: 240회 중 0). 실패가 있으면 `ports.web.transport.lifeCycle.requestAcceptGraceTimeout`을 30에서 40초로 올리고, `deployment.terminationGracePeriodSeconds`와 등록 해제 지연을 그보다 길게 맞춘 뒤 다시 잰다. 앱 전환(4단계) 뒤에도 같은 시험을 앱 호스트로 한 번 더 한다. Traefik pod가 바뀔 때 그 pod를 지나던 docker-doom `/websockify` 연결은 끊긴다(ADR 0058).

## 3. 내부 DNS와 앱 Ingress

다음 infra PR(워크로드 Terraform의 `hopcharlie.internal`, 앱 차트 Ingress, eks 프로필, Traefik 분산)이 들어가면 main의 Terraform 자동 적용과 Argo CD 자동 sync가 적용한다. Terraform 자동 적용은 runner 대기열에 따라 늦을 수 있으므로 Actions에서 main의 `Charlie infrastructure` 실행이 끝났는지 본다.

- `charlie-workload-traefik`은 자동 sync가 없으므로 `python3 scripts/register-traefik.py sync`로 한 번 sync한다(분산 설정). 끊김 감시를 켜 두고, 끝나면 `kubectl -n traefik get pods -o wide`로 두 pod가 다른 노드에 있는지 본다.
- `kubectl -n docker-doom get ingress`의 ADDRESS가 NLB 이름이다. Application `docker-doom-eks`, `moimroom-eks`가 Synced, Healthy다.
- 이름 확인: 2단계의 probe pod를 `args`만 바꿔 띄운다. 관리 클러스터의 CoreDNS를 쓰므로 게이트웨이 pod와 같은 경로로 이름을 푼다. zone이 생기기 전에 누가 물은 이름(`shop.eks.hopcharlie.internal` 같은 게이트웨이 기본 백엔드)은 없다는 답이 최대 900초(SOA TTL) 동안 캐시될 수 있으므로 쓰지 않고, 아무도 묻지 않았던 `docker-doom.eks.hopcharlie.internal`로 본다. 게이트웨이는 백엔드 상태가 바뀔 때만 로그를 남기므로 로그로는 확인하지 않는다.

```sh
nslookup docker-doom.eks.hopcharlie.internal                       # NLB 주소가 나와야 한다
curl -s -o /dev/null -w '%{http_code}\n' http://docker-doom.eks.hopcharlie.internal/vnc.html   # 200
curl -s -o /dev/null -w '%{http_code}\n' 'http://docker-doom.eks.hopcharlie.internal/a%2Fb'     # 400이 아니어야 한다
curl -s -o /dev/null -w '%{http_code}\n' --max-time 2 --http1.1 -H 'Connection: Upgrade' -H 'Upgrade: websocket' \
  -H 'Sec-WebSocket-Version: 13' -H 'Sec-WebSocket-Key: dGhlIHNhbXBsZSBub25jZQ==' -H 'Sec-WebSocket-Protocol: binary' \
  http://docker-doom.eks.hopcharlie.internal/websockify                                           # 101
```

`docker-doom.eks.hopcharlie.internal`이 풀리고 200을 받기 전에는 4단계(플랫폼 틀)로 넘어가지 않는다. 풀리지 않으면 workload Terraform 적용이 끝났는지, plan에 `check.eks_apps_entry_nlb` 경고(NLB가 하나가 아님)가 있는지 본다.

## 4. 플랫폼 틀과 앱 전환

platform PR #73이 들어간 이미지가 infra에 고정된 뒤, `deploy.backendTemplates`(`PLATFORM_APP_BACKEND_TEMPLATES`)를 켜는 PR이 들어간다(API pod가 다시 시작한다).

```bash
charlie deploy docker-doom --redeploy
charlie app show docker-doom      # eks URL이 http://docker-doom.eks.hopcharlie.internal
```

배포 실행기는 새 주소가 `/vnc.html`에 답한 뒤에만 게이트웨이를 바꾼다. 실패하면 게이트웨이는 그대로다. moimroom은 활성 gke 쓰기 백엔드를 지키는 플랫폼 수정이 들어간 뒤 한 번 재배포한다(직접 PUT하지 않는다). 전환 뒤 2단계의 롤링 재시작을 다시 해서 Traefik 경로의 끊김이 0인지 본다.

## 5. 앱 NLB 정리

게이트웨이 `GET /apps`(관리 포트 9901)에 `elb.amazonaws.com` 백엔드가 하나도 없을 때만 한다. eks 프로필의 공개 Service를 `ClusterIP`로, quota `services.loadbalancers`를 `0`으로 바꾸는 PR이 들어가면 컨트롤러가 앱 NLB를 지운다.

```bash
aws elbv2 describe-load-balancers --query 'LoadBalancers[?Type==`network`].LoadBalancerName'   # hopcharlie-apps-eks 하나
kubectl -n docker-doom get svc docker-doom-web   # TYPE ClusterIP, finalizer 없음
```

## NLB가 다시 만들어졌을 때

Service `traefik/traefik`을 지웠다 다시 만들거나 이름 annotation이 바뀌면 NLB의 DNS 이름이 바뀐다. 워크로드 Terraform root를 다시 적용해야 `*.eks.hopcharlie.internal`이 새 NLB를 가리킨다. main에 아무 커밋이나 들어가면 자동 적용되고, 바로 하려면 Actions의 `Charlie infrastructure`를 `apply`, stack `workload`로 실행한다. 그 전까지 게이트웨이의 eks 백엔드는 답하지 않는다.

## 되돌리기

- Traefik 등록: Service에 `Delete=false`가 있으므로 Application을 지워도 NLB가 남는다. 앱이 아직 Traefik을 쓰지 않을 때만 `kubectl -n argocd delete application charlie-workload-traefik` 뒤 `kubectl -n traefik delete svc traefik`로 NLB까지 지운다.
- 앱 전환 뒤: 5단계 전이면 앱 NLB가 아직 있으므로, `deploy.backendTemplates`를 비우는 PR 뒤 재배포하면 게이트웨이가 앱 NLB로 돌아간다.

## 실행 기록

| 시각(KST) | 단계 | 결과 |
| --- | --- | --- |
