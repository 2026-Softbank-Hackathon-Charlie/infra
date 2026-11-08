# 0093. 온프레 수집기는 장비 식별과 서버 검증 뒤 명시적으로 설치한다

- 상태: 채택(설치 도구 검증 완료, 실제 대상 연결 차단)
- 날짜: 2026-11-08

## 상황

사용자가 온프레 관측 수집기 적용을 명시적으로 요청했다. ADR0092의 고정 차트 준비물을 실제 적용할 때 다른 Kubernetes context에 설치하거나 인증·TLS 선행 조건 없이 시작하지 않아야 한다. 점검 당시 기존 대상 `hop-onprem-laptop.tail567fe8.ts.net`은 Tailscale Online=false이고 마지막 연결은 2026-11-08 01:20:00.1 UTC다. 관리 측 기존 API 경로의 DNS 조회가 실패했다. 별도의 online VM을 기존 노트북과 동일한 대상으로 간주하지 않는다.

## 결정

- 고정 차트 renderer를 재사용하는 별도 관리자 설치 명령을 둔다. kubeconfig·context와 별도 확인한 kube-system namespace UID를 필수 입력으로 받는다. 사용자 요청으로 승인된 기존 대상만 선택한다.
- 기본 동작은 서버 dry-run이다. 기존 observability namespace의 privileged PSA, local-path 저장소, 인증·TLS Secret 7개, 정해진 HTTPS 중앙 전송 endpoint, monitoring CRD를 확인한 후 전체 bundle을 dry-run한다. Secret은 존재 이름만 조회하고 값은 읽지 않는다.
- `--execute`는 같은 검사와 직전 UID 재검증을 거쳐 43개 렌더 자원을 적용하고 명시된 Deployment·DaemonSet rollout을 확인한다. CRD·namespace·새 자격·전송 경로·앱 SDK 활성화는 자동으로 만들지 않는다. 적용 전 노드 여유 용량과 권한은 관리자가 렌더 결과 기준으로 검토한다.
- readiness와 실제 중앙 수신은 별도다. 결과의 receivingVerified는 항상 false이며 최신 service/namespace/cluster에 묶인 로그·trace·metrics 검증을 후속으로 수행해야 한다. 실패 시 이미 적용한 부분이 남을 수 있으며 자동 삭제·업무 DB 변경은 하지 않는다.

## 검토한 대안

앱 등록 credential의 권한을 자동 확대하는 방식은 등록과 노드 관측의 경계를 무너뜨린다. 사용 가능한 다른 VM에 자동 설치하는 방식은 사용자 대상과 어긋날 수 있다. 검토되지 않은 임의 manifest 경로 대신 기존 고정 renderer를 직접 호출한다.

## 결과

새 권한이나 유료 클라우드 리소스를 만드는 기능은 없다. 기존 노트북에서 Alloy hostPath·gateway 2Gi·Prometheus 10Gi는 ADR0092와 같은 용량을 사용한다. 이 변경으로 해당 자원을 아직 생성하지 않았다. 나중에 설치 자원을 철회할 때도 업무 PVC/DB를 보존하고 queue/Prometheus PVC는 별도 승인 없이 삭제하지 않는다.

로컬 경계 테스트 6개와 고정 실제 차트 렌더 테스트 3개를 통과했다. 실제 노트북 연결은 실패했고 서버 dry-run·collector 설치·중앙 수신은 실행하지 못했다. 노트북 재연결 또는 명시적인 대상 변경 후 이어서 검증한다.
