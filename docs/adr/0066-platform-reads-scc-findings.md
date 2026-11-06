# 0066. 플랫폼 API가 SCC Standard 결과를 읽기 전용으로 읽는다

- 날짜: 2026-11-06
- 상태: 채택
- 관련 ADR: [0041](0041-keyless-gcp-access-for-platform-app-storage.md), [0065](0065-image-scanner-reads-artifact-registry.md)

## 배경과 제약

platform 보안 화면의 "GCP 프로젝트" 행(SEC-03)은 Security Command Center(SCC)가 연결되지 않아 "미연동"이다. AWS 계정 행은 Security Hub와 GuardDuty를 이미 읽는다. 프로젝트의 SCC API는 꺼져 있었다(`SERVICE_DISABLED`). 프로젝트는 조직 아래에 있고, 작업 계정은 프로젝트 owner이지만 조직 권한은 없다.

## 검토한 대안

- 조직 단위 SCC 활성화: 조직 권한이 없어 제외했다.
- Premium 등급: 사용량 과금이라 해커톤 범위에 과해 제외했다.
- 보안 전용 서비스 계정을 새로 만들기: 플랫폼 pod에 두 번째 GCP 자격 구성이 필요해 제외했다. 읽기 전용 역할 하나만 더하므로 기존 계정을 쓴다.

## 결정과 이유

- SCC Standard(무료)를 프로젝트 단위로 켠다. Terraform에 활성화 리소스가 없으므로 콘솔에서 한 번 켠다.
- `terraform/integrations/gcp/platform-storage`에 `securitycenter.googleapis.com` 활성화(삭제해도 끄지 않음)와 플랫폼 계정의 `roles/securitycenter.findingsViewer`를 더한다. 상태 변경·mute·설정 권한은 주지 않는다.
- 다른 루트와의 충돌: bootstrap/gcp의 API 목록과 tailscale의 `iap`에는 SCC가 없다. IAM은 모두 비권한형(`iam_member`)이다. 실제 state 대상 plan은 생성 2·변경 0·삭제 0이었다.

## 영향과 후속 작업

- 기대 효과: 보안 화면에서 AWS와 GCP의 계정 단위 결과를 나란히 볼 수 있다.
- 단점과 위험: 스토리지용 계정이 SCC 결과도 읽는다. 프로젝트 단위 활성화는 그 프로젝트만 본다.
- 후속: platform의 gcp-scc 수집기 구현(SEC-03).

## 관련 자료

- [security-findings.tf](../../terraform/integrations/gcp/platform-storage/security-findings.tf)
- [Activate Security Command Center for a project](https://docs.cloud.google.com/security-command-center/docs/activate-scc-for-a-project)
