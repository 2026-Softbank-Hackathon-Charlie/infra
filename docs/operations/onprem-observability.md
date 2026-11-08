# 온프레미스 관측 준비

현재 onprem 앱 연결 과정은 Alloy·OTel·Prometheus를 설치하지 않는다. 다음 명령은 로컬 values만 만든다.

```sh
python3 scripts/render-onprem-observability.py --output-dir /tmp/onprem-observability-proposal
```

검토한 고정 차트의 로컬 디렉터리가 있으면 실제 manifest까지 생성한다. 차트 이름·버전을 계약과 비교하며 다운로드나 클러스터 API 접근을 하지 않는다. 차트 취득은 기존 `scripts/check-clickstack.py`의 고정 버전/체크섬 경로를 사용할 수 있다.

```sh
python3 scripts/render-onprem-observability.py \
  --output-dir /tmp/onprem-observability-proposal \
  --alloy-chart /tmp/charts/alloy \
  --otel-chart /tmp/charts/opentelemetry-collector \
  --prometheus-chart /tmp/charts/kube-prometheus-stack
```

`contract.json`의 requiredPreparation과 렌더된 RBAC/hostPath/PVC를 함께 검토한다. 중앙 OTLP·역방향 metrics query의 인증 TLS 경로/ACL, collector Secret·CA, CRD·namespace·노트북 저장소/메모리를 준비하고 별도 승인해야 한다. `observability-remote-endpoint` ConfigMap도 승인된 경로에 맞춰 준비해야 한다. 기본 local-path는 단일 노드 디스크이며 고가용성 저장소가 아니다.

기존 앱 등록 credential의 제한을 변경하지 않는다. values 생성이나 Pod Ready만으로 관측 동등성을 통과시키지 않는다. 설치 뒤 앱 프로필의 공개 CA/endpoint와 플래그를 설정하고 정확한 cluster·namespace·service 및 최신 수신을 검증한다. 로그는 Alloy, trace는 앱 SDK, CNPG metrics는 PodMonitor가 각각 필요하다. Langfuse 프로젝트 연결은 별도 API 설정이다.

플랫폼의 앱별 requiredSignals는 먼저 audit로 확인한다. 원본·대상 모두 일치하는 최신 수신이 있을 때만 enforce를 선택적으로 켠다. 표본 없음은 0 손실이나 AI 미사용이라는 의미가 아니다.

## 승인된 대상에 설치하기

사용자의 실제 적용 요청 후에도 기존 노트북 연결을 먼저 확인한다. 2026-11-08 점검에서는 `hop-onprem-laptop` 및 그 앱/DB/MinIO peers가 offline이었으며 마지막 연결은 01:20:00.1 UTC였다. 별도 online VM을 자동 대체하지 않는다. 이 상태에서 설치나 수신 성공을 기록하지 않았다.

`scripts/apply-onprem-observability.py`는 명시한 kubeconfig/context와 별도로 확인한 kube-system UID에 설치 대상을 고정한다. 로컬 고정 차트와 관리자 권한을 사용한다. 먼저 namespace/RBAC·노드 용량, CRD, 인증 Secret 7개, 공개 CA/leaf, 중앙 transport/ACL 및 reverse metrics query 경로를 위 계약대로 준비한다. namespace는 node-log agent를 위한 privileged PSA가 필요하다. existing TLS 검증을 끄거나 노트북 대신 다른 서버를 지정하지 않는다.

```sh
python3 scripts/apply-onprem-observability.py \
  --kubeconfig /secure/reviewed-onprem.kubeconfig --context reviewed-onprem \
  --expected-kube-system-uid '<별도 확인한 대상 UID>' \
  --alloy-chart /tmp/charts/alloy \
  --otel-chart /tmp/charts/opentelemetry-collector \
  --prometheus-chart /tmp/charts/kube-prometheus-stack
```

기본은 전체 server dry-run이다. 확인된 동일 명령에 `--execute`를 추가하면 직전 UID를 재검사하고 적용한다. 인증 Secret 값·API 응답·오류 본문은 출력하지 않으며 토큰 발급/복사, IAM, CRD 설치, tailnet 변경은 하지 않는다. Secret·transport가 없으면 적용 전에 실패한다. 적용 중 실패하면 일부 자원이 남을 수 있다. 무조건 재시도하거나 전체 namespace를 삭제하지 않고 정확한 자원 상태부터 확인한다.

설치 뒤에는 Prometheus Ready/target up, gateway/Alloy 큐·export 실패, 합성 nonce 요청의 중앙 ClickHouse 수신을 확인한다. 앱 SDK trace와 stdout 로그, CNPG metrics를 각각 정확한 onprem identity와 최신 시간으로 조회한다. `receivingVerified=false`는 이 후속 검증을 설치 도구가 대신하지 않음을 뜻한다. 업무 원문·Secret·헤더를 증거로 저장하지 않는다. 전체 동등성 enforce는 원본·대상에서 같은 필수 신호를 확인한 다음 선택한다.
