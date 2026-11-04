# GitHub 앱 주소 열기 실행 순서

플랫폼이 GitHub에서 배포한 앱을 `https://<instance>.hopcharlie.dev`로 여는 데 필요한 운영 작업을 순서대로 적는다. 결정과 이유는 [ADR 0036](../adr/0036-app-routing-and-argo-apps.md)에 있다. 각 단계는 실제 클러스터, AWS, GitHub, Cloudflare를 바꾸므로 [AGENTS.md](../../AGENTS.md)에 따라 단계마다 실행 전에 승인을 받는다. 스크립트의 미리 보기와 확인 명령도 private 클러스터와 계정을 조회하므로 같은 승인 범위에 넣는다. 이 문서를 쓴 시점(2026-11-04)에는 아래 명령을 하나도 실행하지 않았다.

## 준비

- 명령은 저장소 루트(`C:\Works\hackerthon\infra`, `config/account.local.json`이 있는 checkout)의 Git Bash에서 실행한다. 이 PR이 merge된 뒤 `git pull`로 main을 받는다.
- 도구: AWS CLI v2, kubectl 1.28 이상(`auth whoami`), Helm 3.19.1, Python 3와 PyYAML, session-manager-plugin, gh(선택), Docker(`tf.sh`용). `kubectl diff`가 쓰는 `diff`가 있어야 하므로 PowerShell이 아니라 Git Bash에서 실행한다.
- AWS 프로필은 `hopcharlie`(계정 525091405886)를 쓴다. 이 프로필이 두 EKS의 Access Entry(클러스터 관리자)를 가져야 한다. 아니면 그 권한이 있는 프로필 이름을 `AWS_PROFILE`에 넣는다.
- 터미널 두 개에 private EKS API 터널을 켜 둔다. 두 터미널은 작업이 끝날 때까지 닫지 않는다.

```bash
# 터미널 A: 관리 EKS (127.0.0.1:10443)
make aws-tunnel AWS_PROFILE=hopcharlie PYTHON=python
# 터미널 B: 워크로드 EKS (127.0.0.1:11443)
make aws-tunnel CLUSTER=workload AWS_PROFILE=hopcharlie PYTHON=python
```

이후 명령에서 쓰는 줄임말이다.

```bash
export AWS_PROFILE=hopcharlie AWS_REGION=ap-northeast-2
MK="kubectl --kubeconfig .generated/access/management.kubeconfig --context charlie-dev-management"
WK="kubectl --kubeconfig .generated/access/workload.kubeconfig --context charlie-dev-workload"
```

| 단계 | 바뀌는 것 | 필요한 권한 |
| --- | --- | --- |
| 1. PR merge | 플랫폼 Argo 앱이 Ingress `hopcharlie-apps`를 만들고 ALB에 `*.hopcharlie.dev` 규칙이 생긴다 | infra main merge |
| 2. 워크로드 LBC 확인 | 없을 때만 Application 하나를 sync한다 | 관리 EKS 관리자 |
| 3. 플랫폼 kubeconfig | 워크로드에 namespace, ServiceAccount, 토큰 Secret, ClusterRole, ClusterRoleBinding, SSM `/hopcharlie/kube/eks`, API 재시작 | 워크로드 EKS 관리자, `eks:DescribeCluster`, `ssm:PutParameter`, 관리 EKS 관리자 |
| 4. gitops 저장소 자격 증명 | GitHub 토큰 발급, `argocd/charlie-gitops-repository` Secret | gitops 저장소에 대한 fine-grained PAT 발급(필요하면 조직 승인), 관리 EKS 관리자 |
| 5. 앱 ApplicationSet | AppProject와 Application 하나씩 만들고 sync, 그 결과 앱이 워크로드에 배포된다 | 관리 EKS 관리자 |
| 6. DNS | Cloudflare `*.hopcharlie.dev` CNAME | Cloudflare `Zone.DNS:Edit` 토큰, AWS 프로필 |
| 7. 확인 | 없음(조회만) | 위와 같음 |

## 1. PR merge와 ALB 규칙 확인

플랫폼 Application `charlie-management-hopcharlie-platform`은 자동 sync가 켜져 있다. 그래서 merge가 곧 배포다. merge 전에 지금 응답을 적어 둔다.

