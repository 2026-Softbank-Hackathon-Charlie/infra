# 0076. 운영 API의 환경 동등성 검사를 감사 모드로 활성화한다

- 날짜: 2026-11-07
- 상태: 채택
- 근거: 사용자가 운영 서버의 환경 동등성 검증 활성화를 요청했다.

## 배경과 제약

운영 API 로그에서 PLATFORM_PARITY_FILE 미설정을 확인했다. Argo CD가 Deployment를
자동 복원하므로 일회성 kubectl 패치 대신 chart와 관리 환경 values에 설정을 둔다.
검사는 새 이전 시작 직전에 실행하며 실패도 이전을 차단하지 않는 기존 audit 계약을 유지한다.

## 결정과 이유

api.parity.enabled와 config로 비밀값이 없는 ConfigMap을 생성해 API에 읽기 전용으로
마운트한다. 설정 해시를 Pod annotation에 넣어 변경 시 재시작한다. 기본 chart는 비활성화하고
관리 환경에서만 활성화한다. 이미지, 자격증명, RBAC 및 네트워크 정책은 변경하지 않는다.

현재 등록된 moimroom-mig와 docker-doom을 대상으로 한다. 실제 EKS Deployment에서
명시된 ServiceAccount가 없어 default를 사용함을 확인했다. 인증된 계정의 자기 권한 조회와
Secret 목록 금지를 검사하되 검사자의 SAR 권한이 없으면 unknown으로 남긴다.
Secret 이름은 앱 chart의 app-secrets/app-s3 계약과 EKS 메타데이터에서 확인했다.
moimroom-mig-db와 26시간 백업 신선도는 대상에 요구하는 정책이며 대상 존재·복원 성공의
증거가 아니다. 다른 환경에서도 같은 계약이 충족되는지 검사하고 없으면 실패/미확인을 남긴다.

검토 완료된 원본 baseline은 아직 없으므로 baselines를 만들어 넣지 않는다. 모니터링의 앱별
수집 job과 감사 전달 경로도확인 전에는 설정하지 않는다. 부분 검사를 전체 통과로 표시하지 않는다.
HyperDX는 관측 UI이고 현재 프로브는 Prometheus API를 사용한다. HyperDX 설치만으로
수집·알림 전달이 입증되거나 parity 감사 결과가 HyperDX로 자동 전송된다고 주장하지 않는다.

## 검토한 대안

- Deployment 직접 패치: Argo self-heal에 의해 되돌아가므로 채택하지 않는다.
- SSM에 새 비밀 설정 저장: 비밀이 없는 정책 파일이라 ConfigMap으로 충분하다.
- 검증 성공 baseline이나 가상 관측 endpoint 추가: 확인하지 않은 항목을 통과시키므로 제외한다.

## 영향과 검증

API는 Recreate 전략이므로 적용 중 관리 API가 잠시 중단된다. 게이트웨이와 앱 Deployment는
바꾸지 않는다. 실행 중인 이전이 없는지 적용 직전에 재확인한다. 기존 halted run을 자동 재개하지 않는다.
검사 한 번은 최대 30초이고, 필수 항목 미구현·권한 부족은 unknown이다. 활성화만으로 기존 run을
소급 검사하지 않는다. 실제 migration을 검증 목적으로 실행하지 않는다.

Helm lint/render, 운영 설정의 플랫폼 parser 검증, 배포 후 readiness와 활성화 로그를 확인한다.
되돌릴 때는 api.parity.enabled를 false로 바꿔 API를 재배포한다.
