# 0042. 플랫폼이 빌드한 앱 이미지를 Artifact Registry에도 올려 GKE가 받게 한다

- 날짜: 2026-11-04
- 상태: 제안
- 관련 ADR: [ADR 0022](0022-platform-app-build.md), [ADR 0039](0039-add-gke-app-environment.md), [ADR 0041](0041-keyless-gcp-access-for-platform-app-storage.md)

## 배경과 제약

플랫폼은 CodeBuild 프로젝트 `hopcharlie-app-build`로 앱 이미지를 빌드해 ECR `hopcharlie/apps/<app>/<service>`에 올린다([ADR 0022](0022-platform-app-build.md)). GKE 노드는 ECR 인증 수단이 없어 이 이미지를 받지 못한다.

- 확인한 사실:
  - GKE 노드 서비스 계정에 프로젝트 범위 `roles/artifactregistry.reader`가 있다(`terraform/bootstrap/gcp`). Artifact Registry(AR) 이미지는 pull secret 없이 받는다.
  - moimroom CI는 이미 같은 digest를 ECR과 AR `charlie-apps`에 함께 올린다.
  - buildspec은 플랫폼 코드(`internal/deploy/codebuild.go`)가 빌드마다 넘긴다.
  - GKE에 장기 AWS 키를 두지 않는다(팀 결정).

## 검토한 대안

- ECR 토큰을 GKE `imagePullSecrets`로 주입: 토큰이 12시간마다 만료돼 갱신 CronJob과 GKE 안의 AWS 자격 증명이 필요하다.
- AR remote repository로 ECR 프록시: ECR 자격 증명을 GCP에 저장해야 하고 토큰 갱신 문제가 같다.
- 빌드 때 AR에도 push(선택): GKE 쪽 설정이 없고, moimroom에서 이미 쓰는 방식이다.

## 결정과 이유

Terraform 스택 [terraform/integrations/gcp/app-images](../../terraform/integrations/gcp/app-images/):

- AR 저장소 `hopcharlie-apps`(asia-northeast3, Docker). moimroom CI의 `charlie-apps`와 분리해 앱 빌드가 moimroom 이미지를 덮어쓰지 못하게 한다.
- WIF pool `charlie-app-build`, AWS provider `aws-codebuild`. 빌드 계정의 역할 `hopcharlie-app-build` 세션만 믿는다(`attribute.aws_role` 조건). Argo와 플랫폼 API의 pool과 분리한다.
- Google 서비스 계정 `charlie-app-image-publisher`. 위 역할에만 `roles/iam.workloadIdentityUser`를 주고, 권한은 `hopcharlie-apps` 저장소의 `roles/artifactregistry.writer`뿐이다(삭제·프로젝트 권한 없음).
- 출력 `federation_config`는 AWS 세션으로 토큰을 교환하는 공개 ADC 설정이다. 키나 토큰은 없다.
- state는 기존 GCS state 버킷의 `gcp/dev/app-images`. CI는 init, validate, mock test만 한다.

플랫폼 쪽(별도 이슈):

- buildspec이 ECR push 뒤 같은 이미지를 `<image_prefix>/<app>/<service>`에 push한다. GCP 토큰은 CodeBuild 역할의 AWS 자격 증명과 `federation_config`로 받는다.
- gke 환경 배포 때 이미지 주소의 레지스트리 부분만 AR로 바꾼다. digest는 같다.

## 영향과 후속 작업

- 기대 효과: GKE에 AWS 자격 증명 없이 플랫폼 앱을 배포한다. GKE 쪽 설정은 바뀌지 않는다.
- 단점과 위험:
  - buildspec override와 privileged 빌드는 빌드 역할 권한으로 앱 Dockerfile을 실행한다([ADR 0022](0022-platform-app-build.md)). 이제 그 권한에 AR push가 더해진다. 범위는 ECR과 같이 앱 이미지 저장소 하나다.
  - 이미지 저장 비용이 두 곳에서 든다. 정리 정책은 아직 없다.
  - ECR과 AR의 digest가 같은지는 push 방식에 달려 있다. 플랫폼은 AR push 결과 digest를 확인해야 한다.
- 필요한 후속 작업(각각 별도 승인):
  1. `terraform apply`(backend: 기존 GCP state 버킷)
  2. 플랫폼 buildspec과 gke 이미지 주소 교체 구현
  3. 검증: CodeBuild에서 토큰 교환과 AR push, GKE에서 pull secret 없이 pull

## 관련 자료

- [terraform/integrations/gcp/app-images/main.tf](../../terraform/integrations/gcp/app-images/main.tf), [tests/access.tftest.hcl](../../terraform/integrations/gcp/app-images/tests/access.tftest.hcl)
- [terraform/platform/aws/app-build.tf](../../terraform/platform/aws/app-build.tf)
- [Google: AWS 워크로드 아이덴티티 제휴](https://cloud.google.com/iam/docs/workload-identity-federation-with-other-clouds)