```bash
for h in api app console; do echo "$h $(curl -s -o /dev/null -w '%{http_code}' https://$h.hopcharlie.dev/)"; done
```

merge 뒤 3분 안에 Ingress가 생기고 ALB 주소가 붙는다.

```bash
$MK -n charlie-platform get ingress hopcharlie-platform hopcharlie-apps
alb=$(aws elbv2 describe-load-balancers --names charlie-management-external --query 'LoadBalancers[0].LoadBalancerArn' --output text)
listener=$(aws elbv2 describe-listeners --load-balancer-arn "$alb" --query 'Listeners[?Port==`443`].ListenerArn' --output text)
aws elbv2 describe-rules --listener-arn "$listener" \
  --query 'Rules[].{priority: Priority, hosts: Conditions[?Field==`host-header`].HostHeaderConfig.Values[]}' --output json
```

`api`, `app`, `console`(있으면 `headlamp`)의 우선순위 숫자가 `*.hopcharlie.dev`보다 작아야 한다. Ingress 이벤트에 `FailedBuildModel`이나 `FailedDeployModel`이 있으면 LBC가 이 ALB 그룹의 변경을 반영하지 못한 상태이므로 아래처럼 되돌린다.

```bash
$MK -n charlie-platform describe ingress hopcharlie-apps | sed -n '/^Events:/,$p'
```

그다음 응답을 다시 확인한다.

```bash
for h in api app console; do echo "$h $(curl -s -o /dev/null -w '%{http_code}' https://$h.hopcharlie.dev/)"; done   # merge 전과 같아야 한다
curl -s https://api.hopcharlie.dev/ -H 'Host: routing-check.hopcharlie.dev'   # 게이트웨이의 404 unknown_app (전에는 ALB의 "not found")
```

되돌리기: 플랫폼 앱은 `selfHeal: true`, `prune: false`다. 커밋이 main에 있는 동안 Ingress를 지우면 다시 만들어지고, 커밋을 되돌려도 Ingress는 저절로 지워지지 않는다. 규칙 순서가 틀렸으면 승인을 받고 둘 중 하나로 되돌린다.

- 기본: 되돌리는 커밋을 main에 넣는다. 플랫폼 앱의 sync revision이 그 커밋이 되고, OutOfSync로 남은 리소스가 `Ingress/hopcharlie-apps`(requiresPruning true) 하나뿐이면 그것을 지운다. prune이 꺼져 있어 그 전까지 앱은 OutOfSync로 보인다.

```bash
$MK -n argocd get application charlie-management-hopcharlie-platform -o jsonpath='{.status.sync.revision}{"\n"}{range .status.resources[?(@.status=="OutOfSync")]}{.kind}/{.name} {.requiresPruning}{"\n"}{end}'
$MK -n charlie-platform delete ingress hopcharlie-apps
```

- 빠른 길(api나 console이 가려졌을 때): selfHeal을 끄고 Ingress를 지운다. 되돌리는 커밋이 sync된 뒤 selfHeal을 다시 켠다. 그 전에 켜면 Ingress가 다시 생긴다. selfHeal이 꺼져 있어도 새 커밋의 자동 sync(이미지 고정 커밋 포함)는 그대로 돈다.

```bash
$MK -n argocd patch application charlie-management-hopcharlie-platform --type merge \
  -p '{"spec":{"syncPolicy":{"automated":{"prune":false,"selfHeal":false}}}}'
$MK -n charlie-platform delete ingress hopcharlie-apps
# 되돌리는 커밋이 sync된 뒤
$MK -n argocd patch application charlie-management-hopcharlie-platform --type merge \
  -p '{"spec":{"syncPolicy":{"automated":{"prune":false,"selfHeal":true}}}}'
```

## 2. 워크로드 Load Balancer Controller 확인

앱의 공개 Service는 워크로드 LBC가 만드는 내부 NLB를 쓴다. 2026-11-03 기록에는 이 앱이 `Missing`이었다. 먼저 확인한다.

