# 0035. 앱 버킷과 앱 시크릿에 필요한 인프라를 한 번에 준비한다

- 날짜: 2026-11-04
- 상태: 제안
- 관련 ADR: [ADR 0016](0016-run-hopcharlie-platform-on-management-cluster.md), [ADR 0023](0023-deploy-github-apps-with-applicationset.md), [ADR 0033](0033-platform-admin-accounts-from-ssm.md), platform 저장소 ADR 0016(앱 오브젝트 스토리지), ADR 0019(앱 시크릿과 재배포)

## 배경과 제약

- platform ADR 0016: 플랫폼이 s3 리소스가 있는 앱 인스턴스마다 버킷 `<bucketPrefix><instance>`와 IAM 사용자 `hopcharlie-app-<instance>`(경로 `/hopcharlie-apps/`, permissions boundary 적용)를 만들고, 그 버킷 전용 액세스 키를 대상 클러스터의 Sealed Secrets 공개 인증서로 봉인해 gitops 파일에 넣는다. 앱 차트는 이를 SealedSecret `app-s3`로 그린다.
- platform ADR 0019: 앱 시크릿은 플랫폼 DB에 AES-256-GCM으로 보관하고, 키는 SSM `/hopcharlie/app-secrets-key`(32바이트, base64, Terraform이 만든다)를 API가 `PLATFORM_SECRETS_KEY`로 받는다. 배포할 때 인스턴스마다 봉인해 SealedSecret `app-secrets`로 넣고 `envFrom`으로 주입한다. gitops 파일의 `values.sealedSecrets`와 envs.yaml의 `sealedSecretsCert`, `storage` 형식을 정한다.
- 두 ADR은 infra가 한 번만 바꾸면 되는 항목을 정했다. 플랫폼 pod 역할 권한, permissions boundary, 시크릿 키, 클러스터 인증서를 플랫폼이 읽을 위치, 앱 차트의 SealedSecret 템플릿과 스키마, AppProject의 SealedSecret 허용이다.
- 확인한 사실(저장소 기준):
  - 워크로드 클러스터의 sealed-secrets는 root Application `charlie-workload-sealed-secrets`가 chart 2.20.0으로 `kube-system`에 설치한다. `fullnameOverride`가 `sealed-secrets-controller`(kubeseal 기본 이름), 컨트롤러 이미지는 0.40.0, `keyrenewperiod`는 `"0"`이라 키가 자동으로 바뀌지 않는다([values](../../clusters/aws-dev-workload/values/sealed-secrets.yaml)).
  - 앱 Application이 쓰는 AppProject는 `helm/charts/hopcharlie-apps`가 그리는 `hopcharlie-apps`다. root의 `charlie-hopcharlie-apps`는 ApplicationSet 차트 자체를 관리 클러스터 argocd 네임스페이스에 까는 프로젝트라서 앱 객체와 관계없다. 앱 네임스페이스의 객체 종류를 막는 다른 admission 정책은 없다(`platform-rbac.yaml`의 정책은 플랫폼의 Application 수정만 막는다).
  - 플랫폼 pod 역할 `hopcharlie-platform-pod`는 지금 SSM 읽기, 보안 뷰 읽기, CodeBuild 시작만 할 수 있다.
  - API init 컨테이너의 `fetch-settings.sh`는 `get-parameter`가 실패하면 멈춘다. 파라미터가 없으면 API가 뜨지 않는다.
  - 플랫폼 API는 envs.yaml을 `yaml.UnmarshalStrict`로 읽는다(platform `internal/envs/config.go`). 모르는 필드가 있으면 시작하지 않는다.
- 미확인 사항: 워크로드 클러스터에서 컨트롤러가 실제로 떠 있는지, 서비스 proxy로 `/v1/cert.pem`이 열리는지, 실제 IAM 동작(정책 평가)은 이번 작업에서 조회하지 않았다. 플랫폼이 실제로 부르는 S3와 IAM API 목록은 platform 구현이 끝난 뒤 맞춘다.

## 검토한 대안

