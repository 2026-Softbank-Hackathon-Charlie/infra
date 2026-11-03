# GKE Headlamp Google 로그인과 자동 갱신

이 문서는 `https://headlamp.hopcharlie.dev/gke`의 **제안된 별도 배포**를 설명한다. 로컬 설정과 실제 배포·로그인·갱신 성공은 구분한다. 기존 `/`의 AWS Cognito 로그인 및 기존 GKE 수동 토큰 경로는 유지한다.

## 권한과 보안 경계

- 팀원은 각자의 Google 계정으로 로그인한다. GKE에서 적용되는 권한은 해당 사용자의 기존 Google IAM과 Kubernetes RBAC다. 이 구성은 읽기 전용을 보장하지 않으며 `cloud-platform` scope는 다른 Google Cloud API 권한도 가질 수 있다.
- OAuth2 Proxy는 Google refresh token을 세션 갱신에 사용한다. 암호화된 세션 쿠키와 쿠키 암호화 키를 보호하고, 토큰·OAuth client secret·쿠키를 채팅, Git, 로그 또는 명령 인자에 기록하지 않는다. 개인 `gcloud` 인증 파일이나 서비스 계정 키를 공유하지 않는다.
- 프록시 앞의 ALB만 신뢰하고 외부에서 제공한 신원·토큰 헤더가 Headlamp까지 그대로 전달되지 않도록 한다. 허용 목록에 없는 Google 계정과 인증되지 않은 Kubernetes API 요청은 차단한다.
- OAuth callback의 `code`·`state`가 ALB 또는 Cloudflare 등 외부 계층의 접근 로그에 남는지 실제 로그 설정을 적용 전에 확인하고, 노출되지 않도록 운영 정책을 마련한다. 프록시의 `--request-logging=false`만으로 외부 계층의 로그 노출을 막을 수는 없다.
- OAuth 클라이언트 및 자격 증명 생성·등록, Secret 저장, 클러스터 조회, Helm 적용, IAM/RBAC 또는 네트워크 변경은 각각 구체적 범위를 설명하고 명시적으로 승인받은 후에만 실행한다. 이 문서 자체는 실행 승인이 아니다.

## 배포 전 준비

1. 대상 관리 EKS, GKE endpoint와 CA, 접근 권한, ALB 그룹 및 ALB에서 Pod로 도달할 때 보이는 실제 source CIDR을 승인된 범위에서 확인한다. 기존 `/` Ingress 및 listener 규칙을 기록한다. 예측한 CIDR을 신뢰 프록시 값으로 쓰지 않는다. 현재 차트는 각 옥텟이 0–255인 IPv4 CIDR `/1`–`/32`만 허용하며 IPv6는 지원하지 않는다. 실제 ALB source가 해당 범위가 아니면 임의로 넓히지 말고 중단한다.
2. Google Auth Platform 동의 화면을 준비한다. External/Testing이면 사용할 팀원을 test user로 등록한다. web application OAuth client의 승인된 redirect URI는 `https://headlamp.hopcharlie.dev/gke/oauth2/callback`이다. `openid`, `email`, `profile`, `https://www.googleapis.com/auth/cloud-platform` scope와 기존 개인별 GKE 권한이 필요하다.
3. Google OAuth client ID/secret, 무작위 쿠키 암호화 비밀, 접근 허용 이메일 목록을 안전한 경로로 준비한다. 이메일을 정확히 제한하고 wildcard domain 허용을 함께 켜지 않는다. 사용자 개인 credential 파일을 복사하지 않는다.
4. 승인된 절차로 관리 EKS namespace `headlamp`에 OAuth Secret을 만든다. Secret 이름은 차트의 `oauth.existingSecret`에 지정하며 키는 `client-id`, `client-secret`, `cookie-secret`, `allowed-emails`다. `allowed-emails`는 정확한 이메일을 한 줄에 하나씩 넣는다.
5. 인증 정보가 없는 GKE 전용 kubeconfig를 별도 Secret의 `config` 키로 준비한다. GKE API endpoint와 CA만 넣고 사용자 token, 개인 인증 파일, 서비스 계정 키는 넣지 않는다. Secret 이름은 `clustersSecret`에 지정한다. 기존 AWS `headlamp-clusters`를 덮어쓰지 않는다.

## 로컬 검증과 적용

