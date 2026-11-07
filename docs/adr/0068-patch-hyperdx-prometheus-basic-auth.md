# 0068. 고정 HyperDX의 Prometheus 인증 전달을 한정 수정한다

- 날짜: 2026-11-07
- 상태: 채택 — 사용자 이미지 게시·한정 배포 승인, ECR 게시 완료, 사용자 PR 머지 후 운영 반영 예정
- 관련 ADR: [0061](0061-private-otlp-ingress-for-aws-workload.md)

## 배경과 제약

현재 query proxy에 올바른 BasicAuth로 직접 요청하면 성공하지만 HyperDX에서는401이다. 고정2.39.1 API의 외부 Prometheus 분기가 저장된 username/password를 전달하지 않으며 exemplar 조회는 password를 선택하지 않는다.

## 검토한 대안

- 인증 제거·URL에 credential 삽입: 보안 경계를 바꾸므로 제외한다.
- 전체 버전 업그레이드: credential 필드 전달 해결을 확인하지 못했고 변경 범위가 커 제외한다.
- 기존 image의 API만 한정 수정: 고정 base·원본 SHA 검사와 route 회귀 검증으로 범위를 제한한다.

## 결정과 이유

현재 API의 네 외부 호출에 UTF-8 BasicAuth header를 전달하고 exemplar의 password 선택을 추가하는 패치를 제안한다. frontend·DB schema·runtime dependency·TLS·권한은 유지한다. 새 CI 대신 기존 hopcharlie-app-build의 buildspec override를 준비한다. 사용자 요청으로 로컬 컨테이너·추가 로컬 테스트는 수행하지 않는다.

## 영향과 후속 작업

compiled 파일에 종속되므로 원본 SHA·교체 횟수·문법이 일치하지 않으면 중단한다. 원격 CodeBuild에서 실제 patched route 성공과 원본 실패를 검증해야 한다. 이미지 게시 위치·digest·실제 Deployment 변경은 별도 승인 전 실행하지 않는다. 운영401이 해결되었다고 보고하지 않는다. 배포 시 복구 대상은 HyperDX image digest 하나다.

## 관련 자료

- [패치 및 원격 검증 준비](../../images/hyperdx-prometheus-auth/README.md)

원격 검증 결과: CodeBuild42019641-7966-41f1-b967-30c2bb57a48b에서 patched17pass/0fail, 원본5pass/12fail을 확인했다. 로컬컨테이너실행 없이 원격 image build와 실제 compiled route를 검증했다. 게시·배포는 미실행이다.

게시 결과와 확정 digest는 [패치 안내](../../images/hyperdx-prometheus-auth/README.md#게시-완료배포-대기)에 기록했다. 관리 values만 image를 바꾸며 기존 default upstream base와 데이터 구성 요소는 유지한다.
