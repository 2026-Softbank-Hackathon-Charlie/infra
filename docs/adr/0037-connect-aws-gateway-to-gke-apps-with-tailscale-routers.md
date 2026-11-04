# 0037. AWS 게이트웨이와 GKE 앱을 Tailscale 서브넷 라우터 두 대로 잇는다

- 날짜: 2026-11-04
- 상태: 제안
- 관련 ADR: [ADR 0002](0002-require-approval-for-privileged-operations.md), [ADR 0016](0016-run-hopcharlie-platform-on-management-cluster.md), [ADR 0036](0036-app-routing-and-argo-apps.md)

## 배경과 제약

[ADR 0036](0036-app-routing-and-argo-apps.md)의 앱 주소는 관리 EKS의 게이트웨이 pod가 앱의 내부 로드밸런서 80 포트로 요청을 보내는 구조다. EKS 앱은 같은 VPC 안이라 이 경로가 열려 있지만, GKE 앱의 내부 로드밸런서(GKE 기본 서브넷 `10.80.0.0/20`)는 AWS VPC `10.40.0.0/16`에서 갈 길이 없다.

- 확인한 사실: AWS VPC와 관리 서브넷 두 개(`10.40.0.0/20`, `10.40.16.0/20`), GKE VPC와 서브넷 `10.80.0.0/20`은 이미 있다. 두 클라우드 사이 VPN이나 상호 연결은 없다. 팀 tailnet은 이미 있다(소유자 계정은 팀 공용 계정).
- 제약: 기존 VPC, 클러스터, 다른 스택의 state를 바꾸지 않는다. 인증 키를 Terraform 변수, state, 클라우드 로그에 남기지 않는다. 열어 둘 통신은 관리 게이트웨이 대역에서 GKE 앱 대역으로 가는 TCP 80 하나다.
- 미확인: 실제 Tailscale 연결 속도, GKE 내부 로드밸런서가 자동으로 만드는 방화벽 규칙이 `10.40.0.0/19` 출발 요청을 허용하는지.

## 검토한 대안

- AWS Site-to-Site VPN과 GCP HA VPN: 관리형이고 표준이지만 양쪽에 게이트웨이, 터널 여러 개, BGP 설정이 필요하고 시간당 비용이 계속 든다. 해커톤 범위에 비해 구성과 비용이 크다.
- GKE 앱을 공개 로드밸런서로 열기: 가장 간단하지만 앱이 인터넷에 직접 노출되고, ADR 0036의 "게이트웨이만 앱 주소를 연다"는 구조가 깨진다.
- 게이트웨이 pod에 Tailscale 사이드카 붙이기: 라우터 VM이 필요 없지만 플랫폼 차트와 pod 권한을 바꿔야 하고, pod마다 tailnet 기기가 생긴다([ADR 0016](0016-run-hopcharlie-platform-on-management-cluster.md)도 사이드카가 따로 필요하다고 기록했다).
- 양쪽에 Tailscale 서브넷 라우터 VM 한 대씩(선택): 기존 tailnet을 쓰고, 작은 VM 두 대와 라우트 몇 개로 끝난다. 클러스터와 플랫폼 코드는 바꾸지 않는다.

## 결정과 이유

[terraform/integrations/aws-gcp/tailscale](../../terraform/integrations/aws-gcp/tailscale/)에 독립 스택을 둔다. 기존 스택의 state를 읽지 않고, 필요한 네트워크 정보는 data source로만 읽는다.

1. 라우터: AWS 관리 서브넷에 `t3.micro` 한 대, GKE 서브넷에 `e2-micro` 한 대를 둔다. 둘 다 공인 IP와 SSH 키가 없다. 이미지는 공식 Amazon Linux 2023과 Debian 12 이미지를 ID로 고정한다.
2. 경로: AWS 관리 서브넷 라우트 테이블 두 개에 `10.80.0.0/20 → AWS 라우터`를, GKE VPC에 `10.40.0.0/19 → GCP 라우터`를 더한다. 라우터는 SNAT를 하지 않으므로 GKE 쪽은 원래 출발 주소(`10.40.0.0/19`)를 본다. 라우터의 Tailscale이 끊기면 원격 대역으로 가는 패킷은 버린다(fail closed).
3. 방화벽: 라우터의 바깥 통신은 HTTPS 443과 DNS만 허용한다. GCP 라우터는 GKE 대역 TCP 80으로만 보낼 수 있고, 들어오는 연결은 IAP 대역의 등록 화면 포트(5252)만 받는다. AWS 라우터는 게이트웨이 대역의 TCP 80만 받는다.
4. 등록: 인증 키를 쓰지 않는다. 라우터는 Tailscale 웹 화면을 열어 두고, 운영자가 SSM(AWS) 또는 IAP(GCP) 터널로 접속해 tailnet 소유자 계정으로 브라우저 로그인한다. 그래서 키가 변수, state, user data에 남지 않는다.
5. tailnet 정책: Terraform은 tailnet을 바꾸지 않는다. 출력 `tailscale_policy_candidate`는 태그 두 개, 라우트 자동 승인, `10.40.0.0/19 → 10.80.0.0/20 tcp:80` grant를 담은 후보다. 소유자가 기존 정책에 **합쳐 넣는다**. 후보의 `acls: []`, `ssh: []`로 기존 정책을 덮어쓰면 기존 기기의 규칙이 사라진다.
6. CI: `terraform-check`의 검증 대상에 이 스택을 더한다. backend 없이 init, validate, mock test만 하며 클라우드 자격 증명을 쓰지 않는다. 배포 파이프라인 대상(`project-config.py`)에는 넣지 않는다.

## 영향과 후속 작업

- 기대 효과: 클러스터, 플랫폼 코드, 기존 VPC를 바꾸지 않고 GKE 앱을 게이트웨이 경로에 붙일 수 있다. 앱은 인터넷에 직접 열리지 않는다.
- 단점과 위험:
  - 라우터가 한 대씩이라 하나가 멈추면 GKE 앱 주소가 끊긴다(단일 장애점).
  - 바깥 UDP를 막았으므로 라우터 사이는 직접 연결 대신 Tailscale 중계(DERP, HTTPS)를 거친다. 지연과 처리량이 직접 연결보다 나쁠 수 있다. 시연 트래픽에는 충분하다고 가정하며 실제 측정이 필요하다.
  - 등록과 정책 반영은 tailnet 소유자 계정이 있어야 한다.
- 필요한 후속 작업 및 검증(각각 별도 승인):
  1. 이미지 ID, VPC·서브넷 ID, backend 값을 넣고 plan을 검토한 뒤 apply한다.
  2. tailnet 소유자가 라우터 두 대를 등록하고 정책 후보를 기존 정책에 합친다.
  3. 게이트웨이 대역에서 GKE 내부 로드밸런서 80 포트까지 요청이 가는지, GKE 쪽 방화벽이 막지 않는지 확인한다.
  4. GKE 앱 환경 설정(`gke` 프로필, `gatewayFrom: 10.40.0.0/19`)은 별도 PR로 다룬다.

## 관련 자료

- [terraform/integrations/aws-gcp/tailscale/main.tf](../../terraform/integrations/aws-gcp/tailscale/main.tf)
- [terraform/integrations/aws-gcp/tailscale/router-bootstrap.sh.tftpl](../../terraform/integrations/aws-gcp/tailscale/router-bootstrap.sh.tftpl)
- [terraform/integrations/aws-gcp/tailscale/policy.json.tftpl](../../terraform/integrations/aws-gcp/tailscale/policy.json.tftpl)
- Tailscale 서브넷 라우터 문서: https://tailscale.com/kb/1019/subnets