- permissions boundary를 `hopcharlie-eks-*` 버킷과 객체의 `s3:*`로 두기: 범위가 넓다. 플랫폼 역할은 버킷의 공개 접근 차단을 풀 수 있으므로, boundary가 버킷 정책까지 허용하면 플랫폼이 잘못된 인라인 정책을 쓴 사용자를 거쳐 버킷을 공개할 수 있다. 객체 사용과 목록만 허용하는 쪽을 골랐다.
- 사용자 생성에 `iam:PutUserPermissionsBoundary`, `iam:AttachUserPolicy`를 주기: boundary를 바꾸거나 관리형 정책을 붙이는 길이 생긴다. AWS 문서의 위임 예시처럼 `iam:CreateUser`에 `iam:PermissionsBoundary` 조건을 거는 것으로 충분하다고 보고 제외했다(실제 호출로는 확인하지 않았다).
- 시크릿 키를 사람이 만들어 넣기(ADR 0033 방식): Terraform state에 남지 않지만 platform ADR 0019가 Terraform 생성을 정했다. 키를 잃으면 시크릿을 되살릴 수 없으므로 state에도 남는 쪽이 낫다고 보았다.
- 인증서를 Secret에서 읽기(`kubectl get secret -l sealedsecrets.bitnami.com/sealed-secrets-key=active`): 출력에서 `tls.crt`만 고르더라도 개인키가 클라이언트로 전송된다. 컨트롤러의 `/v1/cert.pem`을 API 서버 서비스 proxy로 읽는 방법(kubeseal `--fetch-cert`와 같은 경로)을 골랐다.
- 인증서를 Terraform으로 SSM에 넣기: Terraform이 private 워크로드 API에 닿지 않는다. 스크립트로 넣는다.
- 시크릿 키가 없을 때 init 컨테이너를 통과시키는 방법으로 `describe-parameters`로 먼저 확인하기: 계정 전체 파라미터 이름을 볼 수 있는 권한이 필요하다. `get-parameters`는 없는 이름을 `InvalidParameters`로 돌려주고 성공하므로, 권한이나 네트워크 오류는 그대로 실패시키면서 없는 경우만 통과시킬 수 있다.
- SealedSecret에 sync-wave를 두지 않기: Argo CD는 같은 wave에서 Deployment를 SealedSecret보다 먼저 적용한다. 재배포할 때 새 파드가 컨트롤러가 Secret을 바꾸기 전의 값으로 뜰 수 있다.

## 결정과 이유

### 앱 차트(`helm/charts/hopcharlie-app`)

- `values.sealedSecrets.s3`가 있으면 SealedSecret `app-s3`(키 `accessKey`, `secretKey`), `values.sealedSecrets.app`이 있으면 SealedSecret `app-secrets`를 릴리스 네임스페이스에 그린다. `bitnami.com/v1alpha1`, strict 범위(범위 annotation 없음), `spec.template`은 이름과 네임스페이스, `type: Opaque`다.
- SealedSecret은 sync-wave -1(Namespace와 같은 wave)이다. Argo CD는 이 wave가 Healthy가 된 뒤 Deployment를 바꾼다. Argo CD의 SealedSecret health 검사는 `status.observedGeneration`이 `metadata.generation`과 같고 `Synced` 조건이 True일 때 Healthy다(Argo CD master의 `resource_customizations/bitnami.com/SealedSecret/health.lua` 기준, 설치된 버전에서는 미확인). 그래서 재배포로 암호문이 바뀌어도 컨트롤러가 새 값을 반영한 뒤에 파드가 바뀐다. 복호화에 실패하면(Degraded) sync가 실패하고 옛 파드가 계속 돈다.
- `app`이 있으면 모든 서비스 컨테이너에 `envFrom: [{secretRef: {name: app-secrets}}]`를 넣고, 파드 템플릿 annotation `hopcharlie.dev/secrets-checksum`에 `appChecksum`을 쓴다. 같은 이름이면 `env`가 `envFrom`보다 이기며, 플랫폼이 시크릿과 같은 이름을 `env`에서 빼므로 차트는 따로 하지 않는다.
- `S3_BUCKET`을 `<bucketPrefix><namespace>`로 바꾼다(`-<resource>`를 뺀다).
- 스키마는 정확히 `s3`, `app`, `appChecksum`만 허용한다. 암호문은 base64 문자열, 시크릿 이름은 `^[A-Z_][A-Z0-9_]*$`이고 64자까지이며 `PORT`, `DATABASE_URL`, `S3_*`는 안 된다. `appChecksum`은 소문자 hex 64자이고 `app`과 함께만 온다.
- `values.sealedSecrets.s3`가 있는데 `profile.s3`가 Secret `app-s3`와 기본 키 이름을 읽지 않으면 렌더링을 실패시킨다. strict 봉인은 이름 `app-s3`에 묶이기 때문이다.

### AppProject `hopcharlie-apps`

- `namespaceResourceWhitelist`에 `bitnami.com/SealedSecret`을 더한다. Secret은 Argo CD가 아니라 컨트롤러가 만들고, SealedSecret을 owner로 가지므로 Argo CD가 지우지 않는다. 그래서 Secret은 계속 허용하지 않는다.

