# 0075. 지운 moimroom의 앱 목록 파일과 이미지 게시 자원을 없앤다

- 날짜: 2026-11-07
- 상태: 제안
- 관련 ADR: [0024](0024-publish-images-from-github-actions.md), [0026](0026-retire-platform-ec2-host.md), [0029](0029-add-gcp-app-image-repository.md), [0030](0030-publish-moimroom-to-aws-and-gcp.md), [0063](0063-born-paired-app-databases-for-two-environment-apps.md), [0065](0065-image-scanner-reads-artifact-registry.md)

## 배경과 제약

- 사용자가 2026-11-07 12:59(KST)에 플랫폼에서 앱 moimroom(EKS namespace `moimroom`, onprem DB `moimroom-db-onprem`)을 지웠다. 손으로 만든 GKE 사본은 infra #130에서 이미 지웠다.
- 플랫폼 앱 목록 파일 `terraform/platform/aws/host/apps.yaml`에는 moimroom 하나만 있었다. 이 파일은 SSM `/hopcharlie/host/apps.yaml`로 올라가고, 플랫폼 차트의 `fetch-settings.sh`가 `/config/apps.yaml`로 받아 `PLATFORM_APPS_FILE`로 넘긴다. 그래서 콘솔과 CLI가 지운 앱을 "앱 파일에 있는 이전 대상"으로 계속 보여 준다.
- platform `internal/apps/apps.go`의 `New`는 앱이 0개면 `no apps configured`로 실패하고, `cmd/api/main.go`는 이를 `fatal`로 처리한다. 따라서 `apps: []`로 비우면 API가 뜨지 않는다.
- 같은 `main.go`에서 `PLATFORM_APPS_FILE`은 선택 사항이다. 비어 있으면 파일 목록 없이 시작하고, `PLATFORM_APP_DB_HOST_TEMPLATES`가 있으면 `apps.Empty`에 앱 등록부(`WithRegistry`)를 붙여 등록된 앱의 DB 쌍으로 이전 계획을 만든다. 등록 앱의 DB 주소, 비밀번호 변수, 버킷도 정책에 합쳐진다(`plans.Policy`의 registry 병합). 관리 클러스터 값은 `appDbTopology.hostTemplates`를 설정해 두었다.
- moimroom-mig(eks+gke)는 앱 등록부에서 오고 이미지는 플랫폼 빌드 경로 `hopcharlie/apps/moimroom-mig/<service>`(ECR)와 AR `hopcharlie-apps`를 쓴다. Terraform이 만든 ECR `hopcharlie/apps/moimroom`, 게시 역할 `hopcharlie-moimroom-image-publish`, GCP의 `charlie-moimroom-publisher`와 AR `charlie-apps`는 moimroom 저장소의 CI만 썼다.

## 검토한 대안

- `apps: []`로 비운다: 변경은 가장 작지만 API가 시작하지 못한다. 제외했다.
- platform을 고쳐 빈 목록을 허용한다: platform PR이 따로 필요하고, 파일을 남길 이유가 없다. 제외했다.
- 차트에서 파일을 넘기지 않는다: 플랫폼이 이미 지원하는 경로라 platform 변경이 없다. 선택했다.
- ECR·AR 저장소를 남기고 역할만 지운다: 지운 앱의 이미지와 비용이 남는다. 제외했다.

## 결정과 이유

- 플랫폼 차트가 `PLATFORM_APPS_FILE`을 넘기지 않고 `host/apps.yaml`을 받지 않는다. `host/apps.yaml`과 그 SSM 파라미터를 Terraform에서 지운다. 이전 대상 앱은 앱 등록부에서만 온다.
- ECR `hopcharlie/apps/moimroom`, 게시 역할과 정책, `moimroom_*` 출력을 지운다.
- GCP bootstrap의 AR `charlie-apps`, `charlie-moimroom-publisher`, WIF provider `moimroom`과 그 권한, 관련 변수와 출력을 지운다. app-images의 스캐너 추가 저장소 기본값에서 `charlie-apps`를 뺀다.

## 영향과 후속 작업

- 기대 효과: 콘솔과 CLI에 지운 앱이 보이지 않는다. 쓰지 않는 저장소와 신뢰 관계가 사라진다.
- 단점과 위험: 차트 변경이 배포되기 전에 SSM 파라미터를 지우면 API pod가 다시 시작할 때 `fetch-settings.sh`가 실패한다. 적용 순서를 지켜야 한다. ECR 저장소는 `force_delete = false`라 이미지가 있으면 apply가 실패한다. AR 저장소를 지우면 안의 이미지도 함께 지워진다.
- 필요한 후속 작업 및 검증(사람이 승인 후 실행):
  1. 이 PR을 merge하고 Argo CD가 `charlie-management-hopcharlie-platform`을 sync한 뒤 API pod가 Ready인지, 로그에 `plans for registered apps with a database pair enabled`가 있는지 본다.
  2. ECR `hopcharlie/apps/moimroom`의 이미지를 지운 뒤 `terraform/platform/aws`를 plan해 `host_file["apps.yaml"]`, ECR, 게시 역할과 정책 삭제만 있는지 확인하고 apply한다.
  3. `terraform/integrations/gcp/app-images`를 plan해 스캐너의 `charlie-apps` 읽기 권한 삭제만 있는지 확인하고 apply한다.
  4. `terraform/bootstrap/gcp`의 로컬 tfvars에서 `enable_moimroom_image_publishing`, `moimroom_repository_id`를 지우고 plan해 AR `charlie-apps`, 게시 SA, provider, 권한 삭제만 있는지 확인하고 apply한다.
  5. SSM `db-passwords`와 `rclone-env`에 moimroom 전용 값(`EKS_MOIMROOM_PGPASSWORD`, `ONPREM_MOIMROOM_PGPASSWORD`, minio 원격)이 남아 있으면 지운다. moimroom 저장소의 이미지 게시 workflow 변수도 정리한다.

## 관련 자료

- [helm/charts/hopcharlie-platform/templates/api.yaml](../../helm/charts/hopcharlie-platform/templates/api.yaml)
- [helm/charts/hopcharlie-platform/templates/settings.yaml](../../helm/charts/hopcharlie-platform/templates/settings.yaml)
- [terraform/platform/aws/main.tf](../../terraform/platform/aws/main.tf)
- [terraform/platform/aws/image-publish.tf](../../terraform/platform/aws/image-publish.tf)
- [terraform/bootstrap/gcp/main.tf](../../terraform/bootstrap/gcp/main.tf)
- [terraform/integrations/gcp/app-images/variables.tf](../../terraform/integrations/gcp/app-images/variables.tf)
- platform `cmd/api/main.go`(`PLATFORM_APPS_FILE`, `PLATFORM_APP_DB_HOST_TEMPLATES`), `internal/apps/apps.go`(`New`, `Empty`, `WithRegistry`)
