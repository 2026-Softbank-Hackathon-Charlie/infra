# 팀 Headlamp에 기존 GKE 연결

**AWS는 기존 Cognito 조회 전용 로그인, GKE는 개인 Google access token 로그인이다. GKE 조회 전용을 보장하는 구성은 아니다.** Google 계정의 기존 IAM/RBAC 권한을 따르므로 Owner/관리자는 변경도 할 수 있다. 이 문서는 적용 준비이며 실제 연동·로그인 성공 기록이 아니다.

GKE를 새로 만들거나 Headlamp Pod를 GKE에 추가하지 않는다. 관리 EKS의 기존 Headlamp가 GKE API에 연결하도록 클러스터 목록만 추가한다. Cognito 계정·EKS 로그인·기존 RBAC는 유지한다. 관련 결정: [ADR0020](adr/0020-add-gke-to-team-headlamp.md).

## 준비와 오프라인 미리보기

1. 승인된 읽기 작업으로 공개 GKE 메타데이터를 받는다. 파일에는 endpoint와 공개 CA만 있고 로그인 토큰은 없다.

```powershell
gcloud container clusters describe charlie-dev-gcp-workload `
  --project=project-b47cdba3-db5b-42a2-9a6 --zone=asia-northeast3-a `
  '--format=json(name,endpoint,masterAuth.clusterCaCertificate,status)' | Set-Content -Encoding utf8 .generated/headlamp/gke-public.json
```

출력 디렉터리는 먼저 생성한다. 기존 default kubeconfig를 덮어쓰거나 다른 프로젝트의 context를 변경하지 않는다.

2. 로컬 미리보기는 Secret 조회·Kubernetes 접속을 하지 않는다.

```powershell
python scripts/register-headlamp-gke.py --metadata .generated/headlamp/gke-public.json
```

메타데이터의 cluster name을 GKE context 이름으로, `gke-personal:<name>`을 자격 증명 없는 user로 쓴다. 해당 이름에 다른 설정이 이미 있으면 덮어쓰지 않고 중단한다. 같은 설정으로 재실행하면 Secret 변경·Pod 재시작을 하지 않는다.

## 실제 변경 전 승인 범위

승인할 대상은 관리 EKS의 `headlamp/headlamp-clusters` Secret 조회·갱신과 `headlamp/headlamp` Deployment 재시작이다. Secret의 `data.config`에 GKE cluster/context/빈 user를 추가하며, 기존 AWS 항목·OIDC client secret·current-context·다른 Secret data/metadata는 보존한다. `resourceVersion`을 포함한 `kubectl replace -f -`로 동시 갱신을 거부하고 Secret을 stdin으로만 전달한다. 값은 명령 인자·파일·로그에 출력하지 않는다.

배포자에게 해당 Secret `get/update`와 Deployment `patch` 권한이 필요하다. IAM 변경·새 RBAC·공유 Google 서비스 계정 키·방화벽 확대·GKE 인증 방식 변경은 이 작업에 포함하지 않는다. 인증·변경의 구체적 승인을 받은 뒤에만 다음을 실행한다.

```powershell
python scripts/register-headlamp-gke.py `
  --metadata .generated/headlamp/gke-public.json `
  --kubeconfig .generated/access/management.kubeconfig `
  --context charlie-dev-management --install-secret