```bash
$MK -n argocd get application charlie-workload-aws-load-balancer-controller \
  -o jsonpath='{.status.sync.status} {.status.health.status}{"\n"}'
$WK -n kube-system get deploy aws-load-balancer-controller
aws eks list-pod-identity-associations --cluster-name charlie-dev-workload --namespace kube-system \
  --query 'associations[].serviceAccount' --output text   # aws-load-balancer-controller가 있어야 한다
```

Deployment가 없을 때만, 별도 승인을 받고 이 Application 하나만 sync한다. root와 다른 앱은 건드리지 않는다.

```bash
$MK -n argocd patch application charlie-workload-aws-load-balancer-controller --type merge \
  -p '{"operation":{"initiatedBy":{"username":"<이름>"},"sync":{"syncOptions":["ServerSideApply=true"]}}}'
$WK -n kube-system rollout status deploy/aws-load-balancer-controller --timeout=5m
```

## 3. 플랫폼이 워크로드를 읽는 kubeconfig

[scripts/put-platform-kubeconfig.py](../../scripts/put-platform-kubeconfig.py)가 [clusters/aws-dev-workload/hopcharlie-platform-access.yaml](../../clusters/aws-dev-workload/hopcharlie-platform-access.yaml)을 적용하고, 토큰으로 kubeconfig(컨텍스트 `eks-hopcharlie`, private EKS endpoint)를 만들어 SSM에 넣는다. 다른 계정의 프로필이면 아무것도 하지 않고 멈춘다. 토큰은 출력하지 않는다.

```bash
python scripts/put-platform-kubeconfig.py            # 바꾸지 않고 계정, 터널, SSM, RBAC diff만 본다
python scripts/put-platform-kubeconfig.py --apply    # 적용, 권한 확인(can-i), SSM 저장
```

미리 보기는 `/hopcharlie/kube` 아래의 모든 파라미터 이름과 마지막 수정자를 보여 준다(값은 읽지 않는다). API는 이 파일들을 모두 합쳐 쓰고 컨텍스트 이름이 겹치면 먼저 읽은 파일이 이긴다. 워크로드에 `charlie-system`이 아직 없으면 미리 보기는 클러스터 범위 객체(ClusterRole, ClusterRoleBinding)만 diff한다. `--apply`는 namespace를 먼저 만들고, 그 안의 ServiceAccount와 토큰 Secret을 diff한 뒤 전체를 적용한다. `--apply`는 저장 전에 터널로 그 토큰의 권한을 확인한다. 읽기 7개는 yes, Secret과 ConfigMap 읽기와 변경 7개는 no여야 저장한다. `/hopcharlie/kube/eks`가 이미 있으면 멈춘다. 그 값은 EC2 호스트 시절에 다른 클러스터를 가리키도록 넣었을 수 있으므로, 마지막 수정자에게 확인한 뒤에만 `--replace`를 붙인다.

API pod는 시작할 때만 SSM을 읽으므로 다시 시작한다. API는 Recreate라서 1분쯤 응답하지 않는다.

```bash
$MK -n charlie-platform rollout restart deployment/hopcharlie-api
$MK -n charlie-platform rollout status deployment/hopcharlie-api --timeout=5m
charlie env list   # eks가 reachable이고 노드 2대, Kubernetes 1.35로 보여야 한다
```

토큰을 끊을 때는 `$WK -n charlie-system delete secret hopcharlie-platform-token`을 실행한다. 새 토큰은 `--apply --replace`로 Secret을 다시 만들고 저장한 뒤 API를 다시 시작한다. Secret을 지우지 않고 `--replace`만 하면 같은 토큰이 다시 저장된다. 이 클러스터에는 platform 저장소의 `make-kubeconfig.sh`를 쓰지 않는다.

## 4. gitops 저장소 읽기 자격 증명

infra 저장소와 같은 방식(HTTPS, `x-access-token`, 읽기 전용 PAT)을 쓴다.

1. GitHub의 Settings, Developer settings, Fine-grained tokens에서 토큰을 만든다. Resource owner는 `2026-Softbank-Hackathon-Charlie`, Repository access는 Only select repositories의 `gitops` 하나, Repository permissions는 Contents Read-only(Metadata Read-only는 자동)만 준다. 만료는 2026-11-30으로 둔다. 조직이 승인을 요구하면 조직 owner가 승인한다.
2. 토큰을 다른 사람이 읽을 수 없는 파일에 넣고 등록한 뒤 파일을 지운다.

