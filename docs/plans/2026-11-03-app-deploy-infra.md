# App deployment infrastructure implementation plan
> **For agentic workers:** REQUIRED SUB-SKILL: Use superpowers:subagent-driven-development for scoped implementation and independent review. User approval rules override automatic commit/push steps.

**Goal:** 사용자 명세의 인프라1~8을 순서대로 구현·적용·검증하고 실제 완료 근거를 Notion에 반영한다.
**Architecture:** 기존 플랫폼 CodeBuild Builder와 GitOps/Argo 경로를 재사용한다. infra main에 필요한 최소 추가를 branch/PR로 전달하며 기존 AWS/GCP 작업을 보존한다.
**Tech Stack:** Terraform>=1.9, locked AWS6.71.0/random3.9.1, Python, Helm/Argo, AWS CodeBuild/ECR/CloudWatch/EKS.
**Spec:** docs/plans/2026-11-03-app-deploy-infra-spec.md

## Global Constraints
위 명세의 전역 제약 전체를 따른다. base main8931283, branch codex/app-deploy-infra. 현재1번 실행, 뒤 단계는 선행 실제 결과를 받은 뒤 현재 소스를 다시 읽어 상세 contract를 확정한다.

## Review Focus
- ECR push가 hopcharlie/apps/*에만 허용되고 인증 token만 Resource=*인지 확인.
- 플랫폼이 특정 project StartBuild/BatchGetBuilds 및 해당 log group GetLogEvents만 받는지 확인.
- CodeBuild service trust가 CodeBuild와 해당 source account/project로 제한되는지 확인.
- validator가 새 Terraform 파일/test를 실제 포함하고 자격증명·state를 상속하지 않는지 확인.
- mock/코드 검증과 실제 기존 리소스/state/배포 검증을 혼동하지 않는지 확인.

## Task 1: CodeBuild 프로젝트와 ECR/IAM 연결 준비·인수
**Owner:** implementer는 제품 코드/검증/관련 문서, root는 이 plan/spec·세션 상태·승인·Notion·runtime 확인.
**Files:** create terraform/platform/aws/app-build.tf, terraform/platform/aws/tests/app-build.tftest.hcl, docs/adr/0022-platform-app-build.md. Modify scripts/check-platform-terraform.py, scripts/test-platform-terraform.py, terraform/platform/aws/README.md, README.md. 다른 파일은 필수 근거가 있을 때 root와 조율한다.
**Interfaces:** 기존 aws_iam_role.platform_pod 재사용. projectName=hopcharlie-app-build, app repo prefix=hopcharlie/apps/, logs=/aws/codebuild/hopcharlie-app-build. 플랫폼은 per-build buildspec과 GitHub token을 전달한다. 프로젝트에는 secret/token 값을 넣지 않는다.
**Settings:** NO_SOURCE, NO_ARTIFACTS, LINUX_CONTAINER, aws/codebuild/standard:7.0, BUILD_GENERAL1_SMALL, privileged=true, image_pull_credentials_type=CODEBUILD. build timeout15분, queued timeout15분, project concurrent limit1, log retention3일(기존 EKS 관례).
**IAM:** CodeBuild 전용 trust(서비스 codebuild.amazonaws.com, SourceAccount와 source project ARN), ECR CreateRepository/DescribeRepositories/BatchCheckLayerAvailability/InitiateLayerUpload/UploadLayerPart/CompleteLayerUpload/PutImage/BatchGetImage는 앱 repository ARN prefix만, GetAuthorizationToken만 *. 로그 writer는 생성된 group의 stream만. 플랫폼 role의 별도 inline policy는 특정 project StartBuild/BatchGetBuilds와 해당 streams GetLogEvents. StopBuild/PassRole/임의 registry/global logs 권한을 추가하지 않는다.
**Default buildspec:** 플랫폼 override 없이 실행하면 명확히 실패하는 최소 buildspec을 사용한다. 프로젝트 생성 요구를 만족하되 수동 빈 빌드를 성공 처리하지 않는다.
- [ ] 기존 clean-copy6테스트와 inventory220 baseline 기록. UTF8 process mode로 Windows cp949 환경 차이를 분리한다.
- [ ] Python 회귀시험을 먼저 바꿔 새 asset/test 파일 복사·host-only Terraform mock test 실행 요구가 현재 validator에서 실패함을 확인한다.
- [ ] mock AWS/random Terraform plan 시험을 추가해 project 설정·trust·ECR action/resource 분리·log 범위·플랫폼 범위를 검증한다. mock caller account/서브넷 IDs·AMI 등 기존 root의 데이터가 유효해야 한다.
- [ ] app-build.tf와 checker 최소 구현 후 fmt/validate/mock test·Python 회귀시험·inventory를 통과시킨다. init은 clean temp tree/backend=false/credentials isolated, test는 command=plan/mock providers만.
- [ ] SourceAccount/ARN scope·신규 정책의 과도 권한·기존 리소스 주소 보존을 독립 리뷰한다. 새 provider/lock 변경 없이 처리한다.
- [ ] 실제 추가 리소스와 변경·검증 결과를 설명한 뒤 현재 diff commit, branch push/PR, 구체적 AWS readonly preflight/plan 범위를 요청한다. 각각의 승인 범위를 구분한다.
- [ ] 승인 후 실제 계정·quota·동명자원·IAM/SCP와 state 소유권 확인, 실제 plan 검토. apply/build 역할검증은 정확한 영향·범위 승인 후 수행한다.
- [ ] 플랫폼 역할 실제 빌드·로그·digest/ECR 증거가 확인될 때 카드의 해당 완료 조건을 체크한다. 2번에 필요한 미구현 플랫폼 설정이 발견되면 의존성 사실을 설명하고 상태를 유지한다. 전체 목표/완료 범위를 줄이지 않는다.

## Task 2: 플랫폼 SSM·Argo RBAC·도메인
**Files:** helm/charts/hopcharlie-platform의 기존 template/values, terraform/platform/aws의 SSM 정의. 실제 현행 파일을 읽고 세부 contract를1번 인수 뒤 확정한다.
**Interfaces:** projectName을 PLATFORM_CODEBUILD_PROJECT로 사용. /hopcharlie/api-extra.env는 빈 Terraform 파라미터+팀 실제값 인계, argocd role 최소권한, GATEWAY_APP_DOMAIN=hopcharlie.dev.
- [ ] spec의2번 카드 조건·현재 이미지/환경변수 계약과 chart를 대조해 테스트부터 준비한다.
- [ ] 필요한 코드/PR/승인된 runtime 반영·권한 검증 후 실제 증거만 체크한다.

## Task 3: Workload LBC·CNPG
**Files:** helm/gitops, clusters/aws-dev-workload의 기존 addons 정의와 환경 연결.
**Interfaces:** 강종욱 기존 Argo 등록/실제 sync와 분담, C kind E2E의 고정 CNPG 버전.
- [ ] 기존 앱등록과 실제 sync를 구분하고 현재 설치·CRD·LB 상태를 승인 범위에서 확인한다.
- [ ] 필요한 addon 정의/검증/PR/승인 적용과 Ready·내부 NLB 증거를 확보한다.

## Task 4: 앱 차트·ApplicationSet·AppProject·환경 프로필
**Files:** C의 platform/deploy/charts/hopcharlie-app와 deploy/argocd를 인계받아 infra helm/charts/hopcharlie-app, helm/gitops, clusters/aws-dev-workload/values/hopcharlie-app.yaml에 최소 반영.
**Interfaces:** multi-source gitops revision, 앱별 namespace/Quota/LimitRange/NetworkPolicy, Namespace만 cluster resource 허용, 시스템 namespace 차단.
- [ ] C 원본·환경 값·입력 계약을 확인하고 render/mock/정책 시험부터 준비한다.
- [ ] PR/승인 적용·격리 앱 수명주기/시스템 namespace 거부 검증 후 완료를 체크한다.

## Task 5: DNS·Ingress·관리→workload 경로
**Files:** terraform/platform/aws/dns, 플랫폼 Ingress, 실제 소유 stack의 SG 정의.
**Interfaces:** *.hopcharlie.dev→공유 ALB→gateway→내부 NLB. api/console/app 우선순위 보존.
- [ ] 기존 DNS/TLS/SG 소유·현재값과 gateway 출발지·앱포트를 확인하고 제한된 변경을 준비한다.
- [ ] 승인된 변경 뒤 정상/거부 요청을 기존 공동 NetworkPolicy 카드와 함께 검증한다.

## Task 6: Service 조회 계정과 kubeconfig 인계
**Files:** 기존 workload RBAC/manifests와 platform put-kubeconfig.sh 계약, 위치는 현재 코드에서 확정.
**Interfaces:** 동윤 발급→C의 SSM /hopcharlie/kube/eks 저장, Service 읽기만. Argo/CNPG/샌드박스 관리자 권한은 합치지 않는다.
- [ ] 기존 계정·권한·endpoint/CA를 확인하고 수명/갱신·인계 방식의 구체적 승인을 받는다.
- [ ] 승인 후 안전한 발급·저장 인계, 필요한 조회 성공·불필요 권한 거부를 확인한다. 비밀값은 노트/로그/Git에 남기지 않는다.

## Task 7: S3 생성 주체·credential 방식 의사결정
**Files:** 선택 후 기존 소유 stack/chart/credential 주입 경로에서 확정.
**Interfaces:** <prefix><namespace>-<resource>, s3.credentialsSecret, 앱별 분리.
- [ ] 구현된 사실과 미정사항을 대조해 사용자에게 생성 주체·공급 방식의 구체적인 대안을 설명하고 결정을 받는다.
- [ ] 승인된 최소 구현/PR/권한/공급·사진 저장/조회·분리 검증으로 카드 조건을 충족한다.

## Task 8: digest 인계와 실제 AWS 인수
**Files:** 플랫폼 image values, helm/images.lock.json와 기존 CLI/test 절차. digest 작성자부터 합의한다.
**Interfaces:** C 이미지 인계→앞7단계+기존 Argo/NetworkPolicy 증거→실제 앱 등록·push·health 실패·두번째 앱.
- [ ] 승인된 릴리스와 인수 범위를 준비하고 실제 사용자 경로로 검증한다.
- [ ] 등록5분 이내200, push 새commit/revision, 실패버전에서도기존응답, 두번째앱infra변경없음의 각각 증거를 확인한다.
- [ ] 모든 카드 실제 완료·체크/Done과 최종 감사·Wiki/세션 인수를 끝낸 뒤에만 goal complete한다.
