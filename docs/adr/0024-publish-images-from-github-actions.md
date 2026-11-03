# 0024. 플랫폼과 콘솔 이미지는 각 저장소의 GitHub Actions가 게시하고 infra PR을 연다

- 날짜: 2026-11-03
- 상태: 제안. infra PR과 사람 머지 부분은 [ADR 0025](0025-pin-images-on-infra-main.md)가 대체한다
- 관련 ADR: [ADR 0016](0016-run-hopcharlie-platform-on-management-cluster.md), [ADR 0022](0022-platform-app-build.md)

## 배경과 제약

- 플랫폼(API, 게이트웨이)과 콘솔 이미지는 digest로 고정해 플랫폼 values와 [helm/images.lock.json](../../helm/images.lock.json)에 기록한다(ADR 0016). 새 이미지를 내보내는 일은 이 두 곳을 바꾸는 infra PR이다.
- platform 저장소에는 이미지를 빌드하는 CI가 없어서, 사람이 `deploy/aws/push-image.sh`로 빌드해 ECR에 올리고 digest PR을 손으로 만든다(예: #14).
- frontend 저장소에는 콘솔 이미지를 빌드해 ECR에 올리고 infra PR을 여는 workflow(frontend#5)가 있다. 그러나 OIDC 역할, 저장소 변수, infra 쓰기 토큰이 없어 한 번도 실행되지 않았다.
- 앱 배포 인프라 계획의 8번 "digest 작성 담당"이 미정으로 남아 있었다. 2026-11-03 팀 Slack에서 플랫폼도 같은 workflow를 두고 두 저장소의 토큰을 설정하기로 했다.
- 계정에는 GitHub OIDC provider(`token.actions.githubusercontent.com`)가 이미 있다. GitHub Actions의 subject는 소유자와 저장소 ID가 들어간 immutable 형식이다(ADR 0012).

## 검토한 대안

- 사람이 계속 로컬에서 빌드하기: 빌드한 커밋과 환경이 기록되지 않고, 담당자 노트북에 Docker와 AWS 자격 증명이 있어야 한다.
- 두 저장소가 역할 하나를 같이 쓰기: 설정은 줄지만 frontend가 플랫폼 이미지를, platform이 콘솔 이미지를 덮어쓸 수 있다. frontend README도 역할을 그 저장소 main으로만 제한하도록 적고 있다.
- infra의 workflow가 다른 저장소를 체크아웃해 빌드하기: infra에 두 저장소의 읽기 권한과 빌드 도구가 필요하고, 빌드를 시작한 커밋과 저장소의 관계가 흐려진다.
- workflow가 infra main에 직접 커밋하기: 리뷰 없이 운영 이미지가 바뀐다.

## 결정과 이유

- `terraform/platform/aws`에 저장소마다 역할 하나를 둔다([image-publish.tf](../../terraform/platform/aws/image-publish.tf)).
  - `hopcharlie-platform-image-publish`: platform main만 신뢰하고 ECR `hopcharlie/platform`에만 push한다.
  - `hopcharlie-frontend-image-publish`: frontend main만 신뢰하고 ECR `hopcharlie/console`에만 push한다.
  - 신뢰 조건은 `aud=sts.amazonaws.com`과 immutable subject의 정확한 일치다. 권한은 해당 저장소의 layer, image push와 `ecr:GetAuthorizationToken`뿐이고, 저장소 생성과 삭제는 없다.
- 두 workflow는 main에서 수동으로 실행한다. 빌드한 이미지를 커밋 SHA 7자리 태그로 올리고, infra main의 해당 이미지 pin과 lock 항목만 바꾸는 PR을 연다. 머지는 인프라 담당이 리뷰한 뒤 한다.
- infra PR을 여는 토큰(`INFRA_PR_TOKEN`)은 infra 저장소에만 Contents, Pull requests 쓰기 권한이 있는 fine-grained PAT로 하고, 두 저장소의 Actions secret에 넣는다. 유효기간은 대회 종료(2026-11-08) 직후로 둔다. 조직이 Free 플랜이라 비공개 저장소는 조직 secret을 쓸 수 없다.
- 이 결정으로 앱 배포 인프라 계획 8번의 digest 작성 담당은 "각 저장소의 workflow"이고, 리뷰와 머지는 인프라 담당이다.

## 영향과 후속 작업

- 기대 효과: 이미지 빌드가 GitHub Actions에 기록되고, 담당자 노트북 없이 버튼 한 번으로 digest PR이 생긴다.
- 단점과 위험:
  - PAT는 만든 사람의 권한으로 동작하고 PR 작성자도 그 사람이 된다. 유효기간이 지나면 workflow가 PR 단계에서 실패한다.
  - workflow가 연 PR을 머지해도 플랫폼이 Helm 릴리스인 동안에는 `helm upgrade`나 Argo CD sync를 따로 해야 반영된다.
  - 게시 역할이 탈취되면 해당 ECR 저장소에 이미지를 올릴 수 있다. 운영 반영은 digest PR을 거치므로 리뷰 없이 바뀌지는 않는다.
- 필요한 후속 작업 및 검증:
  - `terraform/platform/aws` apply로 두 역할을 만든다(승인 필요).
  - 두 저장소에 변수 `AWS_DEPLOY_ROLE_ARN`과 ECR 저장소 주소, secret `INFRA_PR_TOKEN`을 넣는다.
  - 각 workflow를 한 번 실행해 ECR push와 infra PR 생성을 확인한다.

## 관련 자료

- [terraform/platform/aws/image-publish.tf](../../terraform/platform/aws/image-publish.tf), [tests/image-publish.tftest.hcl](../../terraform/platform/aws/tests/image-publish.tftest.hcl)
- frontend 저장소 `.github/workflows/deploy.yml`, `.github/scripts/open-console-pr.py`
- platform 저장소의 이미지 게시 workflow
