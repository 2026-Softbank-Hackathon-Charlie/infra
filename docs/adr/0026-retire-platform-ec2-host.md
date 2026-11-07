# 0026. 플랫폼 EC2 관리 호스트를 철거한다

- 날짜: 2026-11-03
- 상태: 제안
- 2026-11-07 갱신([ADR 0075](0075-remove-moimroom-static-catalog-and-image-publishing.md)): `host/apps.yaml`과 SSM `/hopcharlie/host/apps.yaml`을 지웠다. 플랫폼 차트는 이제 `envs.yaml`만 받는다.
- 관련 ADR: [ADR 0015](0015-centralize-platform-terraform-ownership.md), [ADR 0016](0016-run-hopcharlie-platform-on-management-cluster.md)

## 배경과 제약

- 플랫폼(API, 게이트웨이, 콘솔, PostgreSQL)은 처음에 EC2 한 대(`hopcharlie-management-host`, t3.small)에서 Docker Compose로 돌았다. ADR 0016에 따라 관리 EKS로 옮기면서, 전환을 확인할 때까지 호스트를 남겨 두었다.
- 2026-11-03에 Argo CD가 `charlie-management-hopcharlie-platform`을 넘겨받고 자동 sync를 켰다. `api.hopcharlie.dev`, `console.hopcharlie.dev`는 EKS에서 응답한다.
- 호스트의 하루 수신 트래픽은 몇 MB(SSM 에이전트 수준)이고, 호스트 주소(`*.3-36-23-54.sslip.io`)는 저장소의 문서 예시에만 남아 있다.
- 계정의 온디맨드 vCPU 할당량 16개 중 14개를 쓰고 있다. 호스트가 그중 2개를 쓴다.
- 같은 Terraform root(`terraform/platform/aws`)에 EKS 플랫폼이 쓰는 ECR, 생성한 비밀값과 토큰, `host/envs.yaml`, `host/apps.yaml`, pod 역할이 함께 있다. root 전체를 destroy하면 운영 플랫폼이 깨진다.

## 검토한 대안

- 그대로 두기: 비용은 하루 1달러 미만이지만 vCPU 2개를 계속 쓰고, 옛 DB를 가진 두 번째 플랫폼이 공개 주소로 떠 있어 혼동을 준다.
- 인스턴스만 콘솔에서 지우기: Terraform 설정이 남아 있어 다음 apply가 호스트를 다시 만든다.
- `-target`으로 호스트 리소스만 destroy하기: 같은 문제로 설정과 state가 어긋난다.
- 변수로 호스트를 끄기(`count`): 다시 켤 계획이 없는데 쓰지 않는 코드와 변수가 남는다.

## 결정과 이유

- Terraform 설정에서 호스트 전용 리소스를 지우고, plan과 apply로 실제 리소스를 지운다. 대상은 EC2 인스턴스, EIP와 그 연결, 보안 그룹, 호스트 IAM 역할과 정책 4개, instance profile, 호스트만 읽던 SSM 파라미터 5개(`host/Caddyfile`, `host/compose.yaml`, `host/compose.tailscale.yaml`, `host/settings.env`, `host/sync.sh`)다.
- EKS 플랫폼이 쓰는 리소스는 Terraform 주소와 값을 바꾸지 않는다. 2026-11-03 plan은 15개 삭제, 추가와 변경 0개였다.
- 호스트 전용 변수(`instance_type`, `image_tag`, `console_image_tag`, `gateway_backends`, `cors_origins`, `dsn_host_suffixes`)와 출력(`api_url`, `app_url`, `console_url`, `instance_id`, `shell`), `user_data.sh.tftpl`, 호스트 설정 파일을 지운다.
- 호스트 시절의 SSM 이름(`/hopcharlie/host/envs.yaml`, `apps.yaml`)은 플랫폼 차트가 읽고 있어서 그대로 둔다.

## 영향과 후속 작업

- 기대 효과: vCPU 2개와 EIP 하나를 돌려받고, 플랫폼이 하나만 남는다.
- 단점과 위험:
  - 호스트의 PostgreSQL 데이터(EBS)도 함께 지워진다. EKS로 옮길 때 데이터를 옮기지 않았고, 호스트 DB에는 시험 데이터만 있다고 가정한다.
  - platform 저장소의 `deploy/aws/redeploy.sh`, `push-console.sh`처럼 이 root의 호스트 출력을 읽는 도우미는 더 이상 동작하지 않는다.
- 필요한 후속 작업 및 검증:
  - apply 뒤 `aws ec2 describe-instances`에서 호스트가 terminated인지, EKS 플랫폼 앱이 계속 `Synced`, `Healthy`인지 확인한다.
  - platform 저장소에서 EC2 전용 도우미와 문서의 sslip 주소를 정리한다.

## 관련 자료

- [terraform/platform/aws/main.tf](../../terraform/platform/aws/main.tf), [outputs.tf](../../terraform/platform/aws/outputs.tf), [variables.tf](../../terraform/platform/aws/variables.tf)
- [docs/plans/argocd-root-bootstrap.md](../plans/argocd-root-bootstrap.md)의 플랫폼 앱 인수 결과
