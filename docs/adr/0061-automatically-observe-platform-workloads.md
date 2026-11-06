# 0061. 플랫폼 배포 계약에 관측 자동 연결을 포함한다

- 날짜: 2026-11-06
- 상태: 제안 — v3 계획 독립 재리뷰 GREEN·사용자 개발 승인 전
- 관련 ADR: [0050 ClickStack](0050-adopt-clickstack-for-hackathon-observability.md), [0060 사설 경로](0060-private-otlp-ingress-for-aws-workload.md)

## 배경과 제약

플랫폼이 새 앱/서비스/DB/작업을 생성하는데 수집기에 앱 이름을 수동 등록하는 계획은 자동 배포 목적에 맞지 않는다. 사용자는 CNPG와 runner 로그도 포함하고 새 앱 배포 시 자동 연결하도록 계획 재작성을 요청했다. 기존 두 서비스 고정 경로·모임룸 namespace만의 수신 Secret 복사 계획은 미채택으로 철회한다.

## 검토한 대안

- 앱별 경로/토큰/PodMonitor 수동 등록: 향후 모든 배포마다 작업이 필요해 제외.
- alpha receiver_creator/k8s_observer: 고정 observer가 Running이 아닌 Pod를 제외하므로 즉시 종료 Job의 기본 경로로 제외.
- filelog로 읽고 metadata가 없으면 drop: checkpoint가 먼저 전진해 첫 로그가 누락될 수 있어 제외.
- Alloy1.21.3/chart1.15.0의 Kubernetes discovery→소유 target relabel→파일 reader→OTLP 전송: 선택 제안. metadata가 늦으면 파일 reader 생성도 늦어지며 첫 위치부터 읽는다. discovery/file/conversion은 GA 구성 요소를 사용하며, 영속 queue의 `otelcol.storage.file`은 public-preview 예외다. stability.level=public-preview와 pin·config validate·단기 Job/positions/queue 재시작 proof를 B 선행 관문으로 두고 사용자에게 preview 위험을 명시한다.
- 모든 바이너리 강제 자동 계측: runtime별 차이와 eBPF 특권이 있어 제외. 연결 설정은 자동으로 주입하고 내부 span 지원은 구분한다.

## 결정 제안과 이유

공통 app namespace/Pod, CNPG inherited metadata, 플랫폼 Job template에 관측 소유/분류 라벨을 붙인다. Alloy의 node-local Kubernetes pod discovery가 소유 target을 확정한 뒤 해당 UID/container 파일만 읽는다. 최초 위치부터 읽고 완료 Job Pod를 최소1시간 보존하며 positions/queue를 유지한다. 비소유 파일의 reader/positions가 생성되지 않는 것과 metadata 지연 중 첫 로그가 보존되는 것을 검증한다. 중앙 전송·저장소·콘솔은 기존 OTel gateway/ClickStack이다.

모든 지원 환경의 새 앱에 endpoint/protocol/service/version/public CA를 공통 chart로 자동 연결한다. 앱별 수동 Secret을 없애기 위해 local app listener는 TLS+소유 namespace/Pod ingress 정책을 쓰고, agent bearer 및 중앙 upstream bearer와 분리한다. 기존 obs allow-all은 이 listener의 차단을 우회하지 않도록 workload baseline의 compatibility 대상에서 gateway Pod만 제외하고 그 Pod의 두 listener·기존 health/metrics에 전용 ingress 정책을 적용해야 한다. 다른 관측 Pod의 호환 정책은 유지한다. management 정책은 실제 inventory로 별도 확인한다. 이는 강한 멀티테넌트 인증이 아닌 팀 내부 trust boundary다.

새 CNPG에는 같은 chart가 PodMonitor를 만들고 workload 최소 Prometheus가 label selector로 자동 발견한다. 새 DB마다 query connection을 추가하지 않는다. 모임룸 HTTP SDK를 구현하며 SDK 없는 Docker Doom 등의 내부 trace는 완료로 보고하지 않는다.

## 영향과 후속 작업

- 효과: 새 앱/서비스·DB·Job 추가 후 collector ConfigMap/토큰/monitor를 수동 갱신하지 않는다.
- 위험: 공통 chart·정책·CA/앱 rollout, API metadata 권한·노드 로그 일시 접근, 짧은 Job/metadata race·로그 회전/초기 읽기·추가 Prometheus 운영과 스토리지 비용이 생긴다. SQL 전문 로깅은 켜지 않는다.
- 검증: 처음 보는 앱 이름, 새 service/replica/DB, 즉시 종료 Job, metadata 지연·agent 재시작, 비소유·보호 namespace 미전송, DB metrics와 실제 app log↔trace, 삭제 후 stale target 부재가 완료 기준이다.
- 첫 적용은 AWS에 한정한다. GCP/온프레미스·기존 수동 runner 생성 방식 변경·DB topology·자동 eBPF 주입·CI 신설·노드 자동 증설은 포함하지 않는다.
- [v3 계획](../plans/workload-observability-expansion.md) 독립 리뷰/사용자 개발 승인 후 코드·PR 준비, 실제 권한/데이터·비용·정책 diff를 제시한 C 승인 후 실행한다.

## 관련 자료

- [filelog0.161](https://github.com/open-telemetry/opentelemetry-collector-contrib/blob/v0.161.0/receiver/filelogreceiver/README.md)
- [k8sattributes0.161](https://github.com/open-telemetry/opentelemetry-collector-contrib/blob/v0.161.0/processor/k8sattributesprocessor/README.md)
- [CNPG1.30 logging](https://github.com/cloudnative-pg/cloudnative-pg/blob/release-1.30/docs/src/logging.md), [monitoring](https://github.com/cloudnative-pg/cloudnative-pg/blob/release-1.30/docs/src/monitoring.md)
- [공통 앱 chart](../../helm/charts/hopcharlie-app/), [플랫폼 Job template](../../helm/charts/hopcharlie-platform/templates/hopai.yaml)

준비 PR에서는 모든 activation을 off로 유지한다. C 승인 후 별도 activation PR 머지에 따른 자동 rollout까지 실행 승인에 포함한다. 공개 CA는 환경 profile의 PEM bundle에서 신규 namespace ConfigMap으로 자동 공급하며 교체 checksum으로 기존 앱도 갱신한다.

수집 agent 제안: [Alloy1.21.3](https://github.com/grafana/alloy/releases/tag/v1.21.3) / chart1.15.0, GA [pod discovery](https://grafana.com/docs/alloy/latest/reference/components/discovery/discovery.kubernetes/)→소유 target relabel→[file source](https://grafana.com/docs/alloy/latest/reference/components/loki/loki.source.file/)→[OTel 변환](https://grafana.com/docs/alloy/latest/reference/components/otelcol/otelcol.receiver.loki/)→기존 gateway. 새 agent는 기존 agent와 교체하고 Loki 저장소를 배포하지 않는다.

[Alloy1.21.3 storage.file 상태](https://github.com/grafana/alloy/blob/v1.21.3/docs/sources/reference/components/otelcol/otelcol.storage.file.md)는 public-preview이며 전체 agent가 GA-only라는 뜻이 아니다. B 코드 준비 승인과 C 실행 승인에 이 예외를 포함한다.
