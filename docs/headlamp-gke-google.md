# 한 화면에서 EKS 두 개와 GKE 사용하기

접속 주소는 `https://headlamp.hopcharlie.dev/` 하나다. 기존 클러스터 선택 화면에서 관리 EKS·워크로드 EKS·GKE를 전환한다. EKS는 Cognito, GKE는 개인 Google 로그인과 OAuth2 Proxy 자동 갱신을 사용한다. `/gke` 과거 링크도 같은 root UI의 GKE 화면으로 이동한다.

## 인증과 경로

| 요청 | 담당 |
|---|---|
| root UI, `/config`, 정적 파일, Cognito OIDC | 기존 `headlamp` release |
| `/clusters/charlie-dev-gcp-workload` REST·WebSocket | Google proxy와 전용 loopback Headlamp |
| `/gke/oauth2/start`, callback, sign_out | Google proxy |
| GKE의 encoded clear-token | Google sign_out으로 이동 |
| 다른 클러스터 API와 clear-token | 기존 Cognito Headlamp |

GKE 세션이 없거나 만료되면 API는401을 반환하고, 기존 UI의 GKE 인증 화면에 Google 로그인 버튼이 표시된다. 토큰을 복사하지 않는다. root 전체에 Google 로그인을 강제하지 않으며 AWS 사용자의 접근 조건을 바꾸지 않는다.

## 준비와 권한

- 실제 대상은 관리 EKS `charlie-dev-management`, namespace `headlamp`다. GKE API endpoint/CA와 ALB source IPv4 CIDR을 확인한다. IAM/RBAC/DNS 변경은 이 구성에 필요하지 않다.
- 기존 root Headlamp에 세 context가 등록되어 있어야 한다. GKE 추가가 필요하면 [기존 등록 절차](headlamp-gke.md)를 먼저 따른다. 기존 AWS Secret을 Google backend에 복사하지 않는다.
- OAuth web client callback은 `https://headlamp.hopcharlie.dev/gke/oauth2/callback`이다. Google 계정별 기존 IAM/RBAC가 적용되며 cloud-platform scope는 읽기 전용을 보장하지 않는다.
- OAuth Secret 키는 `client-id`, `client-secret`, `cookie-secret`, `allowed-emails`다. cookie-secret은32바이트 난수를 URL-safe Base64로 인코딩한다. 허용 이메일을 정확히 지정하며 wildcard domain을 함께 켜지 않는다.
- 권한이 필요한 조회·Secret 등록·Helm 적용·Git 게시는 사용자 승인 범위 안에서 실행한다. 이미 받은 해당 범위의 승인은 다시 요청하지 않는다. 비밀값은 채팅·Git·렌더링 결과·명령 인자에 넣지 않는다.

Google에서 프로필만 동의하면 로그인 자체는 성공해도 GKE API가401을 반환할 수 있다. 2026-11-03 실제 검증에서는 사용자가 요청된 Google Cloud 범위에 추가 동의한 뒤 같은 계정의 조회가 성공했다. 모든401을 이 원인으로 단정하지 말고 응답과 동의 범위를 구분한다.

## 배포 순서

1. Google 전용 kubeconfig를 준비한다. 공개 GKE describe JSON을 사용하며 실제 cluster name을 기존 root UI와 일치시킨다. 아래 기본 명령은 오프라인 검사다.

```powershell
python scripts/prepare-headlamp-google.py --metadata gke-public.json
```

승인된 적용에는 관리 EKS의 명시적 접속 파일·context를 전달한다. 전용 Secret이 없으면 만들고, 이전 `gke` 별칭은 rollback을 위해 보존한다. 다른 클러스터·기존 인증 정보·충돌하는 endpoint/CA가 있으면 거부한다. AWS `headlamp-clusters`는 수정하지 않는다.

```powershell
python scripts/prepare-headlamp-google.py --metadata gke-public.json `
  --kubeconfig .generated/access/management.kubeconfig `
  --context charlie-dev-management --install-secret
```

2. 공개 Google values를 만든다. CIDR은 실제 확인값으로 바꾼다.

```yaml
clusterName: charlie-dev-gcp-workload
proxy:
  trustedProxyCIDRs:
    - CONFIRMED_ALB_SOURCE_CIDR
oauth:
  existingSecret: headlamp-gke-google-oauth
clustersSecret: headlamp-gke-google-clusters
```

root release에는 아래 override만 추가한다. 기존 values는 유지한다.

```yaml
googleAuth:
  enabled: true
  clusterName: charlie-dev-gcp-workload
```

3. 두 차트와 테스트를 검증한다. Google의 alpha 설정에는 provider·server·upstream·헤더 구성이 함께 들어가며 Secret 환경변수는 런타임에만 확장된다. pinned7.15.5 이미지에서 비밀값 대신 dummy 값을 사용해 `--config-test`를 실행한다. 실제 kubeconfig는 렌더링에 넣지 않는다.

