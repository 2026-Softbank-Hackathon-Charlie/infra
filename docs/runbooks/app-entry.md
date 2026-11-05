# 워크로드 EKS 앱 진입점(Traefik) 실행 순서

[ADR 0058](../adr/0058-share-one-app-entry-per-workload-cluster.md)을 단계마다 승인받아 실행한다. 단계마다 끊김 감시를 켜 두고, 결과를 아래 실행 기록에 남긴다.

준비:

- 관리 클러스터 kubeconfig(`make aws-tunnel`, 기본 `.generated/access/management.kubeconfig`)와 워크로드 kubeconfig(`make aws-tunnel CLUSTER=workload`)
- `kubectl`, `helm` 3.19.1, Python과 PyYAML, origin/main을 체크아웃한 infra 저장소

## 0. 끊김 감시

공개 주소만 읽는다. 1초마다 docker-doom 화면, moimroom 헬스 체크, docker-doom WebSocket 업그레이드(101)를 확인하고 실패만 남긴다.

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

infra PR(ADR 0058의 Traefik 애드온)이 main에 들어간 뒤 실행한다. merge만으로는 클러스터에 아무것도 바뀌지 않는다.

```bash
python3 scripts/register-traefik.py preview   # AppProject diff, Application 서버 dry-run
python3 scripts/register-traefik.py create    # AppProject apply, Application create(자동 sync 없음)
python3 scripts/register-traefik.py sync      # sync 한 번
```

- preview의 AppProject diff는 source 저장소 `https://traefik.github.io/charts`와 대상 네임스페이스 `traefik` 추가뿐이어야 한다(목록 순서 차이는 괜찮다). 다른 차이가 있으면 멈춘다.
- 확인:

```bash
kubectl -n traefik get deploy,pods,svc,pdb -o wide
kubectl get ns traefik --show-labels            # elbv2.k8s.aws/pod-readiness-gate-inject=enabled
kubectl get ingressclass traefik
kubectl -n traefik get ingress traefik-no-route   # ADDRESS가 NLB 이름(Traefik이 status를 씀)
aws elbv2 describe-load-balancers --names hopcharlie-apps-eks --query 'LoadBalancers[0].[State.Code,Scheme,DNSName]'
aws elbv2 describe-target-health --target-group-arn <대상 그룹 ARN>   # pod 2개 healthy
```

## 2. 롤링 재시작 시험

앱을 옮기기 전에 한 번 한다. 첫 설치 pod에는 readiness gate가 없으므로, 재시작 뒤 새 pod에 `target-health.elbv2.k8s.aws/...` readiness gate가 붙었는지도 본다.

```bash
kubectl -n traefik rollout restart deploy/traefik
kubectl -n traefik rollout status deploy/traefik --timeout=5m
kubectl -n traefik get pods -o jsonpath='{range .items[*]}{.metadata.name}{" "}{.spec.readinessGates}{"\n"}{end}'
```

끊김 감시의 실패가 0이어야 한다. 이 단계에서는 아직 앱이 Traefik을 거치지 않으므로, Traefik 경로의 끊김은 4단계 뒤 재시작으로 다시 본다.

## 3. 내부 DNS와 앱 Ingress

다음 infra PR(워크로드 Terraform의 `hopcharlie.internal`, 앱 차트 Ingress, eks 프로필)이 들어가면 main의 Terraform 자동 적용과 Argo CD 자동 sync가 적용한다.

- 확인: `kubectl -n docker-doom get ingress`의 ADDRESS가 NLB 이름이다. Application `docker-doom-eks`, `moimroom-eks`가 Synced, Healthy다. 게이트웨이 로그의 기본 앱 백엔드 `shop.eks.hopcharlie.internal` 오류가 DNS 실패에서 404로 바뀐다(DNS, NLB, Traefik이 게이트웨이 pod에서 닿는다는 뜻).
- Traefik을 거치는 시험(관리 서브넷의 SSM 브리지로 NLB 80을 로컬에 연다):

```bash
curl -s -o /dev/null -w '%{http_code}\n' -H 'Host: docker-doom.eks.hopcharlie.internal' http://127.0.0.1:<로컬 포트>/vnc.html
curl -s -o /dev/null -w '%{http_code}\n' -H 'Host: docker-doom.eks.hopcharlie.internal' 'http://127.0.0.1:<로컬 포트>/a%2Fb'   # 400이 아니어야 한다
curl -s -o /dev/null -w '%{http_code}\n' --max-time 2 --http1.1 -H 'Host: docker-doom.eks.hopcharlie.internal' \
  -H 'Connection: Upgrade' -H 'Upgrade: websocket' -H 'Sec-WebSocket-Version: 13' \
  -H 'Sec-WebSocket-Key: dGhlIHNhbXBsZSBub25jZQ==' -H 'Sec-WebSocket-Protocol: binary' \
  http://127.0.0.1:<로컬 포트>/websockify                                                                  # 101
```

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
