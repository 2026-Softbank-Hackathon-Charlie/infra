# 0079. 성공한 쓰기 기록의 전용 인증정보를 SSM에서 API에 전달한다

- 상태: 제안
- 날짜: 2026-11-07

## 상황

플랫폼 API는 `PLATFORM_WRITTEN_RECORD_TOKENS`의 `stream:token` 목록으로
성공한 쓰기 기록의 append 요청을 인증한다. 이 인증은 일반 operator·agent
인증과 별개다. 기존 운영 settings 스크립트에는 해당 값을 전달하는 경로가 없어,
앱 수집기 설정만 추가해도 전용 인증을 활성화할 수 없었다.

기록이 없는 이전 실행의 쓰기 누락은 미측정이며, 실패 0건으로 바꿀 수 없다.
이 변경은 인증정보 전달 경로를 보완하며 앱 수집기의 활성화와 실제 기록 수집은
별도 검증 대상이다.

## 결정

`fetch-settings.sh`의 API 분기에서 `$PREFIX/written-record-tokens`를
복호화해 읽고, 값이 있으면 메모리 볼륨의 `/config/api.env`에
`PLATFORM_WRITTEN_RECORD_TOKENS`로 전달한다. 기본 prefix에서는
`/hopcharlie/written-record-tokens`다.

- 기존 선택적 `app-secrets-key`와 같은 `ssm get-parameters` 방식을 쓴다.
- 파라미터가 없거나 비어 있으면 환경변수를 쓰지 않아 기존 동작을 유지한다.
- 접근 거부·네트워크 오류는 init container 실패로 처리한다. 인증을 조용히 끄지 않는다.
- 값은 ConfigMap·Helm values·Git·로그에 넣지 않는다. `api.env`가 shell에서
  source되므로 새 값은 안전하게 인용해 셸문자를 실행하지 않는다.
- 일반 `PLATFORM_TOKENS`와 합치지 않는다. 앱에는 자기 stream의 append만
  가능한 별도 토큰을 사용한다.

## 검토한 대안

- 일반 operator 토큰을 앱에 주입: 앱에 이전 승인·실행 권한까지 주므로 제외한다.
- Helm values에 토큰을 저장: Git과 Argo 렌더 결과에 비밀값이 남으므로 제외한다.
- 파라미터를 필수로 만듦: 아직 수집기를 사용하지 않는 운영 환경의 API 시작을
  막으므로 제외한다.

## 결과와 운영 활성화 조건

로컬 검증은 렌더한 실제 settings 스크립트를 테스트용 AWS 응답으로 실행한다.
미설정·정상 전달·접근 거부·셸문자 인용과 비노출을 확인한다. 실제 SSM·인증·
클라우드 이전 성공의 증거는 아니다.

이 변경은 파라미터나 토큰을 만들지 않고 IAM·앱 설정을 변경하지 않는다.
운영 활성화에는 다음 작업의 별도 승인과 확인이 필요하다.

1. stream마다 예측 불가능한 32자 이상의 토큰을 준비해 SSM SecureString에
   `app-<app>:<token>` 형식으로 저장한다. 여러 stream은 쉼표로 구분한다.
2. 기존 API 역할이 그 prefix에 대한 `GetParameters`·복호화 권한을 갖는지
   확인하고, 이전 실행이 없을 때 API 설정을 갱신해 재시작한다. SSM 값을
   바꾸는 것만으로 이미 실행 중인 API의 환경변수는 바뀌지 않는다.
3. 앱 시크릿에 `WRITTEN_RECORDS_URL`(API origin),
   `WRITTEN_RECORDS_STREAM`(`app-<app>`), `WRITTEN_RECORDS_TOKEN`(같은 토큰)을
   넣고 안전한 시점에 앱을 재배포한다.
4. 실제 앱 쓰기를 수행하고 중앙 기록 수 증가, `application_commits` scope,
   DB generation 일치, 새 이전 계획의 기록 연결을 확인한다.

기존 미측정 실행은 수집이 시작돼도 소급해 측정 완료로 바꾸지 않는다.

## 관련 파일

- [settings.yaml](../../helm/charts/hopcharlie-platform/templates/settings.yaml)
- [test-platform-written-tokens.py](../../scripts/tests/test-platform-written-tokens.py)
- platform `internal/auth/written.go`, `cmd/api/main.go`, ADR 0041