### `terraform/platform/aws`([app-storage.tf](../../terraform/platform/aws/app-storage.tf))

- 관리형 정책 `hopcharlie-app-boundary`: `arn:aws:s3:::hopcharlie-eks-*`에 `s3:ListBucket`, `s3:ListBucketMultipartUploads`, `s3:GetBucketLocation`, 그 객체에 `s3:GetObject`, `s3:PutObject`, `s3:DeleteObject`, `s3:GetObjectTagging`, `s3:PutObjectTagging`, `s3:AbortMultipartUpload`, `s3:ListMultipartUploadParts`만 허용한다.
- 플랫폼 pod 역할의 인라인 정책 `app-storage`:
  - `hopcharlie-eks-*` 버킷에 `s3:CreateBucket`, `s3:PutBucketTagging`, `s3:PutBucketPublicAccessBlock`, `s3:GetBucketLocation`, `s3:ListBucket`, `s3:DeleteBucket`
  - 그 객체에 `s3:DeleteObject`(버킷을 비우기 위해)
  - `user/hopcharlie-apps/*`에 `iam:CreateUser`, `iam:PutUserPolicy`, `iam:DeleteUserPolicy`. 조건 `iam:PermissionsBoundary`가 위 boundary ARN과 같을 때만
  - `user/hopcharlie-apps/*`에 `iam:TagUser`, `iam:GetUser`, `iam:DeleteUser`, `iam:CreateAccessKey`, `iam:DeleteAccessKey`, `iam:ListAccessKeys`
  - 관리형 정책 붙이기, boundary 바꾸기와 지우기, 로그인 프로필, 그룹, 사용자 이름과 경로 바꾸기, 정책 수정은 주지 않는다. mock test가 이 목록이 없음을 확인한다.
- `random_bytes`(32바이트, random provider 3.9.1)로 키를 만들고 SSM SecureString `/hopcharlie/app-secrets-key`에 base64로 넣는다.
- `host/envs.yaml`의 `eks`에 `sealedSecretsCert: /config/sealed-secrets/eks.pem`과 `storage`(`driver: aws`, `bucketPrefix: hopcharlie-eks-`, `region: ap-northeast-2`, `iamPath: /hopcharlie-apps/`, `permissionsBoundary: arn:aws:iam::525091405886:policy/hopcharlie-app-boundary`)를 더한다. 계정 ID는 다른 values 파일처럼 그대로 쓰고, output `app_permissions_boundary_arn`과 mock test로 맞는지 확인한다.

### 플랫폼 차트의 `fetch-settings.sh`

- `app-secrets-key`를 `get-parameters`로 읽어 있으면 `api.env`에 `PLATFORM_SECRETS_KEY`로 쓴다. 없으면 쓰지 않고 API는 시크릿 API에 503을 돌려준다.
- `$PREFIX/sealed-secrets/` 바로 아래 파라미터(String, 이름은 환경 이름)를 `/config/sealed-secrets/<env>.pem`으로 복사한다. 이름 검사는 kube 루프와 같고, 하나도 없으면 디렉터리도 만들지 않는다.

### 인증서 스크립트([scripts/put-sealed-secrets-cert.sh](../../scripts/put-sealed-secrets-cert.sh))

- 워크로드 API 터널(`make aws-tunnel CLUSTER=workload`)의 kubeconfig로 `/api/v1/namespaces/kube-system/services/http:sealed-secrets-controller:/proxy/v1/cert.pem`을 읽어, 인증서 하나이고 개인키가 없는지 확인한 뒤 SSM String `/hopcharlie/sealed-secrets/eks`에 넣는다. `tf.sh`처럼 해커톤 계정이 아닌 프로필은 거부하고, subject, 만료일, SHA-256 지문만 출력한다.

## 영향과 후속 작업

