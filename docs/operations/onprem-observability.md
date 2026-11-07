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
