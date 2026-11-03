# 팀 공용 Headlamp 배포

목표 URL: `https://headlamp.hopcharlie.dev`. 현재 문서는 준비된 절차이며 실제 배포 완료를 뜻하지 않는다.
관리 EKS에서 화면을 제공하고 관리·워크로드 EKS의 OIDC 토큰과 읽기 전용 RBAC를 사용한다.
팀원마다 Cognito 계정으로 로그인한다. AWS 콘솔의 IAM 계정이나 개인 CLI 세션을 공유하지 않는다.

## 적용 전에 확인할 것

사용자 승인 후 배포자의 AWS 계정, EKS OIDC provider 유무, ALB·ACM 상태, 현재 GitOps 구성과 노드 용량을 조회한다.
각 EKS는 외부 OIDC provider를 하나만 연결할 수 있으므로 기존 provider가 있으면 중단하고 통합 방식을 다시 검토한다.
배포자에게 Cognito 생성, EKS OIDC 연결, SG 규칙 생성, 기존 S3 state 새 key 읽기/쓰기 및 Kubernetes 배포/RBAC 권한이 필요하다.
Cloudflare에는 지정 zone의 DNS 수정 토큰이 필요하다. 토큰을 채팅·코드·명령 인자에 넣지 않고 로컬 환경 변수로 전달한다.
읽기 전용 `AmazonEKSAdminViewPolicy`만 부여된 사용자는 이 배포를 수행할 수 없다. 광범위한 권한을 자동으로 추가하지 않는다.

## 로컬 검증

```powershell
$helm = "$PWD\.generated\tools\helm\windows-amd64\helm.exe"
$terraform = "$PWD\.generated\tools\terraform\terraform.exe"
$env:HELM = $helm
python scripts/tests/test-headlamp.py
& $terraform -chdir=terraform/platform/aws/headlamp validate
& $terraform -chdir=terraform/platform/aws/headlamp test
```

## 승인된 인증·DNS 인프라 적용

AWS 프로필 `charlie-admin`과 환경 변수 `CLOUDFLARE_API_TOKEN`이 준비된 상태에서 실행한다.
별도 root/state를 사용하므로 기존 플랫폼 EC2나 기존 DNS 리소스를 소유하지 않는다.

```powershell
$env:AWS_PROFILE = "charlie-admin"
& $terraform -chdir=terraform/platform/aws/headlamp init -reconfigure
& $terraform -chdir=terraform/platform/aws/headlamp plan
# 생성될 리소스와 SG 규칙을 검토하고 승인된 범위일 때만 실행
& $terraform -chdir=terraform/platform/aws/headlamp apply
```

Terraform의 client secret은 민감 output이다. 전체 output/plan/state를 채팅이나 문서에 붙여 넣지 않는다.
공개 values와 Kubernetes Secret 등록은 다음으로 수행한다. Secret은 디스크에 저장하지 않는다.
`headlamp-oidc`(client secret)와 `headlamp-clusters`(클러스터 kubeconfig)를 함께 만든다.
Headlamp는 토큰 교환에 kubeconfig 사용자의 OIDC 설정을 쓰므로 kubeconfig에도 client secret이 필요하다.
그래서 kubeconfig는 ConfigMap이 아닌 Secret으로 두고 차트는 마운트만 한다. 시크릿이나 클러스터가 바뀌면 Headlamp를 재시작한다.

```powershell
python scripts/prepare-headlamp.py --terraform $terraform `
  --kubeconfig .generated/access/management.kubeconfig --install-secret
```

## 승인된 최초 배포

각각 다른 터미널에서 기존 `eks-access.py tunnel`을 management 및 workload로 유지한다.
최초 배포는 변경한 로컬 차트를 Helm으로 적용한다. 노드나 EKS를 새로 만들지 않는다.

```powershell
& $helm upgrade --install headlamp helm/charts/headlamp-team `
  --namespace headlamp --create-namespace `
  --kubeconfig .generated/access/management.kubeconfig `
  -f clusters/aws-dev-management/values/headlamp-team.yaml `
  -f .generated/headlamp/public-values.json --wait --timeout 5m

& $helm upgrade --install headlamp helm/charts/headlamp-team `
  --namespace headlamp --create-namespace `
  --kubeconfig .generated/access/workload.kubeconfig `
  -f clusters/aws-dev-workload/values/headlamp-team.yaml --wait --timeout 5m
```

실제 공개 values를 management values 파일에 반영하고 배포 기록을 검토한다. Secret은 반영하지 않는다.
GitOps로 인계할 때 로컬 Helm과 Argo가 동시에 소유하지 않게 release 인계 절차를 검토한 뒤
root values의 `headlamp.enabled=true`를 활성화한다. 기본 설정은 비활성이라 merge만으로 배포되지 않는다.
사용자 승인 없이 commit/push/CI 배포를 실행하지 않는다.

## 팀원 계정 및 검증

Cognito 콘솔의 `charlie-headlamp-team`에서 팀원 계정을 만들고 `team-viewers` 그룹에 등록한다.
초대 이메일 전송은 사용자에게 명시적으로 요청받은 경우에만 수행한다. 비밀번호는 안전한 경로로 전달하고 첫 로그인에서 변경한다.
자체 회원가입은 비활성이다. IAM 계정과 Cognito 계정은 서로 다르다.

1. HTTPS 인증서와 DNS가 올바르고 로그인 전 Kubernetes 데이터가 반환되지 않는지 확인한다.
2. 팀원으로 로그인해 management/workload Map, 노드·Pod·지표·로그를 실제로 확인한다.
3. Secret/ConfigMap 조회, Pod 삭제, Deployment 변경, exec가 거부되는지 확인한다.
4. 그룹에 없는 계정은 데이터 조회가 거부되는지 확인한다.
5. 로그아웃·세션 만료·재로그인을 확인한다. 그룹 제거 후 이미 발급한 ID token은 최대 1시간 남을 수 있다.
6. 실제 확인 결과와 수행한 변경을 기록하고 ADR 상태를 갱신한다.

## 비용과 정리

기존 ALB·노드 재사용으로 새 ALB·EC2 고정 요금은 계획하지 않는다. CPU 100m/메모리 128Mi를 요청하며 ALB 사용량은 증가할 수 있다.
Cognito Lite의 계정/조직 무료 MAU 한도 내 팀 규모 사용은 추가 사용자 요금이 없을 수 있다.
실제 계정 사용량과 기존 노드 여유를 확인해야 하며 새 노드 증설은 별도 검토한다.

삭제 시 먼저 GitOps Headlamp 앱을 비활성화한 후 두 클러스터의 Headlamp Helm release를 uninstall한다.
그 다음 namespace의 `headlamp-oidc`, `headlamp-clusters` Secret을 삭제한다. 공용 ALB·다른 namespace는 삭제하지 않는다.
Cognito pool의 삭제 방지 `ACTIVE`를 `INACTIVE`로 변경하고 검토·apply한 뒤
`terraform -chdir=terraform/platform/aws/headlamp destroy`를 실행한다.
이 root의 Headlamp DNS·OIDC 연결·SG 규칙·Cognito만 제거된다. 기존 IAM Access Entry는 유지된다.
