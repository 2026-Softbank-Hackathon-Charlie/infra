# hopcharlie-app

HopCharlie 플랫폼이 GitHub에서 배포하는 앱 하나를 그리는 차트다(platform ADR 0011). Argo CD의 ApplicationSet(`helm/charts/hopcharlie-apps`)이 gitops 파일 `apps/<instance>/<env>.yaml`마다 이 차트로 Application `<namespace>-<env>`를 만든다.

## 값

- **gitops 파일 그대로**: Argo CD가 파일을 values 파일로 넘긴다. 차트는 `values.app`, `values.services[]`, `values.resources[]`, `values.sealedSecrets`를 그리고, `commit`과 `deployment`는 Deployment annotation에만 쓴다. `values.schema.json`이 platform 저장소의 `pkg/appspec`과 같은 규칙과 모르는 키를 거부한다. 이미지는 digest로 고정해야 한다.
- **`profile`**: 환경 프로필. ApplicationSet이 환경마다 `valuesObject`로 더한다. 항목과 기본값은 `values.yaml`에 있다.

## 그리는 것

| 객체 | 이름 | 조건 |
| --- | --- | --- |
| Namespace | 릴리스 네임스페이스(파일의 `namespace`) | 항상. Pod Security 라벨(`profile.podSecurity`), `hopcharlie.dev/app`, `hopcharlie.dev/instance`, sync-wave -1 |
| Deployment, Service | `<app>-<service>` | 서비스마다. 공개 서비스만 `profile.publicService`의 type, port(0이면 서비스의 포트), annotation을 씀. `values.sealedSecrets.app`이 있으면 모든 컨테이너가 `envFrom`으로 Secret `app-secrets`를 받고, 파드 annotation `hopcharlie.dev/secrets-checksum`에 `appChecksum`을 쓴다 |
| CNPG Cluster | `<app>-<resource>` | `postgres` 리소스. `DATABASE_URL`은 secret `<app>-<resource>-app`의 `uri` |
| (없음) | | `s3` 리소스는 `S3_*` 변수를 넣는다. 버킷은 `<bucketPrefix><namespace>`이고 플랫폼이 만든다(platform ADR 0016). 키는 Secret `profile.s3.credentialsSecret`에서 읽는다 |
| SealedSecret | `app-s3` | `values.sealedSecrets.s3`. 버킷 키(`accessKey`, `secretKey`). 이때 `profile.s3`는 Secret `app-s3`와 기본 키 이름을 읽어야 한다 |
| SealedSecret | `app-secrets` | `values.sealedSecrets.app`. 앱 시크릿(platform ADR 0019) |
| Ingress | `<app>-<service>` | 공개 서비스, `profile.ingress.enabled`. 클래스 `profile.ingress.className`, 호스트 `<namespace>.<hostSuffix>`(eks: `<instance>.eks.hopcharlie.internal`), 경로 `/`에서 공개 Service의 `http` 포트로. 클러스터의 공용 진입점이 받는다(infra ADR 0058) |
| ResourceQuota, LimitRange, NetworkPolicy | `hopcharlie-app` | `profile.quota`, `limitRange`, `networkPolicy`의 `enabled` |

SealedSecret은 대상 클러스터의 Sealed Secrets 공개 인증서로 strict 범위로 봉인한 값이다. 이름과 네임스페이스가 다르면 열리지 않는다. sync-wave -1이라 Argo CD는 컨트롤러가 Secret을 만든 뒤에 Deployment를 바꾼다. Secret은 컨트롤러가 만들고 SealedSecret이 소유하므로 Argo CD가 지우지 않는다. 같은 이름이면 `env`가 `envFrom`보다 이긴다. 플랫폼이 시크릿과 같은 이름을 `env`에서 빼고 gitops 파일을 쓰며, 남아 있으면 차트가 렌더링을 실패시킨다.

새 버전이 준비되지 않아도 옛 파드가 계속 받는다. Deployment는 `maxUnavailable: 0`, `maxSurge: 1`이고, `progressDeadlineSeconds`가 지나면 Argo CD가 `Degraded`로 보여 준다.

## 앱을 지우면 네임스페이스째 지워진다

네임스페이스가 Application의 객체이므로, gitops 파일이 지워져 Application이 지워지면 Argo CD가 네임스페이스와 그 안의 모든 것을 지운다. CNPG의 데이터(PVC)도, 누가 넣어 둔 Secret(예: S3 키)도 함께 사라지고 되돌릴 수 없다. platform ADR 0011의 "앱을 지우면 DB의 데이터도 함께 사라진다"와 같다. 이미 있던 네임스페이스에 배포하면 Argo CD가 그 네임스페이스를 가져가므로, 지울 때 그 안의 것도 함께 지워진다. AppProject가 시스템과 플랫폼 네임스페이스를 막으므로 앱이 `kube-system`, `argocd` 같은 네임스페이스를 가져갈 수는 없다.

## 테스트

이 저장소의 `scripts/check-helm.py`가 `ci/`의 두 값으로 lint와 렌더링을 하고, 그린 객체가 모두 `hopcharlie-apps` AppProject에서 허용되는지 확인한다. SealedSecret, `envFrom`, checksum annotation, 버킷 이름을 확인하고, `appChecksum` 없는 `app`, 예약된 이름, base64가 아닌 암호문, 다른 Secret을 읽는 프로필, 시크릿과 같은 이름의 `env`는 거부되는지 확인한다. 전체 흐름(등록, 빌드, gitops 커밋, Argo CD sync)은 platform 저장소의 `test/e2e/deploy/run.sh`가 kind의 Argo CD로 검사한다. 이 차트는 platform 저장소 `deploy/charts/hopcharlie-app`(platform `2aea035`)에서 옮겨 왔다.
