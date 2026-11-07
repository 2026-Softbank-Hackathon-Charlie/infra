# 0092. 온프레미스 관측 수집기 준비와 환경 출처 구분

- 상태: 제안(로컬 준비·검증 구현, 운영 설치 미승인·미적용)
- 날짜: 2026-11-08

## 배경

승인된 온프레미스 메타데이터 조회 당시 `observability` namespace와 표준 수집기 리소스가 없고, `moimroom-mig`·`hello-onprem` Deployment의 OTEL 환경변수도 없었다. 중앙 스택 실행만으로 해당 환경의 수신을 증명하지 못한다. 중앙 저장소의 온프레미스 수신 건수는 미측정이다. 앱 등록 권한은 노드 로그 수집 권한과 별개다.

## 제안

- 온프레미스 계약은 enabled=false, 실행 승인 필수로 유지한다. 로컬 renderer가 기존 GKE의 고정 Alloy·OTel·Prometheus 설정을 복사해 온프레미스 identity와 local-path 저장소로 변환한다. 이미지 digest, TLS·인증 Secret 참조, redaction, 영속 큐·재시도·자원 제한을 유지한다. Secret 값을 받거나 생성하지 않는다.
- 명시적인 로컬 차트의 이름·고정 버전을 확인한 후 values와 manifest를 렌더한다. 클러스터/클라우드 API, 자동 차트 다운로드, 자동 Argo Application, enrollment 권한 확대는 수행하지 않는다. CRD 설치도 별도 준비 대상이다.
- 배포 stage `deployment.environment.name=dev`와 실제 실행 환경을 분리한다. 앱 chart에 `hopcharlie.environment.name`을 추가하고 workload collector에서 gke/eks/onprem을 upsert한다. 플랫폼 AI 지도는 이 속성을 우선 읽으며 기존 stage는 유지한다.
- CNPG PodMonitor와 온프레미스 추가 scrape에 environment/cluster target label을 추가한다. externalLabels만으로 로컬 PromQL 선택자를 바인딩할 수 없으므로 실제 시계열에 label을 붙인다.

## 승인·활성화 경계

기존 앱 등록 credential의 observability namespace 제한을 우회하지 않는다. 설치에는 렌더된 Namespace/CRD/RBAC/DaemonSet hostPath/PVC, CA·auth Secret, 양방향 인증 TLS transport·tailnet ACL, 앱 프로필 및 노트북 용량을 검토하고 별도 승인해야 한다. 중앙 OTLP 및 reverse metrics query 경로는 아직 준비되지 않았다. TLS 검증을 끄거나 공용 listener를 만들지 않는다.

수집기가 있어도 SDK 없는 앱의 trace는 생기지 않는다. 앱 chart는 trace exporter를 연결하고 logs/metrics SDK exporter는 none으로 유지한다. stdout 로그는 Alloy, CNPG metrics는 PodMonitor가 수집한다. 실제 SDK 사용과 service identity는 설치 뒤 검증한다. local-path는 단일 노드 저장소이므로 노트북 종료·디스크 손실에 대한 queue 내구성 한계가 있다.

## 대안과 검증

등록 스크립트에 관리자 권한·Secret 생성을 묶으면 기존 연결 승인 범위를 넘는다. GKE 설정을 그대로 설치하면 잘못된 cluster identity와 GCP storageClass로 관측이 섞인다. 전체 동등성 강제 전환 대신 플랫폼의 앱별 requiredSignals opt-in을 사용한다.

고정 차트를 실제 렌더해 수집기/Prometheus/RBAC/PVC, 인증 참조·label·기본 비활성 상태를 테스트한다. 렌더는 운영 설치·수신·무손실 전달 증명이 아니다. 승인된 설치 후 원본·대상 동일 앱의 최신 logs/traces/metrics 수신을 확인한 뒤 필요한 앱에만 enforce를 활성화한다. Langfuse 연결은 별도 프로젝트/API 설정이다.
