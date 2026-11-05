# 0049. 플랫폼 GCS 드라이버의 프로젝트 버킷 목록 조회를 허용한다

- 날짜: 2026-11-05
- 상태: 제안
- 관련 ADR: [ADR 0041](0041-keyless-gcp-access-for-platform-app-storage.md)

## 배경과 제약

platform PR #63의 GCS 드라이버는 버킷의 라벨뿐 아니라 소속 프로젝트도 확인한다. `internal/storage/gcs.go`의 `bucket()`은 프로젝트와 이름 접두사를 지정해 버킷 목록을 조회한다. 기존 역할에는 `storage.buckets.list`가 없어 WIF 인증을 연결해도 이 호출이 거부된다. 목록 조회는 프로젝트 권한이므로 기존 버킷 이름 조건 역할로 해결할 수 없다.

## 검토한 대안

- 플랫폼에 Storage Admin 부여: 파일 읽기·쓰기와 기존 버킷 변경까지 넓어져 제외한다.
- 드라이버가 버킷 단건 조회로 프로젝트를 확인하도록 변경: 프로젝트 번호 조회 또는 설정 계약 변경이 필요하므로 후속 대안으로 남긴다.
- 기존 프로젝트 역할에 `storage.buckets.list` 하나 추가: 병합된 드라이버를 그대로 쓰면서 누락 권한만 보완한다.

## 결정과 이유

기존 `charliePlatformAppIdentities` 역할에 `storage.buckets.list`만 추가하는 안을 제안한다. 새 역할·주체·신뢰 관계를 만들지 않는다. 버킷 생성·수정·삭제와 객체 목록·삭제는 기존 `hopcharlie-gke-*` 조건을 유지한다. 플랫폼에 객체 내용 읽기·쓰기 권한은 추가하지 않는다.

기존 차트의 공개 ADC 설정과 projected token으로 관리 EKS API를 기존 플랫폼 GSA에 연결한다. GKE 환경에는 기존 공개 봉인 인증서와 서울 GCS 저장소 설정을 활성화한다. SSM 환경 파일은 기존 다른 환경을 보존해 별도로 갱신해야 한다.

## 영향과 후속 작업

- 플랫폼은 프로젝트의 다른 버킷 이름·메타데이터도 조회할 수 있다. API의 `prefix` 필터는 IAM 제한이 아니다. 다른 버킷의 파일 읽기·변경 권한은 추가하지 않는다.
- 권한 확대·SSM 갱신·API 재시작과 실제 시험은 별도 실행 승인을 받은 뒤 수행한다. Terraform 대상 계획이 기존 프로젝트 역할 1개 수정과 다르면 중단한다.
- 현재 유효한 `iam.disableServiceAccountKeyCreation` 정책이 새 HMAC 발급을 막는다. 정책 예외는 별도 관리자 조치가 필요하며, 이 변경으로 조직 정책을 수정하지 않는다. 제한을 복구하면 기존 키는 유지되지만 새 키가 필요한 앱 배포·교체는 다시 막힌다.
- 기존 플랫폼 드라이버로 고유 시험 버킷·계정·HMAC을 생성하고 재실행을 확인한다. 기존 GKE 공개 인증서로 키를 봉인해 임시 모임룸 Pod에서 사진 1개 업로드·다운로드와 해시 일치를 검사한다. 시험 사진 행과 이번 Pod·봉인 Secret·버킷·계정·키만 정리한다. 기존 앱·DB의 나머지 데이터와 Gateway는 보존한다.
- 실제 배포·토큰 교환·파일 입출력은 아직 미검증이다. 완료 후 검증 결과와 남은 제약만 짧게 기록한다.

## 관련 자료

- [플랫폼 PR #63](https://github.com/2026-Softbank-Hackathon-Charlie/platform/pull/63)
- [플랫폼 GCP 역할](../../terraform/integrations/gcp/platform-storage/main.tf)
- [플랫폼 배포 값](../../clusters/aws-dev-management/values/hopcharlie-platform.yaml)
- [환경 설정 템플릿](../../terraform/platform/aws/host/envs.yaml)
- [Google Cloud Storage 버킷 목록 API](https://docs.cloud.google.com/storage/docs/json_api/v1/buckets/list)
