# 0019. Prometheus 기본 control-plane scrape용 ServiceAccount 토큰의 승인 경계

- 날짜: 2026-11-03
- 상태: 제안 — 추가 권한 승인 전, 토큰 생성 미실행
- 관련 ADR: [0018 한정 모니터링 설치](0018-stage-minimal-management-monitoring.md)

## 배경과 제약

고정 kube-prometheus-stack91.8.2 렌더링에서 `observability/monitoring-prometheus-token`이 `kubernetes.io/service-account-token`으로 생성됨을 확인했다. chart 기본 `prometheus.serviceAccount.createTokenSecret=true`이고 기본 control-plane ServiceMonitor authorization이 이 Secret을 참조한다. 원래 승인에는 Grafana 관리자 자격 증명과 내부 TLS/설정 Secret만 명시했으므로, 새 장기 API 토큰은 추가 승인 대상으로 분리했다. 실제 Secret/토큰은 생성하지 않았다.

## 검토한 대안

- 기본 chart 관리 토큰: 현재 선언과 수집 경로를 유지하지만 만료 없는 자격 증명이 Secret에 남는다. 추가 명시적 승인을 조건으로 제안한다.
- TokenRequest 기반 단기 토큰 갱신: 정기 갱신·Secret 교체 자동화가 추가되어 별도 운영 설계·권한 검토가 필요하다.
- 인증 대상 수집 비활성화/설정 교체: 장기 토큰을 피할 수 있으나 API server·kubelet 수집 범위나 인증 경로를 바꾸므로 별도 설계·검증이 필요하다.
- kube-prometheus-stack 보류: metrics-server 두 개만 설치하고 관측 스택 인증 방식을 별도로 정한다.

## 결정과 이유

추가 승인 시에만 chart가 `monitoring-prometheus` ServiceAccount용 장기 토큰 Secret 하나를 생성하도록 허용한다. 현재 렌더링된 동일 이름 ClusterRole/Binding의 권한은 전체 namespace의 nodes·nodes/metrics·services·endpoints·pods·endpointslices·ingresses 조회(get/list/watch), /metrics 및 활성화된 metrics 하위 경로 get이다. Secret 값 조회·리소스 변경·cluster-admin을 이 토큰에 부여하지 않는다. 기존 계획에 승인된 다른 스택 리소스 범위를 늘리지 않는다.

토큰 원문을 직접 조회/출력/로컬 파일/Git/문서에 저장하지 않는다. Pod/Operator가 chart의 참조대로 Kubernetes 내부에서 사용한다. 자동 expiry/rotation이 없음을 사용자에게 명시하고, 미승인 또는 답변 불명확 시 monitoring sync를 시작하지 않는다.

## 영향과 후속 작업

- 기대 효과: 고정 chart의 control-plane 수집 설정을 변경하지 않고 설치를 이어갈 수 있다.
- 위험: 토큰이 Secret에 장기 보존되며 자동 만료되지 않는다. 유출 시 위 읽기 권한이 지속된다. 현재 prune=false이므로 앱/values 제거만으로 토큰이 폐기된다고 가정하지 않는다.
- 후속 작업: 새로운 plan-reviewer 검토 → 대상/목적/권한/영향을 명시한 사용자 승인 → 기존 계획의 수동 monitoring sync. 운영 종료·유출 대응 시 해당 Secret 삭제/회전은 별도 실행 승인으로 수행한다. 미승인 시 metrics-server만 완료하고 이 앱은 보류한다.

## 관련 자료

- [설치 계획](../plans/metrics-and-management-monitoring.md)
- [Kubernetes ServiceAccount 수동 토큰](https://kubernetes.io/docs/tasks/configure-pod-container/configure-service-account/#manually-create-a-long-lived-api-token-for-a-serviceaccount)
- chart91.8.2 `templates/prometheus/serviceaccount-token-secret.yaml`, `templates/prometheus/clusterrole.yaml`, `values.yaml`의 createTokenSecret/authorization
