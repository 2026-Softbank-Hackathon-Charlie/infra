# 0022. 앱 빌드를 전용 CodeBuild 프로젝트와 제한된 IAM 역할로 실행한다

- 날짜: 2026-11-03
- 상태: 제안
- 관련 ADR: [0015](0015-centralize-platform-terraform-ownership.md), [0016](0016-run-hopcharlie-platform-on-management-cluster.md)

## 배경과 제약

플랫폼이 앱 이미지를 빌드해 ECR에 올리려면 기존 Pod Identity와 연결된 빌드 실행 권한이 필요하다. 기존 backend와 리소스 주소는 보존한다. 실제 계정의 동명 리소스, quota, IAM/SCP, state 소유권은 확인하지 않았다. 이 제안과 로컬 테스트는 클라우드 적용 승인이 아니다.

## 검토한 대안

- 기존 플랫폼 역할에 ECR 쓰기 권한을 직접 부여: 빌드 실행과 레지스트리 권한이 결합되므로 제외한다.
- 전용 CodeBuild 역할과 프로젝트를 사용: 플랫폼에는 프로젝트 시작·상태와 로그 읽기만, 빌드 역할에는 앱 저장소 쓰기만 부여해 선택한다.

## 결정과 이유

`hopcharlie-app-build` 프로젝트는 NO_SOURCE/NO_ARTIFACTS와 standard:7.0 SMALL privileged 환경을 사용한다. 플랫폼은 빌드별 buildspec과 GitHub token을 전달한다. 프로젝트 기본 buildspec은 override 없이 실패한다. 프로젝트에는 token을 저장하지 않는다. 빌드 및 대기 timeout은 각각 15분, 동시 빌드는 1개이며 전용 로그의 보존 기간은 3일이다.

CodeBuild trust는 SourceAccount와 특정 프로젝트 ARN으로 제한한다. ECR 인증 토큰만 전체 리소스이고 저장소 관련 동작은 `hopcharlie/apps/*`에 제한한다. 플랫폼 역할은 해당 프로젝트의 StartBuild/BatchGetBuilds 및 전용 로그 스트림의 GetLogEvents만 추가한다. 빌드 역할 로그 쓰기도 해당 스트림에 한정한다. provider는 로그 그룹 ARN의 `:*` 접미사를 제거하므로 스트림 범위에 접미사를 명시한다.

## 영향과 후속 작업

- 기존 리소스의 정의·주소를 옮기거나 교체하도록 변경하지 않는다. 실제 state와 plan을 조회하지 않았으므로 기존 리소스의 무변경을 보장할 수 없다.
- privileged 빌드와 buildspec override는 해당 빌드 역할의 권한으로 코드를 실행한다. 플랫폼의 입력 검증 및 token 전달·폐기는 별도 구현과 검증이 필요하다.
- 실제 계정·동명 리소스·quota·IAM/SCP·state 소유권 조회, 실제 plan 및 apply는 각각 구체적인 승인 이후 진행한다. 플랫폼 역할로 빌드·로그 조회와 ECR 이미지 push를 확인해야 완료다.

## 관련 자료

- [구현](../../terraform/platform/aws/app-build.tf), [명세](../plans/2026-11-03-app-deploy-infra-spec.md), [계획](../plans/2026-11-03-app-deploy-infra.md)
- [AWS CodeBuild 권한 참조](https://docs.aws.amazon.com/service-authorization/latest/reference/list_codebuild.html)
- [Terraform 로그 그룹 ARN](https://registry.terraform.io/providers/hashicorp/aws/latest/docs/resources/cloudwatch_log_group)
