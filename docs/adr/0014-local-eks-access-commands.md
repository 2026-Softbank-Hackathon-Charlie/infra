# 0014. Make 명령으로 로컬 EKS 터널과 Argo CD 접속 표준화

- 날짜: 2026-11-02
- 상태: 채택
- 관련 ADR: [0010](0010-grant-eks-operator-access.md), [0003](0003-share-project-inputs-between-local-and-ci.md)

## 배경과 제약

운영자가 private EKS의 SSM 터널과 관리 클러스터 Argo CD 접속을 확인했고,
반복 실행할 Make 명령과 README를 요청했다. 기존 bridge와 클러스터 권한을 사용하고
Terraform·IAM·클러스터 설정을 변경하지 않는 범위로 구현을 승인받았다.
자동화한 명령의 실제 연결은 이번 로컬 검증 범위에 포함하지 않는다.

## 검토한 대안

- 수동 명령 복사: 새 코드가 필요 없지만 endpoint·profile·context·TLS 설정을 반복 관리해야 한다.
- 백그라운드 통합 실행: 한 명령으로 접속할 수 있지만 프로세스 수명과 정리·재시도 정책이 추가된다.
- 두 개의 foreground Make 명령: 기존 접속 흐름을 유지하고 각 터미널에서 오류와 종료를 직접 관리할 수 있어 선택한다.

## 결정과 이유

`make aws-tunnel`은 기본 관리 EKS의 API를 10443으로 연결하며
`CLUSTER=workload`는 11443을 사용한다. 계정 일치, 같은 VPC의 단일 running bridge,
SSM Online과 로컬 포트를 확인한 뒤 세션을 시작한다.
`make argo-port-forward`는 별도 터미널에서 관리 클러스터의
`argocd/svc/argocd-server` 443을 loopback 8080으로 연결한다.
포트 변경을 지원하며 두 명령 모두 foreground에서 실행하고 Ctrl+C로 종료한다.

공유 프로젝트 JSON의 region·이름 및 로컬 계정 JSON을 재사용한다.
모든 AWS 호출에 프로젝트 region을 명시하고 충돌하는 region 환경 변수는 안내 후 무시한다.
터널의 기본 AWS 프로필은 `charlie-admin`이며 명시적으로 변경할 수 있다.
Argo 인증은 kubeconfig에 저장된 프로필을 사용하고 다른 프로필 요청은 거부하여
터널 재생성을 안내한다. 로그인이나 비밀값 조회·저장은 자동화하지 않는다.

클러스터별 `.generated/access/*.kubeconfig`만 생성하고 글로벌 kubeconfig는 변경하지 않는다.
전용 디렉터리 0700, 파일 0600과 원자적 교체를 사용한다.
EKS CA와 원래 hostname의 TLS 서버명을 유지하며 인증서 검증을 끄지 않는다.
Argo 명령은 명시적인 관리 context와 kubeconfig를 사용한다.

## 영향과 후속 작업

- 기대 효과: 두 터미널의 명령과 README만으로 기존 접속 경로를 반복 사용할 수 있다.
- 단점과 위험: 프로필 인증과 bridge·클러스터가 준비되어 있어야 하며 두 프로세스를 유지해야 한다. 포트 사전 확인과 실제 listener 시작 사이의 경쟁은 CLI 오류로 처리한다. 세션 시작 실패 시 kubeconfig는 남지만 자동 재시도하지 않는다.
- 필요한 후속 작업 및 검증: fake AWS·kubectl로 Make 진입점, TLS·프로필·계정·bridge·포트 오류를 검증한다. 실제 연결은 운영자가 실행할 때 확인하며 이번 구현 중에는 세션·배포·권한 변경을 수행하지 않는다.

## 관련 자료

- [Makefile](../../Makefile)
- [접속 도우미](../../scripts/eks-access.py)
- [fake CLI 검증](../../scripts/tests/test-eks-access.py)
- [접속·설치 안내](../../README.md#private-eks-접근과-argo-cd-설치)
