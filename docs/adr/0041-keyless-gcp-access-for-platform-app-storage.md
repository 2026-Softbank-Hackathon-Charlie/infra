# 0041. 플랫폼 gcs 드라이버에 키 없는 GCP 권한을 준다

- 날짜: 2026-11-04
- 상태: 제안
- 관련 ADR: [ADR 0021](0021-keyless-argocd-gke-access.md), [ADR 0035](0035-app-storage-and-secrets-infra.md), [ADR 0040](0040-use-gcs-for-gke-app-buckets.md), platform 저장소 ADR 0016(앱 오브젝트 스토리지)

## 배경과 제약

[ADR 0040](0040-use-gcs-for-gke-app-buckets.md)으로 GKE 앱 파일 저장소는 GCS의 S3 호환 API를 쓴다. 앱마다 버킷과 키는 플랫폼 API의 `gcs` 드라이버가 만든다(platform ADR 0016, 구현은 [platform#57](https://github.com/2026-Softbank-Hackathon-Charlie/platform/issues/57)). 플랫폼 API는 관리 EKS에서 돌기 때문에 GCP를 부를 자격 증명이 필요하다.

- 확인한 사실:
  - Argo CD는 관리 EKS의 projected token으로 Google WIF를 거쳐 GKE에 접근한다([ADR 0021](0021-keyless-argocd-gke-access.md), `terraform/integrations/gcp/argocd`).
  - 플랫폼 API, 게이트웨이, PostgreSQL pod가 같은 Kubernetes 서비스 계정 `charlie-platform/hopcharlie-platform`을 쓴다([ADR 0035](0035-app-storage-and-secrets-infra.md)).
  - GCP 프로젝트에 `iam`, `iamcredentials`, `sts`, `storage` API가 켜져 있다.
  - IAM Conditions의 `resource.name`은 Cloud Storage 버킷과 객체만 지원한다. 서비스 계정과 HMAC 키에는 조건을 걸 수 없다([IAM 문서](https://cloud.google.com/iam/docs/conditions-resource-attributes)).
  - 해커톤 프로젝트는 주최 측 조직에 있어서 앱 전용 프로젝트를 따로 만들 수 없다.
- 미확인: `storage.buckets.create`에 이름 조건이 실제로 적용되는지. apply 후 다른 이름 버킷 생성이 거부되는지 확인한다.

## 검토한 대안

- 서비스 계정 키를 SSM에 두기(platform ADR 0016 문구): 장기 키가 SSM과 pod 메모리에 남고, 키 교체를 따로 해야 한다.
- Terraform이 앱용 서비스 계정을 미리 만들어 두기: 서비스 계정 삭제 권한은 없어지지만 HMAC 키 위험은 그대로이고, 앱 수가 고정된다.
- 앱 전용 GCP 프로젝트: 범위를 가장 잘 나누지만 이 해커톤 조직에서는 만들 수 없다.
- 키 없는 WIF와 프로젝트 범위의 서비스 계정·HMAC 권한(선택)

## 결정과 이유

### Terraform 스택 [terraform/integrations/gcp/platform-storage](../../terraform/integrations/gcp/platform-storage/)

- Argo와 다른 WIF pool `charlie-platform`과 provider `management-eks`. 관리 EKS issuer의 subject `system:serviceaccount:charlie-platform:hopcharlie-platform` 하나만 믿는다. audience는 이 provider의 HTTPS 주소다.
- Google 서비스 계정 `charlie-platform-storage`. 위 subject에만 `roles/iam.workloadIdentityUser`를 준다.
- 커스텀 역할 두 개:
  - `charliePlatformAppBuckets`: 버킷 생성·조회·수정·삭제, 버킷 IAM 조회·설정, 객체 목록·삭제. 조건 `resource.name.startsWith("projects/_/buckets/hopcharlie-gke-")`로 앱 버킷에만 준다. 객체 읽기와 쓰기는 주지 않는다(앱 파일을 볼 필요가 없다).
  - `charliePlatformAppIdentities`: 서비스 계정 생성·조회·목록·삭제, HMAC 키 생성·조회·목록·수정·삭제. 조건을 걸 수 없어 프로젝트 전체다.
- 서비스 계정 가장(`actAs`, `getAccessToken`), 서비스 계정 키, 프로젝트 IAM 변경은 주지 않는다. mock test가 확인한다.
- state는 기존 GCS state 버킷의 `gcp/dev/platform-storage`. CI는 init, validate, mock test만 하고 배포 파이프라인에는 넣지 않는다(Argo 스택과 같다).

### 플랫폼 차트(`helm/charts/hopcharlie-platform`)

- 값 `gcp.credentialConfig`(Terraform 출력 `federation_config`, 공개 정보)와 `gcp.tokenAudience`가 있으면 API pod에만 다음을 더한다.
  - ConfigMap `hopcharlie-gcp-adc`의 `adc.json`을 `/etc/gcp`에, 환경 변수 `GOOGLE_APPLICATION_CREDENTIALS=/etc/gcp/adc.json`
  - 1시간짜리 projected token(audience는 위 provider)을 `/var/run/secrets/gcp/token`에
- 두 값은 함께 있어야 하고, 비어 있으면 아무것도 바뀌지 않는다.
- 게이트웨이와 PostgreSQL pod는 같은 서비스 계정을 쓰지만 이 audience의 토큰을 받지 않으므로 GCP 권한이 없다. 두 pod는 Kubernetes API로 토큰을 만들 권한도 없다.

## 영향과 후속 작업

- 기대 효과: 장기 키 없이 플랫폼이 GKE 앱 버킷, 앱 서비스 계정, HMAC 키를 관리한다. 드라이버는 Google 기본 인증만 쓴다.
- 단점과 위험:
  - 플랫폼 GCP 계정은 프로젝트의 **모든** 서비스 계정을 지울 수 있고, 어떤 서비스 계정에든 HMAC 키를 만들 수 있다. HMAC 키를 받은 서비스 계정이 읽을 수 있는 버킷(예: Terraform state)에 접근할 수 있다. 완화: 드라이버는 정해진 접두사와 설명이 붙은 서비스 계정만 다룬다(platform#57), 감사 로그로 추적한다, 해커톤 이후 앱 전용 프로젝트로 옮긴다.
  - 플랫폼 API pod가 탈취되면 위 권한이 그대로 넘어간다. AWS 쪽 플랫폼 역할([ADR 0035](0035-app-storage-and-secrets-infra.md))과 같은 수준의 위험이다.
  - `hopcharlie-gke-*` 버킷 이름은 전 세계에서 하나뿐이어서 다른 프로젝트가 먼저 쓰면 생성이 실패한다.
- 필요한 후속 작업(각각 별도 승인):
  1. `terraform apply`(backend: 기존 GCP state 버킷)
  2. 검증: 플랫폼 계정으로 `hopcharlie-gke-probe` 버킷 생성·삭제 허용, 다른 이름 버킷 생성 거부, 시험 pod에서 토큰 교환
  3. 플랫폼 차트 값(`clusters/aws-dev-management/values/hopcharlie-platform.yaml`)에 `gcp` 추가. platform#57 드라이버가 배포될 때 함께 한다.
  4. platform ADR 0016의 "GCP는 SSM에 둔 값" 문구를 이 방식으로 고친다(platform#57).

## 관련 자료

- [terraform/integrations/gcp/platform-storage/main.tf](../../terraform/integrations/gcp/platform-storage/main.tf), [tests/access.tftest.hcl](../../terraform/integrations/gcp/platform-storage/tests/access.tftest.hcl)
- [helm/charts/hopcharlie-platform/templates/api.yaml](../../helm/charts/hopcharlie-platform/templates/api.yaml), [scripts/check-helm.py](../../scripts/check-helm.py)
- [terraform/integrations/gcp/argocd](../../terraform/integrations/gcp/argocd/)