1. `helm/charts/headlamp-gke-google`을 검토한다. OAuth2 Proxy **7.15.5**는 공식 Quay에서 확인한 multi-architecture digest `sha256:8498b0d0ef0a7b29686414000a08aee467f02d0299c9ed1e006a8f33fc017916`로 고정되어 있다. `proxy.trustedProxyCIDRs`에는 확인된 ALB source CIDR을 넣는다. `oauth.existingSecret`, `clustersSecret`은 위에서 준비한 이름과 일치시킨다. 기본 host는 `headlamp.hopcharlie.dev`다.
2. 비밀값 없이 `helm template`과 저장소 테스트로 설정을 렌더링하고, 신규 서비스가 프록시만 노출하는지, 신원 헤더 방어와 이메일 허용 목록, `/gke` Ingress의 `group.order: "19"`를 확인한다. 기존 AWS Ingress의 order는 `20`이다.
3. 실제 자격 증명·Secret·Helm release·Ingress를 생성하기 전 정확한 대상과 권한, 영향과 롤백 범위를 제시하고 별도 승인을 받는다. 승인 후 지정된 namespace에 별도 release로 설치한다. 기존 AWS release와 수동 GKE context를 수정하지 않는다.
4. ALB listener에서 `/gke` 규칙이 `/`보다 먼저 적용되는지, 신규 target의 `/ping` health가 정상인지 확인한다. health 경로가 공개되더라도 Kubernetes API나 나머지 UI 인증을 우회해서는 안 된다.

공개 values 파일의 형태는 아래와 같다. 대문자 항목을 확인된 값으로 바꾸며 비밀값은 넣지 않는다. 이미지 기본값은 차트와 `helm/images.lock.json`에서 관리한다.

```yaml
proxy:
  trustedProxyCIDRs:
    - CONFIRMED_ALB_SOURCE_CIDR
oauth:
  existingSecret: headlamp-gke-google-oauth
clustersSecret: headlamp-gke-google-clusters
```

위 파일을 `public-values.yaml`로 준비한 뒤 클러스터에 접속하지 않고 렌더링한다. placeholder가 남아 있으면 차트가 이를 거부한다.

```powershell
helm template headlamp-gke-google helm/charts/headlamp-gke-google `
  --namespace headlamp -f public-values.yaml
```

## 실제 검증

1. 허용된 개인 계정으로 로그인해 GKE의 namespace 또는 node를 조회하고, 권한이 없는 작업은 해당 사용자의 실제 IAM/RBAC 정책대로 거부되는지 확인한다. 권한 이상 동작 시 토큰을 출력하지 말고 중단한다.
2. 허용 목록 밖 계정과 미인증 브라우저가 Kubernetes API에 접근하지 못하는지 확인한다. 신원·토큰 헤더를 직접 위조한 요청도 다른 사용자로 인증되지 않아야 한다. 토큰, 쿠키 및 개인정보가 로그에 출력되지 않는지 승인된 범위에서 살핀다.
3. 처음 발급된 access token의 만료 시각을 기준으로 **원래 토큰이 만료된 뒤** 같은 브라우저 세션에서 다시 GKE API 조회가 성공하는지 확인한다. 단순히 로그인 5분 뒤 API가 성공하는 것만으로는 자동 갱신을 입증할 수 없다. 만료 시각이나 토큰 값을 기록할 때 민감한 본문은 저장하지 않는다.
4. AWS management/workload 로그인과 기존 수동 GKE 경로가 계속 작동하는지 확인한다. 세션 취소 또는 만료 후에는 재로그인이 필요함을 확인한다.

Google OAuth 동의 화면이 External/Testing이고 기본 프로필 외 scope를 요청하면 refresh token이 7일 뒤 만료될 수 있다. 쿠키 만료 설정은 Google access token의 유효기간을 늘리지 않으며, 토큰 취소나 정책 변경 시에도 재로그인이 필요하다.

## 롤백

명시적으로 승인된 범위에서 신규 `/gke` Helm release와 그 전용 Ingress·Service·Secret만 제거한다. 공용 ALB, 기존 AWS Headlamp release·Secret 및 수동 GKE context는 보존한다. ALB 규칙과 AWS 로그인 정상 여부를 확인하고 기존 수동 GKE 토큰 경로로 전환한다. OAuth client 폐기 및 사용자 refresh token 취소는 영향 범위를 확인한 뒤 별도 승인으로 수행한다.

## 참고 자료

- [설계 결정 0027](adr/0027-headlamp-google-auto-refresh.md)
- [Headlamp 신뢰 프록시 보안 요구](https://headlamp.dev/docs/latest/installation/in-cluster/identity-aware-proxy/)
- [OAuth2 Proxy Google 설정](https://oauth2-proxy.github.io/oauth2-proxy/configuration/providers/google/)
- [OAuth2 Proxy 공식 Quay 이미지](https://quay.io/repository/oauth2-proxy/oauth2-proxy?tab=tags)
- [Google refresh token 만료](https://developers.google.com/identity/protocols/oauth2#expiration)