```

이 PC에서 관리용 kubeconfig와 Session Manager plugin이 아직 준비되지 않았다면 먼저 멈춘다. 도구를 자동 설치하지 않는다. 별도 승인을 받아 [기존 EKS 접근 결정](adr/0014-local-eks-access-commands.md)의 `scripts/eks-access.py tunnel`을 사용하고 별도 터미널에서 SSM 터널을 유지한다. 실제 스크립트 인자는 `--help`로 확인한다. 환경 변수로 다른 kubeconfig를 가리킨다는 이유만으로 기본 파일을 사용하지 않는다.

AWS IAM 관리자 권한과 Kubernetes API 접근권한은 별개다. 현재 관리 EKS는 API 인증 모드이며 `AccountFullAccessRole`에는 AccessEntry가 없는 것으로 확인됐다. 권한이 없다면 먼저 멈추고, 사용자 승인 후에만 해당 principal의 임시 AccessEntry와 `AmazonEKSEditPolicy`를 **headlamp namespace에 한정해** 연결하는 방안을 검토한다. 기존 entry를 다시 조회해 중복 생성·기존 권한 삭제를 피하며, 작업 후 이번에 생성한 임시 entry만 회수한다. 이 등록 스크립트는 AccessEntry를 만들거나 IAM을 변경하지 않는다.

Secret 변경 후 재시작에 실패하면 Secret은 이미 갱신된 상태다. 원인을 확인하고 같은 명시적 kubeconfig/context로 `kubectl --namespace headlamp rollout restart deployment/headlamp`를 실행한다. 그다음 `rollout status deployment/headlamp --timeout=5m`을 확인한다. 등록 스크립트의 성공 메시지는 rollout·로그인 성공을 뜻하지 않는다.

## 연결과 개인 로그인 검증

- 기존 Headlamp Pod에서 GKE API까지 TCP443·TLS가 정상인지 승인된 방법으로 확인한다. 현재 GKE authorized network는 AWS NAT 공인 IP `15.165.220.134/32`, `54.116.82.33/32`다. 실제 Pod의 egress가 이 경로인지 확인하며, 실패해도 `0.0.0.0/0`으로 넓히지 않는다.
- 브라우저에서 `https://headlamp.hopcharlie.dev`를 열고 `charlie-dev-gcp-workload`를 선택한다. AWS 두 context는 기존 Cognito 로그인 버튼을 유지해야 한다.
- GKE에는 해당 프로젝트 접근권한을 가진 **본인 Google 계정의 짧은 수명 access token**을 사용한다. 개인 인증 후 토큰을 발급·입력하는 행위도 승인된 범위에서만 한다. 토큰을 채팅·Notion·Git·공용 kubeconfig·Secret에 저장하거나 다른 사람과 공유하지 않는다.
- Google access token은 GKE에만 권한이 제한된 토큰이 아니다. 해당 계정의 다른 Google Cloud 권한도 가질 수 있으므로 프로젝트 전용 계정과 신뢰할 수 있는 Headlamp만 사용한다. 서버는 로그인 토큰을 처리한다.
- Pod/노드 조회와 실제 계정의 허용·거부 범위를 확인한다. 조회 전용이 필요하면 별도 제한 계정/IAM·RBAC 설계를 먼저 승인받는다. 조회 전용 ClusterRole을 추가해도 기존 Owner 권한은 줄어들지 않는다.
- 세션 만료·재로그인과 기존 AWS 로그인 회귀를 확인한다. GKE 로그인 실패를 Cognito client secret 교체로 해결하려고 하지 않는다.

## 재등록·복구

`prepare-headlamp.py --install-secret`는 Terraform 출력의 AWS 클러스터 목록으로 Secret을 다시 만든다. 이를 실행하면 추가한 GKE 항목이 빠질 수 있으므로 이후 승인된 GKE 등록을 다시 수행한다. 장기적으로 공개 메타데이터와 등록 순서를 동일한 관리 절차로 유지한다. 롤백은 GKE에 해당하는 cluster/context/빈 user만 제거하고 Headlamp를 재시작하는 별도 승인 작업이다. Secret 전체를 로그나 파일로 백업하지 않는다.

Headlamp v0.46.0은 kubeconfig OIDC 설정의 `scope` 키를 읽는다. 준비 스크립트는 이 키로 `profile,email`을 생성하도록 수정했다. GKE 등록 도구는 기존 AWS OIDC 설정을 보존하므로 이미 설치된 `extra-scopes` 키를 자동 변환하지 않는다. 실제 scope 수정이 필요하면 그 변경을 포함한 별도 승인·검증을 먼저 진행한다.

## 로컬 검증

```powershell
python scripts/tests/test-headlamp-gke.py
python scripts/tests/test-headlamp.py
python scripts/check-inventory.py
```

테스트는 AWS 자격 증명 보존, GKE 무자격 user, 이름 충돌 거부, 재실행 불변, 잘못된 메타데이터, 명시적 적용 옵션, stdin 갱신·실패 시 비밀값 비출력을 검사한다. 실제 네트워크·로그인·권한·세션 검증은 별도다.
