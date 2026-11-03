# 0028. Headlamp UI는 하나로 유지하고 GKE 인증 경로만 분리한다

- 날짜: 2026-11-03
- 상태: 채택 — 사용자 요청으로 구현·PR·main 머지·배포를 승인했다. 실제 검증 결과는 배포 기록과 구분한다.
- 관련 ADR: [0017 Cognito](0017-team-headlamp-with-cognito.md), [0020 GKE 등록](0020-add-gke-to-team-headlamp.md), [0027 Google 자동 갱신](0027-headlamp-google-auto-refresh.md)

## 배경과 제약

사용자는 기존 Headlamp 한 화면에서 관리 EKS·워크로드 EKS·GKE를 전환해야 한다. ADR0027의 `/gke` 별도 UI에서는 Google 로그인과 실제 refresh 성공을 확인했지만 통합 운영 흐름을 충족하지 못했다.

Headlamp0.46의 직접 OIDC access-token 선택은 인스턴스 전역이라 Google opaque access token과 Cognito ID token을 클러스터별로 선택할 수 없다. 기존 Cognito 설정, GKE Google refresh, 개인 권한과 이메일 허용 목록을 유지한다.

## 검토한 대안

- 별도 UI 사이 링크: 인증은 작동하지만 사용자가 요구한 하나의 클러스터 선택 화면이 아니므로 제외한다.
- root 전체에 Google 로그인 강제: 기존 AWS 사용자의 접근 조건이 달라져 제외한다.
- Headlamp fork: 유지 비용이 크고 공식 plugin과 경로 분리로 해결할 수 있어 제외한다.
- 기존 root UI + GKE API 프록시 + 작은 공식 route plugin: 사용자 경험과 인증 경계를 모두 보존하므로 채택한다.

## 결정과 이유

기존 Headlamp는 세 context와 Cognito 인증을 그대로 제공한다. opt-in plugin이 정확히 GKE의 `/c/<name>/login`, `/token` 화면만 Google 로그인 버튼으로 대체한다. 플러그인은 공식 `window.pluginLib`와 `registerRoute`를 사용하며 별도 빌드 의존성이 없다. 반환 주소는 같은 GKE root로 고정한다.

ALB는 `/clusters/<GKE>` API와 `/gke/oauth2` 인증 경로만 Google proxy/backend로 보낸다. `/gke` 과거 링크는 기존 UI의 `/c/<GKE>/`로 이동한다. GKE 전용 backend도 root base URL과 실제 cluster name을 사용하며 이전 `gke` 별칭은 config에서 보존해 rollback을 지원한다. 기존 AWS kubeconfig는 변경하지 않는다.

Google cookie는 `__Host-headlamp_gke`, Path=/, Secure, HttpOnly로 설정한다. 과거 `/gke` cookie와 이름이 달라 마이그레이션 때 한 번 다시 로그인하며 기존 쿠키가 인증에 섞이지 않는다. GKE의 encoded clear-token 경로만 Google sign_out으로 연결해 기존 UI 로그아웃과 전체 Sign out에 반영한다. 과거 수동 Headlamp GKE 쿠키는 남을 수 있으나 proxy 인증을 대신하지 못한다.

OAuth2 Proxy7.15.5 alpha 설정에서 Google provider·loopback upstream·요청 헤더를 명시한다. Secret 환경변수는 런타임에만 확장한다. `Authorization`과 `X-Forwarded-Access-Token`에 모두 세션 access token을 주입하고 외부 입력은 제거한다. Google 세션을 읽은 뒤 upstream의 `Cookie`도 제거해 과거 수동 토큰 쿠키가 serviceproxy 인증을 덮어쓰지 못하게 한다. 이는 일부 Headlamp helper가 proxy-auth 전용 헤더를 읽지 않는 문제를 피한다. Headlamp는 loopback 바인딩을 유지하며 upstream Host로 재작성한다.

## 영향과 후속 작업

- 일반 REST와 기본 watch/WebSocket은 클러스터별 경로로 분리한다. stock0.46 build의 WebSocket multiplexer는 비활성이다. 향후 해당 기능을 활성화하면 별도 인증 설계가 필요하다.
- Google scope는 기존 cloud-platform이며 GKE를 읽기 전용으로 강제하지 않는다. 사용자 IAM/RBAC를 따른다. Google Testing·세션 만료 시 재로그인이 필요하다.
- 서버 로그인 거부·세션 만료는 API401을 반환하고 GKE 로그인 버튼으로 복구한다. Google 인증을 다른 클러스터에 적용하지 않는다.
- pinned image의 config-test, plugin 실행, 세 클러스터 왕복, Google 실제 refresh·logout, 익명·위조헤더 거부, 기존 AWS 인증을 검증한다. drain/exec 같은 변경 기능은 작업 범위를 넘어 실제 실행하지 않는다.
- 새 Secret 준비 도구는 승인된 install 모드만 Kubernetes를 변경하며 비밀값을 기록하지 않는다. GitOps 자동 적용은 새로 켜지 않는다.

## 관련 자료

- [통합 운영 절차](../headlamp-gke-google.md)
- [구현 계획](../plans/2026-11-03-headlamp-unified.md)
- [Headlamp plugin globals](https://github.com/kubernetes-sigs/headlamp/blob/v0.46.0/frontend/src/plugin/index.ts)
- [Headlamp routes](https://github.com/kubernetes-sigs/headlamp/blob/v0.46.0/frontend/src/components/App/RouteSwitcher.tsx)
- [OAuth2 Proxy alpha loader](https://github.com/oauth2-proxy/oauth2-proxy/blob/v7.15.5/pkg/apis/options/load.go)
