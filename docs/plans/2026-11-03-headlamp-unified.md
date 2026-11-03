# Unified Headlamp Implementation Plan

> **For agentic workers:** Use subagent-driven-development for independent tasks. Preserve other workers' edits. Main agent owns Git publication, deployment and live verification.

**Goal:** 기존 Headlamp 한 화면에서 관리 EKS·워크로드 EKS·GKE를 전환하고 GKE Google 로그인·자동 갱신을 유지한다.

**Architecture:** 기존 root Headlamp가 세 클러스터 UI와 Cognito 인증을 제공한다. GKE 전용 API 경로와 Google OAuth 경로만 기존 OAuth2 Proxy/loopback Headlamp로 라우팅한다. GKE 인증 화면만 작은 Headlamp route plugin으로 대체한다.

**Tech Stack:** Headlamp0.46.0, OAuth2 Proxy7.15.5, ALB, Helm, Python contract tests, dependency-free browser plugin where supported.

**Spec:** 아래 승인된 요구 및 성공 기준. 사용자는 구현·PR·main 머지·배포를 명시 승인했다.

## Global Constraints
- 루트 URL은 https://headlamp.hopcharlie.dev/ 이며 관리·워크로드 EKS의 기존 Cognito와 권한을 보존한다.
- GKE는 charlie-dev-gcp-workload, 허용 계정은 softbankcharlie@gmail.com이다.
- Google callback은 https://headlamp.hopcharlie.dev/gke/oauth2/callback 으로 유지한다.
- 개인 Google access/refresh token과 AWS OAuth secret을 코드·로그·디스크에 기록하지 않는다.
- Google cookie는 root API 요청을 위해 Path=/로 설정하며 unique cookie name과 Secure/HttpOnly를 유지한다.
- global Google gate, 공유 관리자 토큰, IAM/RBAC/DNS 변경 및 Headlamp fork는 도입하지 않는다.

## Review Focus
- Google 세션 없는 GKE 선택은 수동 토큰 대신 Google 로그인으로 연결되고 AWS 선택은 그대로다.
- 인증 만료·거부 시 반복 redirect 없이 재로그인이 가능해야 한다.
- GKE REST/WebSocket 요청만 Google backend로 가며 Cognito 토큰은 GKE로 전달되지 않는다.
- /gke 과거 링크는 통합 UI로 돌아오고 OAuth callback은 redirect 규칙에 가로채이지 않는다.
- Kubernetes Secret 변환은 AWS entries를 보존하고 충돌·불완전 입력에는 안전하게 실패한다.

## Task 1: Root UI authentication plugin
- [ ] 공식0.46 loader/route 계약을 확인한다.
- [ ] GKE login/token 경로와 AWS 비간섭 회귀 테스트를 먼저 작성한다.
- [ ] optional plugin ConfigMap/mount/checksum 및 GKE-only Google sign-in component를 구현한다.
- [ ] main chart 기본값은 기존 동작을 유지하고 opt-in으로 통합한다.

## Task 2: Per-cluster Google API routing
- [ ] auth/API/legacy path의 렌더 회귀 테스트를 먼저 작성한다.
- [ ] GKE backend root baseURL, named cluster API routing, cookie Path=/와 callback 보존을 구현한다.
- [ ] UI는 root Headlamp만 제공하고 /gke legacy 경로는 해당 root cluster로 보낸다.

## Task 3: Migration and documentation
- [ ] 기존 OAuth Secret을 재사용해 GKE kubeconfig의 context/cluster 이름을 일관되게 변환하는 검증된 절차를 만든다.
- [ ] 신규 ADR0028과 운영 문서·README inventory를 갱신한다. ADR0027의 별도UI 결정을 대체한다.
- [ ] local tests/Helm lint/inventory와 독립 보안·기능 리뷰를 통과한다.

## Task 4: Ship and verify
- [ ] 현재 diff를 conventional commit, named branch push, PR 생성한다.
- [ ] CI와 독립 리뷰가 통과한 head를 main에 머지한다.
- [ ] merged source로 GKE release와 root Headlamp plugin을 배포한다. 필요한 Secret 변경은 메모리에서 수행한다.
- [ ] 같은 화면에서 세 클러스터 선택, AWS Cognito 경로, GKE Google 로그인/실제5분refresh, 위조헤더 거부와 legacy redirect를 검증한다.
- [ ] runtime 수정이 추가되면 별도 검증/PR/merge로 main과 배포 상태를 일치시킨다.
- [ ] 증거/로그/재개 상태를 기록하고 임시 접속을 닫는다.
