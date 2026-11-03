# 0027. GKE Headlamp에 개인 Google 로그인과 토큰 자동 갱신을 분리한다

- 날짜: 2026-11-03
- 상태: 제안
- 관련 ADR: [0017 팀 Headlamp](0017-team-headlamp-with-cognito.md), [0020 개인 Google 인증](0020-add-gke-to-team-headlamp.md)

## 배경과 제약

기존 GKE context는 개인 Google access token을 수동으로 입력해야 한다. 만료 때마다 입력하는 불편을 줄이되 AWS Cognito 로그인과 GKE의 기존 수동 로그인은 유지한다. 개인별 Google IAM과 GKE RBAC를 사용하며 공유 관리자 토큰을 도입하지 않는다.

Headlamp v0.46.0의 직접 OIDC 방식은 Google access token의 만료와 갱신을 이 구성에서 안정적으로 처리한다는 검증이 없다. 실제 Google OAuth 동의, 프록시, 갱신 및 운영 환경의 경로 우선순위도 아직 검증되지 않았다.

## 검토한 대안

- 기존 수동 토큰: 구조 변경은 없지만 만료 때마다 토큰을 발급·입력해야 한다. 장애 시 대체 경로로 유지한다.
- 기존 Headlamp에서 Google OIDC를 직접 처리: AWS와 함께 사용하는 설정과 Google access token 갱신 동작이 확인되지 않아 선택하지 않는다.
- GKE 전용 Headlamp 앞에 OAuth2 Proxy를 둔다: 기존 AWS 경로를 분리하고 사용자별 Google 토큰의 자동 갱신을 검증할 수 있어 제안한다.
- 공용 서비스 계정 토큰: 개인별 권한과 감사 경계가 사라지므로 제외한다.

## 결정과 이유

동일 호스트의 `/gke` 경로에 별도 Helm release를 두고, Google 인증 및 세션 갱신을 처리하는 OAuth2 Proxy 뒤의 Headlamp가 사용자 access token을 GKE에 전달하도록 제안한다. 기존 `/`의 AWS Headlamp와 수동 GKE context는 변경하지 않는다. Ingress 우선순위는 신규 경로가 먼저 평가되도록 구성하고 실제 ALB 규칙으로 확인한다.

프록시와 Headlamp는 같은 Pod에서 통신하고 Headlamp는 loopback에만 바인딩한다. 서비스는 프록시만 노출하고, 프록시는 클라이언트가 보낸 신원·토큰 헤더를 제거하거나 덮어쓴다. 사용자는 명시한 이메일 허용 목록으로 제한하고 신뢰할 ALB 프록시의 실제 주소 범위를 명시한다. Pod 서비스 계정 토큰은 자동 탑재하지 않는다.

인증 우회 보안 수정이 포함된 OAuth2 Proxy 7.15.5를 공식 Quay의 multi-architecture 이미지 digest `sha256:8498b0d0ef0a7b29686414000a08aee467f02d0299c9ed1e006a8f33fc017916`로 고정한다. OAuth 클라이언트와 세션 암호화 비밀은 별도 Kubernetes Secret으로 참조하며 코드와 렌더링 결과에 포함하지 않는다. 자동 갱신에는 사용자 refresh token 처리가 필요하며, 암호화된 세션 쿠키에 보관된다. 개인 CLI 인증 파일이나 서비스 계정 키를 올리지 않는다.

## 영향과 후속 작업

- 사용자는 개인 Google 계정으로 로그인하고, access token 만료 후에도 유효한 세션과 refresh token이 있으면 별도 수동 입력 없이 갱신할 수 있다. 세션·토큰 만료 또는 취소 시 다시 로그인해야 한다.
- ADR 0020과 마찬가지로 **GKE 읽기 전용을 강제하지 않는다**. 사용자 Google IAM/RBAC에 허용된 변경이 가능하며 `cloud-platform` scope 토큰은 다른 Cloud API 권한도 가질 수 있다.
- Google OAuth 동의 화면이 External/Testing이고 기본 프로필 외 scope를 요청하면 refresh token이 7일 후 만료될 수 있다. 쿠키 유지 기간이 access token 수명을 연장하는 것은 아니다.
- OAuth 클라이언트 생성, Secret 등록, 클러스터 조회 및 배포·권한 변경은 대상과 영향을 구체화해 별도 승인받은 뒤 수행한다. 이 ADR과 로컬 코드 작성 자체는 실행 승인이 아니다.
- 허용되지 않은 사용자와 미인증 API 접근, 위조 헤더, 실제 ALB 경로·헬스체크, 최초 access token 만료 이후 같은 세션의 갱신, 기존 AWS·수동 GKE 경로를 검증한다. 실패하면 신규 경로만 되돌린다.

## 관련 자료

- [운영 절차](../headlamp-gke-google.md)
- [Headlamp 신뢰 프록시](https://headlamp.dev/docs/latest/installation/in-cluster/identity-aware-proxy/)
- [OAuth2 Proxy Google provider](https://oauth2-proxy.github.io/oauth2-proxy/configuration/providers/google/)
- [OAuth2 Proxy 공식 Quay 이미지](https://quay.io/repository/oauth2-proxy/oauth2-proxy?tab=tags)
- [Google refresh token 만료](https://developers.google.com/identity/protocols/oauth2#expiration)
