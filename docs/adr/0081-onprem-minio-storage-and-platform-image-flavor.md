# 0081. onprem 앱 저장소를 노트북 MinIO로 두고 관리 플랫폼에 MinIO 이미지를 고정

- 날짜: 2026-11-07
- 상태: 제안
- 관련 ADR: [0072](0072-add-onprem-as-paired-database-environment.md), [0078](0078-manage-three-member-db-paths-with-gitops.md), platform ADR 0016·0045

## 배경과 제약

`moimroom-mig`를 GKE에서 onprem(노트북 WSL2 k3s)으로 옮길 때 사진도 옮겨야 한다. onprem 프로필의 `s3`는 그동안 AWS S3(`hopcharlie-eks-`)를 가리켰고, `host/envs.yaml`의 onprem에는 `storage`가 없어 플랫폼이 onprem 버킷·키를 만들지 않았다.

- 노트북 k3s의 `minio` 네임스페이스에 MinIO(ClusterIP `minio:9000`)가 설치돼 있다.
- 관리 EKS에서는 Tailscale egress Service `hopcharlie-db-network/minio-onprem`(포트 80 → 노트북 9000)로 닿는다. 플랫폼 API egress NetworkPolicy가 허용하는 80 포트를 쓴다.
- platform main(#121, ADR 0045)은 `PLATFORM_APP_FILES=1`일 때 MinIO 환경에도 rclone 원격을 만든다. 이때 `adminAccessKeyEnv`·`adminSecretKeyEnv`가 가리키는 환경변수가 비어 있으면 API가 시작하지 못한다.
- `storage.driver: minio`는 `-tags minio`로 빌드한 이미지(`<sha7>-minio`)에서만 허용된다. 기본 이미지에 이 블록을 주면 `PLATFORM_ENVS_FILE` 검증에서 API가 종료된다(이전 CrashLoop/503 사례와 같다).

## 검토한 대안

- 사진만 수동 `rclone copy`: 계획·검증 경로에서 파일이 빠지므로 제외했다.
- 기본 이미지에 MinIO 상시 포함: platform ADR 0016·0045가 거절한 선택이라 제외했다.
- 앱 pod도 tailnet 주소로 MinIO에 접근: 앱 pod와 MinIO가 같은 k3s에 있으므로 불필요하다. 엔드포인트는 둘로 나눈다.

## 결정과 이유

1. 관리 플랫폼 `images.platform`을 수동 발행한 `<sha7>-minio` digest로 고정한다. 자동 핀은 flavor가 다르면 건너뛴다(platform ADR 0045).
2. `host/envs.yaml` onprem에 `storage`를 둔다: `driver: minio`, `bucketPrefix: hopcharlie-op-`, `region: us-east-1`, `endpoint: http://minio-onprem.hopcharlie-db-network.svc.cluster.local`, 키 환경변수 `ONPREM_MINIO_ADMIN_ACCESS_KEY`·`ONPREM_MINIO_ADMIN_SECRET_KEY`.
3. 키 값은 SSM SecureString `/hopcharlie/rclone-env`에 같은 이름의 줄로 넣는다. API 컨테이너가 이 파일을 export한다. Git에는 이름만 둔다.
4. onprem 앱 프로필 `s3`는 k3s 내부 `http://minio.minio.svc.cluster.local:9000`, `pathStyle: true`, 같은 `bucketPrefix`를 쓴다. 앱 NetworkPolicy `extraEgress`에 `minio` 네임스페이스 `app=minio` TCP 9000을 연다.

적용 순서는 고정한다: SSM 키 → MinIO 이미지 핀이 Argo로 배포돼 API가 그 이미지로 실행 중인지 확인 → `terraform/platform/aws` apply(사용자)로 `host/envs.yaml` 반영 → API 재시작. 핀보다 envs.yaml이 먼저 반영되면 API가 멈춘다.

## 영향과 후속 작업

- 기대 효과: onprem 앱이 버킷·키를 받고, GKE→onprem 이전 계획에 사진이 포함된다.
- 단점과 위험:
  - MinIO 이미지는 자동 갱신되지 않는다. 플랫폼 보안 패치마다 MinIO flavor를 수동 발행하고 이 핀을 바꿔야 한다.
  - 플랫폼은 MinIO root 키를 쓴다. 앱 버킷 하나로 제한된 복사 계정은 아니다.
  - 노트북 절전·재시작 시 onprem 저장소 연결이 끊긴다.
  - AGPL-3.0 MinIO 관리 클라이언트를 포함한 이미지를 운영에서 쓴다. 팀 합의가 필요하다(platform ADR 0045).
- 필요한 후속 작업 및 검증:
  - k3s `minio` 네임스페이스는 `minio-deny-ingress`로 앱 네임스페이스를 막고 있다. 앱 pod가 9000에 닿으려면 k3s에 허용 NetworkPolicy가 별도로 필요하다(GitOps 밖, 승인 대상).
  - apply 뒤 `charlie env show onprem`의 storage 줄, API `/healthz`, onprem 등록 앱의 버킷 생성을 확인한다.
  - 롤백: envs.yaml의 storage 블록을 먼저 되돌려 apply한 뒤 이미지 핀을 기본 flavor로 되돌린다(역순).

## 관련 자료

- [terraform/platform/aws/host/envs.yaml](../../terraform/platform/aws/host/envs.yaml)
- [clusters/aws-dev-management/values/hopcharlie-apps.yaml](../../clusters/aws-dev-management/values/hopcharlie-apps.yaml)
- [clusters/aws-dev-management/values/hopcharlie-platform.yaml](../../clusters/aws-dev-management/values/hopcharlie-platform.yaml)
- [helm/charts/hopcharlie-platform/templates/settings.yaml](../../helm/charts/hopcharlie-platform/templates/settings.yaml) (`rclone-env` → `/config/rclone.env`)
- [scripts/check-helm.py](../../scripts/check-helm.py) (onprem 프로필 렌더 검사)