```bash
umask 077; read -rs t && printf '%s' "$t" > ~/argocd-gitops-token; unset t   # 토큰을 붙여 넣고 Enter, 화면에 보이지 않는다
python scripts/register-hopcharlie-apps.py add-repository --token-file ~/argocd-gitops-token
rm ~/argocd-gitops-token
```

스크립트는 같은 이름의 Secret이 있으면 덮어쓰지 않고 멈춘다. 토큰은 kubectl의 stdin으로만 넘긴다. 저장소를 실제로 읽는지는 5단계의 ApplicationSet 상태로 확인한다.

## 5. 앱 ApplicationSet만 만들고 sync

[scripts/register-hopcharlie-apps.py](../../scripts/register-hopcharlie-apps.py)는 `charlie-root`의 지금 values로 `helm/gitops`를 렌더링해 AppProject `charlie-hopcharlie-apps`와 Application `charlie-management-hopcharlie-apps`만 꺼낸다. Application은 자동 sync 없이 만든다. root는 읽기만 하고 sync하지 않는다.

```bash
python scripts/register-hopcharlie-apps.py preview
gh api repos/2026-Softbank-Hackathon-Charlie/gitops/contents/apps --jq '.[].name'
```

`preview`는 서버 dry-run과, sync가 만들 객체(AppProject `hopcharlie-apps`, ApplicationSet `hopcharlie-apps`, Role과 RoleBinding `hopcharlie-platform-deployer`, ValidatingAdmissionPolicy와 Binding)의 `kubectl diff`를 보여 준다. `gh api`로 본 `apps/<instance>/eks.yaml`마다 sync 직후 Application이 생기고, 자동 sync와 prune으로 워크로드에 바로 배포된다. 지우려는 앱이 남아 있으면 플랫폼에서 먼저 정리한다.

```bash
python scripts/register-hopcharlie-apps.py create   # origin/main을 fetch해 같은 commit일 때만 실행된다. 객체 2개 생성, sync 없음
make argo-port-forward PYTHON=python                 # 다른 터미널, https://localhost:8080 에서 diff 확인
python scripts/register-hopcharlie-apps.py sync     # sync 한 번, ApplicationSet과 앱 Application 상태 출력
```

`sync` 출력에서 ApplicationSet 조건에 `ErrorOccurred=True`나 저장소 인증 오류가 없어야 한다. 앱 Application은 몇 분 안에 `Synced`, `Healthy`가 된다. 공개 서비스의 NLB 주소를 확인한다.

```bash
$MK -n argocd get applications -l hopcharlie.dev/app
$WK get svc -A -l hopcharlie.dev/app -o wide | grep LoadBalancer
```

첫 sync가 성공하고 결과를 확인한 뒤, 차트 변경이 사람 없이 반영되도록 root 템플릿과 같은 자동 sync를 켠다. 켜지 않으면 차트가 바뀔 때마다 `sync`를 다시 실행한다. 특히 AppProject가 SealedSecret을 허용하게 바꾸는 저장소 PR(feat/app-storage-infra)이 이 단계 뒤에 merge되면, 자동 sync가 꺼져 있는 동안에는 그 변경이 반영되지 않아 앱의 SealedSecret이 프로젝트에서 거부된다. 그때는 `sync`를 다시 실행하거나 자동 sync를 켠다.

```bash
$MK -n argocd patch application charlie-management-hopcharlie-apps --type merge \
  -p '{"spec":{"syncPolicy":{"automated":{"prune":false,"selfHeal":true}}}}'
```

### 멈추기와 되돌리기

ApplicationSet이 만든 Application에는 ApplicationSet을 가리키는 ownerReference와 `resources-finalizer.argocd.argoproj.io`가 있다. 그래서 ApplicationSet이 지워지면 Application이 모두 지워지고, finalizer 때문에 앱마다 네임스페이스와 CNPG DB까지 지워진다. 멈출 때는 아래 방법만 쓴다.

- 차트 변경 반영을 멈춘다: `charlie-management-hopcharlie-apps`의 자동 sync를 끈다.

```bash
$MK -n argocd patch application charlie-management-hopcharlie-apps --type merge -p '{"spec":{"syncPolicy":{"automated":null}}}'
```

