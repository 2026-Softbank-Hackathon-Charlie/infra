# 0079. 플랫폼 API는 쓰기 기록 토큰을 SSM에서 선택적으로 읽는다

- 날짜: 2026-11-07
- 상태: 제안
- 관련 ADR: [0033](0033-platform-admin-accounts-from-ssm.md), [0035](0035-app-storage-and-secrets-infra.md). platform ADR 0041이 쓰기 기록 수집과 실행별 검증 입력을 다룬다.

## 배경과 제약

- 플랫폼 API는 `POST /v1/written-records/{stream}`을 받을 때 `PLATFORM_WRITTEN_RECORD_TOKENS`(쉼표로 구분한 `stream:token`, 토큰은 32자 이상)에 있는 스트림 토큰만 허용한다(platform `internal/auth/written.go`, `cmd/api/main.go`).
- 지금 차트의 init 컨테이너는 이 변수를 `api.env`에 쓰지 않는다. 그래서 앱이 기록을 보내도 모두 거절되고, 콘솔의 "앱 쓰기 기록 수집" 카드는 `app-moimroom-mig` 스트림 0건, "수집 기록 없음"으로 나온다(2026-11-07 `GET /v1/written-records/app-moimroom-mig`에서 확인).
- moimroom-mig 앱에는 기록을 보내는 reporter가 이미 있고 `WRITTEN_RECORDS_URL`, `WRITTEN_RECORDS_STREAM`, `WRITTEN_RECORDS_TOKEN`이 있을 때만 켜진다(moimroom-mig `internal/writtenrecords/reporter.go`, `internal/config/config.go`).
- 플랫폼 pod 역할은 이미 `/hopcharlie/*` 파라미터를 읽을 수 있다(`terraform/platform/aws/main.tf`). 새 IAM 권한은 필요 없다.
- 토큰 값은 Git, Terraform state, ADR에 남기지 않는다.

## 검토한 대안

- 대안 A: 토큰을 차트 values에 직접 쓴다. 비밀값이 Git에 남으므로 제외한다.
- 대안 B: Terraform이 `random_password`로 만들어 SSM에 쓴다. 값이 state에 남고, 스트림이 늘 때마다 Terraform apply가 필요하므로 지금은 제외한다.
- 대안 C: 사람이 `aws ssm put-parameter`로 SecureString `/hopcharlie/written-record-tokens`를 만들고, init 컨테이너가 있으면 읽는다. `app-secrets-key`와 같은 방식이고, 파라미터가 없을 때는 지금과 똑같이 동작한다. 선택한다.

## 결정과 이유

- init 컨테이너(`helm/charts/hopcharlie-platform/templates/settings.yaml`)가 `aws ssm get-parameters`로 `$PREFIX/written-record-tokens`를 읽는다. 값이 있으면 `api.env`에 `PLATFORM_WRITTEN_RECORD_TOKENS`로 쓰고, 없으면 쓰지 않는다.
- `get-parameters`를 쓰므로 파라미터가 없어도 init 컨테이너가 실패하지 않는다. 네트워크 오류는 지금처럼 멈춘다.

## 영향과 후속 작업

- 기대 효과: 앱의 커밋 기록이 플랫폼에 쌓여 이전 검증에서 쓰기 누락을 실제 데이터로 계산할 수 있다.
- 단점과 위험: 토큰을 바꾸면 API pod를 다시 띄워야 반영된다. 파라미터를 지우면 다음 재시작부터 모든 기록이 거절된다.
- 필요한 후속 작업 및 검증 (모두 승인이 필요한 작업이다):
  1. 32자 이상 토큰을 만들어 SecureString `/hopcharlie/written-record-tokens`에 `app-moimroom-mig:<토큰>`으로 저장한다.
  2. 이 변경이 배포된 뒤 플랫폼 API pod를 다시 띄워 `api.env`를 새로 만든다.
  3. 콘솔의 moimroom-mig 시크릿에 `WRITTEN_RECORDS_URL`(`https://api.hopcharlie.dev`), `WRITTEN_RECORDS_STREAM`(`app-moimroom-mig`), `WRITTEN_RECORDS_TOKEN`(1의 토큰)을 넣고 빌드 없이 다시 배포한다.
  4. 예약을 하나 만든 뒤 `GET /v1/written-records/app-moimroom-mig`의 `count`가 늘어나는지 확인한다.

## 관련 자료

- [settings.yaml](../../helm/charts/hopcharlie-platform/templates/settings.yaml)
- [terraform/platform/aws/README.md](../../terraform/platform/aws/README.md)의 파라미터 표