```powershell
python scripts/tests/test-headlamp.py
python scripts/tests/test-headlamp-gke-google.py
python scripts/tests/test-headlamp-google-config.py
```

4. 검증된 PR을 main에 머지한 뒤 Google release, root plugin 순서로 배포한다. 다음 파일명은 위에서 준비한 공개 values를 가리킨다.

```powershell
helm upgrade --install headlamp-gke-google helm/charts/headlamp-gke-google `
  --namespace headlamp --kubeconfig .generated/access/management.kubeconfig `
  --kube-context charlie-dev-management -f google-public-values.yaml `
  --atomic --wait --timeout 5m

helm upgrade headlamp helm/charts/headlamp-team --reuse-values `
  --namespace headlamp --kubeconfig .generated/access/management.kubeconfig `
  --kube-context charlie-dev-management -f team-google-override.yaml `
  --atomic --wait --timeout 5m
```

`--reuse-values`는 root release의 기존 Cognito/클러스터 값을 유지한다. Google 전용 kubeconfig만 나중에 갱신하는 경우에도 전용 backend를 재시작해 반영한다. 이전 `/gke` cookie와 충돌하지 않도록 새 `__Host-headlamp_gke` 쿠키를 사용하므로 마이그레이션 후 한 번 다시 로그인한다.

## 검증과 운영

1. root 화면의 세 클러스터를 왕복한다. AWS Cognito 로그인과 기존 RBAC가 유지되고 GKE 인증 화면이 수동 토큰 대신 Google 버튼을 제공하는지 확인한다.
2. GKE Google 로그인 후 node/namespace 조회와 일반 watch가 동작하는지 확인한다. 익명·위조 신원/토큰 헤더 API 요청은401이어야 하며, 허용 목록 밖의 계정은 거부되어야 한다.
3. 갱신 로그만 보지 말고 로그인5분 후 tokeninfo의 남은 유효기간과 만료 시각 연장까지 확인한다. 실제 토큰·쿠키를 기록하지 않는다. 원래 토큰 만료 뒤 장시간 사용 검증은 별도로 구분한다.
4. GKE 로그아웃 및 전체 Sign out 후 Google 세션이 제거되어 재로그인을 요구하는지 확인한다. 과거 수동 Headlamp GKE 쿠키가 남더라도 Google proxy 인증을 우회하지 못해야 한다. 프록시는 Google 세션을 읽은 뒤 backend로 보내는 Cookie를 제거해 과거 토큰이 새 access token을 덮어쓰지 못하게 한다.
5. ALB target `/ping` healthy, root AWS 정상, Google callback 경로 우선순위를 확인한다. ALB/Cloudflare 등 외부 계층의 callback code/state 로그 설정도 확인한다. proxy request logging 비활성만으로 외부 로그가 차단되는 것은 아니다.

Headlamp0.46의 기본 build는 WebSocket multiplexer가 비활성이며 개별 `/clusters/<name>` 경로를 사용한다. 향후 multiplexer를 활성화할 때는 여러 클러스터 인증을 한 socket에 섞는 경계를 다시 설계해야 한다. drain/exec 등의 실제 변경 작업은 조회 검증에 포함하지 않는다.

Google External/Testing + cloud-platform 범위에서는 refresh token이7일 뒤 만료된다. 사용자 취소·정책·세션 만료에도 재로그인이 필요하다. 쿠키 수명이 access token의 유효기간을 늘리는 것은 아니다.

## 롤백

root release의 plugin을 끄거나 해당 release를 이전 revision으로 되돌리고, Google release를 이전 revision으로 되돌린다. 전용 kubeconfig에 보존한 `gke` 별칭으로 이전 `/gke` UI를 사용할 수 있다. 서로 다른 쿠키 이름 때문에 재로그인이 필요할 수 있다. 공용 ALB·기존 AWS release·OAuth client·Secret을 삭제하지 않는다. 권한 회수나 자격 증명 폐기는 별도 영향 범위를 확인한다.

## 근거

- [ADR0028 단일 UI](adr/0028-unify-headlamp-cluster-ui.md)
- [구현·검증 계획](plans/2026-11-03-headlamp-unified.md)
- [Headlamp0.46 plugin runtime](https://github.com/kubernetes-sigs/headlamp/blob/v0.46.0/frontend/src/plugin/index.ts)
- [OAuth2 Proxy7.15.5 alpha loader](https://github.com/oauth2-proxy/oauth2-proxy/blob/v7.15.5/pkg/apis/options/load.go)
- [Google refresh token 만료](https://developers.google.com/identity/protocols/oauth2#expiration)
