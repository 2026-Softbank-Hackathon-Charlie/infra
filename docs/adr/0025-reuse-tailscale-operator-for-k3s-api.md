# 기존 Operator의 K3s 전용 egress

- 날짜: 2026-11-07
- 상태: 운영 식별정보가 없는 재사용 참고 문서

## 배경과 제약

독립 VM workload를 기존 관리 계층에 연결하는 범위를 단순화하고 운영 정보의 공개를 피한다. 실제 환경·실행 기록은 이 공개 문서에 넣지 않는다.

## 검토한 대안

기존 target 교체·관리자 자격 공유·관리 도구 중복 설치보다 대상과 소유권을 분리하는 방식을 사용한다.

## 결정과 이유

기존 Tailscale Operator의 단일 대상 전용 egress를 재사용한다. 소비자 selector·TCP6443에 맞는 새 정책을 추가하고 기존 DB proxy/Operator credential/ACL은 변경하지 않는다. 새 proxy의 상태 Secret·tailnet node 생성은 명시적 승인 범위다. 원래 TLS serverName과 CA 검증을 유지한다.

## 영향과 후속 작업

권한 작업은 구체적 승인 뒤 수행하고 성공/실패·정리·만료/복구를 구분해 검증한다. 이 공개 template의 존재만으로 실제 배포나 승인 완료를 뜻하지 않는다.

## 관련 자료

- [공개 연결 절차](../plans/onprem-k3s-management-connection.md)
