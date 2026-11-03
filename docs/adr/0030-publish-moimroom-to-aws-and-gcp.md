# 0030. 모임룸 이미지를 GitHub Actions에서 AWS·GCP에 함께 게시

- 날짜: 2026-11-04
- 상태: 채택. 코드 준비 결정이며 실제 적용·게시 승인은 별도다.
- 관련 ADR: [0012](0012-match-github-immutable-oidc-subject.md), [0024](0024-publish-images-from-github-actions.md), [0029](0029-add-gcp-app-image-repository.md)

## 배경과 제약

사용자는 모임룸 이미지 자동 업로드와 AWS ECR·GCP Artifact Registry 양쪽 게시를 요청했다. 저장소와 게시 권한은 Terraform으로 관리한다. 기존 GCP `charlie-apps`와 노드 reader, AWS 계정 공용 GitHub OIDC 제공자, 모임룸 Dockerfile을 재사용한다. 앱·DB 배포는 이번 범위에 포함하지 않는다.

## 검토한 대안

- 로컬에서 계속 빌드·게시: 운영자 환경과 자격 증명에 의존한다.
- 클라우드별 빌드 두 번: 시간이 늘고 두 이미지의 내용이 달라질 수 있다.
- GitHub Actions에서 한 번 빌드해 양쪽 게시: 기존 소스 CI와 연결하며 선택한다.

## 결정과 이유

- 모임룸 저장소의 `.github/workflows/publish-image.yml`이 프론트엔드·Go 검사를 통과한 `main` push 또는 `main` 수동 실행에서만 게시한다. PR·다른 브랜치 검사는 클라우드 인증을 사용하지 않는다.
- AWS는 기존 `terraform/platform/aws`의 게시 역할·정책 패턴에 모임룸을 추가하고, 전용 ECR `hopcharlie/apps/moimroom`을 생성한다. immutable 태그를 사용하고 자동 삭제 정책은 추가하지 않는다. bootstrap은 기존 state 버킷 소유권을 유지한다.
- AWS 신뢰는 실제 저장소 API에서 확인한 `repo:2026-Softbank-Hackathon-Charlie@350278309/moimroom@1442480764:ref:refs/heads/main`과 `aud=sts.amazonaws.com`의 정확한 일치다. 권한은 전용 ECR 게시와 인증 토큰에 한정한다.
- GCP는 기존 `terraform/bootstrap/gcp`에 선택적으로 모임룸 전용 서비스 계정·WIF 제공자·서비스 계정 신뢰·기존 저장소 writer를 추가한다. 기존 infra 제공자의 신뢰나 노드·배포 계정 권한은 넓히지 않는다. 새 제공자는 저장소 ID·조직 ID·`refs/heads/main`을 제한한다.
- 정적 AWS 키나 Google 서비스 계정 키를 발급하지 않는다. Google 액세스 토큰은 인증 파일 없이 Docker 로그인에 전달한다.
- `linux/amd64` 이미지를 한 번 빌드해 두 주소에 `git-<전체 SHA>-run-<실행 ID>-<시도 번호>`로 게시하고, 실행 요약에 양쪽 digest 주소를 남긴다. 재실행도 새 태그를 사용한다.

GitHub 저장소 변수는 다음 Terraform 출력으로 설정한다.

| GitHub 변수 | Terraform 출력 |
| --- | --- |
| `AWS_IMAGE_PUBLISH_ROLE_ARN` | AWS `moimroom_github_image_publish_role_arn` |
| `AWS_ECR_REPOSITORY` | AWS `moimroom_image_repository_name` |
| `GCP_WORKLOAD_IDENTITY_PROVIDER` | GCP `moimroom_image_workload_identity_provider` |
| `GCP_IMAGE_PUBLISH_SERVICE_ACCOUNT` | GCP `moimroom_image_publisher_service_account` |
| `GCP_IMAGE_REPOSITORY` | GCP `moimroom_image_repository` |

## 영향과 후속 작업

- 효과: 저장소·권한의 선언과 재현 가능한 이미지 게시를 분리해 관리한다. 플랫폼·콘솔 게시 권한은 유지한다.
- 위험: 두 registry 업로드는 원자적이지 않아 한쪽 성공 뒤 다른 쪽 실패가 가능하다. 실패 실행을 배포 완료로 취급하지 않고 새 실행 태그로 재시도한다. 이미지 보관·전송 비용은 각 클라우드에 발생한다.
- 후속: 실제 state를 사용하는 계획·적용, Git 게시, 변수 설정과 최초 실행은 구체적인 승인 후 수행한다. GCP opt-in 입력은 운영자의 기존 bootstrap 입력에 지속 보관해야 후속 계획에서 자원이 제거되지 않는다. 정적·mock 검증은 실제 인증·업로드 성공의 증거가 아니다.

## 관련 자료

- [AWS 게시 역할](../../terraform/platform/aws/image-publish.tf), [AWS 저장소](../../terraform/platform/aws/main.tf)
- [GCP bootstrap](../../terraform/bootstrap/gcp/main.tf)
- [모임룸 저장소](https://github.com/2026-Softbank-Hackathon-Charlie/moimroom)
- [Google GitHub 인증](https://github.com/google-github-actions/auth), [ECR 게시 권한](https://docs.aws.amazon.com/AmazonECR/latest/userguide/image-push-iam.html)
