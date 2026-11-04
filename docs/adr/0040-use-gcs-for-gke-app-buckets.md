# 0040. GKE 앱 파일 저장소는 GCS의 S3 호환 API를 쓴다

- 날짜: 2026-11-04
- 상태: 제안
- 관련 ADR: [ADR 0035](0035-app-storage-and-secrets-infra.md), [ADR 0039](0039-add-gke-app-environment.md)(파일 저장소 부분을 대체), platform 저장소 ADR 0016(앱 오브젝트 스토리지)

## 배경과 제약

[ADR 0039](0039-add-gke-app-environment.md)는 GKE 환경 `gke`에 파일 저장소(`profile.s3`)를 넣지 않았다. 그래서 s3 리소스가 있는 앱(moimroom의 `photos`)은 GKE에서 렌더링에 실패한다. HopCharlie는 앱을 EKS에서 GKE로 옮기는 것을 보여주므로, 파일도 GCP에 있어야 이전이 성립한다.

- 확인한 사실:
  - platform ADR 0016은 모든 환경에서 앱이 같은 `S3_*` 변수와 정적 키로 S3 호환 API를 쓰도록 정했다. GKE용 저장소 드라이버는 `gcs`다. 인스턴스마다 서비스 계정을 만들고, 그 버킷에 `roles/storage.objectAdmin`을 주고, HMAC 키를 만든다. `gcs` 드라이버는 아직 구현되지 않았다.
  - 플랫폼은 환경 설정(envs.yaml)에 `storage`가 있는 환경에만 s3 앱을 등록한다. 지금 `gke`에는 `storage`와 `sealedSecretsCert`가 없다.
  - moimroom은 minio-go로 `S3_ENDPOINT`, `S3_REGION`, `S3_PATH_STYLE`을 읽는다.
  - 저장소에는 GKE용 sealed-secrets 설정이 없다(`clusters/gcp-dev-workload/values`에 없음).
- 미확인:
  - minio-go와 GCS HMAC 조합으로 moimroom의 업로드와 다운로드가 실제로 되는지
  - GKE에 sealed-secrets 컨트롤러가 떠 있는지
  - GKE 노드가 ECR 이미지를 받을 수 있는지. 이 ADR 범위 밖이며 따로 정한다.

## 검토한 대안

- GKE 앱도 AWS S3를 쓰기: infra 변경이 가장 작다. 하지만 파일이 AWS에 남아 이전이 반쪽이 되고, 클라우드 간 호출로 지연과 전송 비용이 생긴다.
- GKE 앱에 GCS 네이티브 API와 Workload Identity 쓰기: 정적 키가 없어진다. 하지만 앱 계약(`S3_*`)이 바뀌고 moimroom 코드도 바뀌어야 한다. platform ADR 0016이 이미 거절했다.
- GCS의 S3 호환 API와 HMAC 키(선택): 앱과 차트 계약이 그대로이고 platform ADR 0016과 같다.

## 결정과 이유

[hopcharlie-apps.yaml](../../clusters/aws-dev-management/values/hopcharlie-apps.yaml)의 `gke` 프로필에 `s3`를 더한다.

- `endpoint`: `https://storage.googleapis.com`(GCS XML API, S3 호환)
- `region`: `auto`(GCS가 S3 호환 클라이언트에 권장하는 값)
- `bucketPrefix`: `hopcharlie-gke-`(15자, platform ADR 0016 상한 이하). 버킷 이름은 `hopcharlie-gke-<instance>`이고 envs.yaml `gke.storage.bucketPrefix`와 같아야 한다.
- `credentialsSecret`: `app-s3`. 키는 플랫폼이 GKE 클러스터 인증서로 봉인해 gitops 파일에 넣고, 차트가 SealedSecret으로 그린다([ADR 0035](0035-app-storage-and-secrets-infra.md)와 같다).
- `pathStyle`: 기본값 `false`(가상 호스트 방식)
- NetworkPolicy는 바꾸지 않는다. 앱의 바깥 TCP 443은 이미 허용되어 있다.

이 변경만으로 배포되는 앱은 없다. 플랫폼이 `gke`에 `storage`를 설정하기 전에는 s3 앱을 GKE에 등록하지 않는다. 그래서 먼저 병합해도 안전하다.

## 영향과 후속 작업

- 기대 효과: 플랫폼의 `gcs` 드라이버와 GKE 인증서가 준비되면 moimroom 같은 s3 앱을 GKE에 배포할 수 있다. 차트와 앱 코드는 바뀌지 않는다.
- 단점과 위험:
  - 정적 HMAC 키를 쓴다. 키마다 버킷 하나로 범위가 좁고 인스턴스와 함께 지워진다(platform ADR 0016).
  - GCS 버킷 이름은 전 세계에서 하나뿐이다. 다른 프로젝트가 같은 이름을 먼저 쓰면 플랫폼의 버킷 생성이 실패한다.
  - 버킷 위치는 플랫폼이 정한다. 레지던시를 지키려면 단일 리전 `asia-northeast3`이어야 한다(platform ADR 0021).
- 필요한 후속 작업(각각 별도 승인):
  1. GKE에 sealed-secrets 컨트롤러 설치(chart 2.20.0, `kube-system`, `keyrenewperiod: "0"`)와 공개 인증서를 SSM `/hopcharlie/sealed-secrets/gke`에 저장
  2. GCP 플랫폼 서비스 계정: 프로젝트 안 `hopcharlie-gke-*` 버킷, 앱 서비스 계정, HMAC 키 관리 권한. 인증 방식(SSM 키 대신 AWS 플랫폼 역할에서의 Workload Identity 연동)도 함께 정한다.
  3. platform의 `gcs` 드라이버 구현(플랫폼 담당)
  4. envs.yaml `gke`에 `sealedSecretsCert`와 `storage`(`driver: gcs`, `bucketPrefix: hopcharlie-gke-`) 추가. 3번이 배포된 뒤에 한다.
  5. 시험 버킷과 HMAC 키로 minio-go 업로드와 다운로드 확인
  6. GKE 노드의 ECR 이미지 pull 방법 결정(이 ADR 범위 밖)

## 관련 자료

- [clusters/aws-dev-management/values/hopcharlie-apps.yaml](../../clusters/aws-dev-management/values/hopcharlie-apps.yaml)
- [scripts/check-helm.py](../../scripts/check-helm.py)
- GCS S3 호환(XML API, HMAC): https://cloud.google.com/storage/docs/interoperability
- platform 저장소 `docs/adr/0016-app-object-storage.md`, `docs/adr/0021-bucket-residency-measured.md`