- 새 배포와 앱 삭제를 멈춘다: 원천은 플랫폼의 gitops 커밋이므로 플랫폼에서 배포와 삭제를 멈춘다. 앱 Application의 자동 sync는 ApplicationSet이 템플릿대로 되돌리므로 Application에서 끄지 않는다.
- ApplicationSet을 없애야 하면 Application과 그 리소스를 남기는 orphan 삭제만 쓴다. 남은 Application에는 finalizer가 그대로 있으므로, 나중에 Application을 지우면 그 앱의 리소스도 지워진다.

```bash
$MK -n argocd delete applicationset hopcharlie-apps --cascade=orphan
```

- `charlie-management-hopcharlie-apps`와 root는 Argo CD 화면이나 CLI에서 cascade로 지우지 않는다. 차트는 ApplicationSet에 `argocd.argoproj.io/sync-options: Delete=false`를 붙여 상위 Application이 cascade로 지워져도 ApplicationSet을 남기지만, 이 annotation은 sync된 뒤에만 효과가 있다. 지우기 전에 annotation을 확인한다. 이 Application 자체에는 finalizer가 없으므로 `kubectl delete application`은 리소스를 지우지 않는다.

```bash
$MK -n argocd get applicationset hopcharlie-apps -o jsonpath='{.metadata.annotations.argocd\.argoproj\.io/sync-options}{"\n"}'   # Delete=false
```

## 6. 와일드카드 DNS

```bash
cd terraform/platform/aws
read -rs CLOUDFLARE_API_TOKEN && export CLOUDFLARE_API_TOKEN   # Zone.DNS:Edit, hopcharlie.dev. 기록에 남지 않게 입력한다
./tf.sh -chdir=dns init
./tf.sh -chdir=dns plan           # cloudflare_dns_record.apps[0] 하나만 추가되어야 한다
./tf.sh -chdir=dns apply
unset CLOUDFLARE_API_TOKEN; cd -
```

plan이 그 추가 하나 외의 변경을 보이면 apply하지 않는다. Cloudflare 화면에서 누가 손으로 만든 `*` 레코드가 이미 있으면 apply가 같은 이름의 레코드 때문에 실패한다. 그때는 그 레코드를 지울지 import할지 팀과 정한 뒤 다시 실행한다.

```bash
nslookup routing-check.hopcharlie.dev 1.1.1.1         # ALB 이름의 CNAME
nslookup api.hopcharlie.dev 1.1.1.1                   # 그대로 기존 레코드
curl -s https://routing-check.hopcharlie.dev/         # 게이트웨이의 404 unknown_app
```

## 7. 앱 주소 확인

플랫폼에서 배포가 `healthy`가 되면 게이트웨이에 앱이 등록된다. 게이트웨이 등록은 플랫폼의 배포 안에서만 일어난다. 이 순서를 마치기 전에 실패한 배포(예: kubeconfig가 없어 Service를 찾지 못한 배포)의 앱은 게이트웨이에 없으므로, 플랫폼에서 다시 배포한다(콘솔의 배포 또는 `POST /v1/apps/<app>/deployments`).

```bash
curl -s -o /dev/null -w '%{http_code}\n' https://<instance>.hopcharlie.dev/
```

502나 시간 초과가 나면 관리 클러스터의 게이트웨이에서 NLB로 직접 요청해 본다(`kubectl exec`이므로 승인 후).

```bash
nlb=$($WK -n <instance> get svc <app>-<service> -o jsonpath='{.status.loadBalancer.ingress[0].hostname}')
$MK -n charlie-platform exec deploy/hopcharlie-gateway -- wget -qO- -T 5 "http://$nlb/<healthCheck 경로>"
```

여기서 실패하면 NLB 보안 그룹의 80 인바운드와 워크로드 노드 보안 그룹의 대상 포트 규칙을 본다.

```bash
aws elbv2 describe-load-balancers --query "LoadBalancers[?DNSName=='$nlb'].[LoadBalancerArn,SecurityGroups]" --output json
```

## 실행 기록

| 날짜 | 단계 | 실행한 사람 | 결과 |
| --- | --- | --- | --- |
| | | | |