- 기대 효과: 플랫폼 구현이 배포되면 s3 리소스가 있는 앱(moimroom의 `photos`)과 시크릿이 있는 앱이 워크로드 클러스터에서 뜬다. 이후 앱이나 브랜치를 더해도 infra 변경은 없다.
- 단점과 위험:
  - 적용 순서가 중요하다. 플랫폼 API는 envs.yaml의 모르는 필드를 거부하므로, `sealedSecretsCert`와 `storage`를 읽는 플랫폼 이미지가 먼저 돌아야 한다. 옛 이미지에서 envs.yaml이 바뀐 뒤 API가 다시 시작하면 뜨지 않는다. 그 이미지가 아직이면 envs.yaml 커밋을 이 PR에서 빼고 따로 올린다. 순서는 [terraform/platform/aws/README.md](../../terraform/platform/aws/README.md#앱-버킷과-시크릿)에 있다.
  - 시크릿 키는 SSM과 Terraform state에만 있다. `random_bytes.app_secrets_key`가 교체되면 저장된 시크릿을 모두 다시 넣어야 하므로, plan에 이 리소스의 replace가 보이면 apply하지 않는다.
  - 플랫폼 역할은 boundary 범위 안에서 모든 `hopcharlie-eks-*` 버킷의 객체를 쓸 수 있는 사용자를 만들 수 있다. 버킷 하나로 좁히는 것은 플랫폼이 쓰는 인라인 정책에 달려 있다.
  - boundary가 플랫폼 인라인 정책보다 좁으면 그 차이만큼의 호출은 조용히 거부된다. 플랫폼 구현이 보고하는 정책 문서와 맞춰야 한다. 버킷 CORS처럼 버킷 설정이 필요한 앱은 지금 범위로는 안 된다.
  - boundary를 쓰는 사용자가 남아 있으면 `./tf.sh destroy`가 정책 삭제에서 실패한다. 앱을 먼저 지운다.
  - S3 키를 바꿔도 파드는 다시 뜨지 않는다(checksum은 앱 시크릿만 다룬다). 키 교체는 platform ADR 0016처럼 나중에 더한다.
  - 컨트롤러 키를 잃거나 sealed-secrets를 다시 설치하면 기존 암호문이 열리지 않아 SealedSecret이 Degraded가 되고 sync가 멈춘다. 새 인증서를 스크립트로 다시 넣고 API를 다시 시작한 뒤 재배포한다. 컨트롤러 개인키 백업은 이 ADR의 범위가 아니다.
  - 버킷 이름이 바뀌므로 이미 s3 리소스로 떠 있는 앱이 있다면 다른 버킷을 보게 된다. platform ADR 0016에 따르면 지금 그런 앱은 없다.
  - 손으로 만든 `app-s3` Secret이 이미 있는 네임스페이스에서는 컨트롤러가 덮어쓰지 않아 Degraded가 된다. 그 Secret을 지운 뒤 다시 sync한다.
  - platform 저장소의 `deploy/charts/hopcharlie-app` 복사본은 이 변경을 따라가야 kind E2E가 같은 차트를 검사한다.
- 필요한 후속 작업 및 검증:
  - 적용(각 단계 실행 전 승인): 플랫폼 이미지 확인, 인증서 스크립트, merge, `./tf.sh plan`과 apply, API 재시작([절차](../../terraform/platform/aws/README.md#앱-버킷과-시크릿)).
  - 플랫폼 구현이 보고하는 S3, IAM 호출과 인라인 정책 문서로 `app-storage.tf`의 두 정책을 맞춘다.
  - 적용 뒤 확인: API 로그에 envs.yaml과 인증서 오류가 없는지, 시크릿 API가 503이 아닌지, moimroom을 배포해 SealedSecret 두 개가 Healthy이고 파드가 `S3_BUCKET=hopcharlie-eks-moimroom`으로 버킷에 쓰는지.
  - 다른 클러스터(GKE, 온프렘)의 인증서와 저장소 드라이버는 따로 정한다.

## 관련 자료

- [helm/charts/hopcharlie-app/templates/sealedsecrets.yaml](../../helm/charts/hopcharlie-app/templates/sealedsecrets.yaml), [deployment.yaml](../../helm/charts/hopcharlie-app/templates/deployment.yaml), [values.schema.json](../../helm/charts/hopcharlie-app/values.schema.json)
- [helm/charts/hopcharlie-apps/templates/appproject.yaml](../../helm/charts/hopcharlie-apps/templates/appproject.yaml)
- [helm/charts/hopcharlie-platform/templates/settings.yaml](../../helm/charts/hopcharlie-platform/templates/settings.yaml)
- [terraform/platform/aws/app-storage.tf](../../terraform/platform/aws/app-storage.tf), [tests/app-storage.tftest.hcl](../../terraform/platform/aws/tests/app-storage.tftest.hcl), [host/envs.yaml](../../terraform/platform/aws/host/envs.yaml)
- [scripts/put-sealed-secrets-cert.sh](../../scripts/put-sealed-secrets-cert.sh), [scripts/check-helm.py](../../scripts/check-helm.py)
- platform 저장소 `docs/adr/0016-app-object-storage.md`, `docs/adr/0019-app-secrets-and-redeploy.md`
