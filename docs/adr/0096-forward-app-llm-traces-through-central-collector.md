# 0096. 앱 LLM 관측은 기존 OTel 경로에서 전용 Langfuse 프로젝트로 분기한다

- 날짜: 2026-11-08
- 상태: 채택 — 사용자가 Langfuse 관측 활성화를 요청함. 실제 적용 결과는 별도 기록
- 관련 결정: [0087 hopai 관측](0087-connect-hopai-langfuse-with-scoped-project-key.md)

## 상황

`moimroom-mig`가 실제로 AI를 사용해도 플랫폼 앱 관측 설정과 앱 generation 계측이 없었다. hopai의 계획·리허설 관측을 앱의 추론으로 표시할 수 없다. Langfuse는 관리 EKS의 내부 Service이며 GKE·온프레 앱에서 직접 접근할 공용 경로가 없다. 기존 앱 OTel 전송은 환경별 수집기를 통해 중앙 ClickStack collector에 도달한다.

## 결정

기존 trace 수집·ClickHouse 파이프라인을 보존하고 중앙 collector에 별도 `traces/langfuse-app` 파이프라인을 추가한다. `k8s.namespace.name=moimroom-mig`와 `service.name=moimroom-mig-web`가 정확하게 일치한 trace만 전용 앱 프로젝트로 전송한다. 누락·다른 값·필터 평가 오류는 허용하지 않는다. generation의 HTTP 부모를 포함한 전체 앱 trace를 보존하고, 플랫폼 API는 generation마다 `hopcharlie.app=moimroom-mig`가 있는지 별도로 검증한다. resource 속성은 기존 전송 경계에서 받은 값이며 이를 독립적인 암호학적 workload identity라고 주장하지 않는다.

앱 프로젝트 `cmw76sgdx000bzk08z3ez9xsq`는 hopai 관측 프로젝트와 GPU 품질 평가 프로젝트에서 분리한다. 기존 관리자 로그인 경계에서 생성한 서로 다른 두 project key를 중앙 collector와 플랫폼 reader에 각각 저장한다. collector는 `observability/moimroom-mig-langfuse-exporter-auth`의 `authorization`을 Secretref env로 받는다. 키는 앱이나 프론트에 배포하지 않는다. 현재 Langfuse project key는 read-only role을 강제하지 않으므로 reader 키도 해당 프로젝트에 쓰기 권한이 있다는 한계가 남는다.

collector는 Langfuse 분기에서 span/resource 속성을 허용 목록으로 제한한다. 실제 모델명·토큰·backend·앱 scope와 환경 정보만 유지하고 prompt/output·헤더·URL·일반 임의 metadata는 제외한다. span 이름은 알려진 request/generation 값으로 고정하고 status message·trace state·events·links를 비운다. instrumentation scope 이름도 고정하고 그 속성과 version을 비운다. 다른 ClickStack 파이프라인의 수집 내용은 바꾸지 않는다. 전송은 5초 제한, 256개 영속 큐, 재시도 최대 60초를 사용하며 강제 종료·장기 장애에서 유실이 없다고 보장하지 않는다. 관측 exporter 실패는 앱 요청의 재실행이나 추가 모델 호출을 하지 않는다.

기본 opt-in은 OFF, management 값만 ON이다. collector Secretref·전용 exporter·정확한 TCP3000 egress와 Langfuse ingress 외에 IAM·Argo 프로젝트 권한·공용 listener를 추가하지 않는다. 변경된 설정은 실제 렌더된 collector 설정 전체의 checksum으로 재시작한다. 값이 같아도 필터·변환 template 내용이 바뀌면 checksum이 바뀐다. 단일 collector의 Recreate 중 수신이 일시 중단되며 기존 수집기의 큐·재시도에 의존한다. 실제 적용 전 config validation과 영향 검증을 수행한다.

## 검토한 대안

- 앱에 Langfuse 키와 직접 HTTPS 경로 추가: 클라우드/온프레별 새 연결·Secret 배포가 필요해 제외했다.
- generation만 중앙에서 전달: HTTP 부모가 빠져 trace 구조가 불완전해질 수 있어 제외했다.
- hopai 또는 품질 평가 프로젝트 재사용: 서로 다른 서비스 관측과 신뢰하는 평가 score 경계가 섞여 제외했다.
- HyperDX HTTP span으로 토큰·품질 추정: 실제 모델 응답 정보와 평가가 아니므로 제외했다.

## 검증과 한계

실제 Langfuse OTLP mapping probe에서 `langfuse.observation.metadata.hopcharlie.app`는 API metadata의 평면 `hopcharlie.app` 키로 저장됐다. probe 값은 `langfuse-mapping-check`여서 앱 API 조회 범위에 포함되지 않으며 실제 앱 추론으로 표시하지 않는다. 실제 모델 호출은 없었다. 전용 reader key의 `/projects` 응답은 정확한 앱 프로젝트와 일치한다. 적용 전 앱의 실제 generation은 0건이었다.

실제 운영 collector 바이너리로 렌더된 새 설정의 `validate`를 실행해 exit0을 확인했다. 이는 설정 수용의 증거이며 실제 앱 요청의 수신·전달 성공은 별도 확인한다. 로컬 단위 테스트는 기본 OFF, 기존 로그/trace 파이프라인 보존, 정확한 identity 필터·egress, Secret 분리, 원문 차단과 위험한 설정 거부를 검증한다. 온프레 장치가 offline이면 실제 앱 추론 관측은 확인할 수 없으며 빈 값을 0 토큰·0 비용·AI 미사용으로 바꾸지 않는다.

## 관련 파일

- [중앙 collector 설정](../../helm/charts/clickstack/templates/otel-collector/custom-config.yaml)
- [collector 배포](../../helm/charts/clickstack/templates/otel-collector/deployment.yaml)
- [Langfuse egress](../../helm/charts/clickstack/templates/otel-collector/langfuse-policy.yaml)
- [Langfuse ingress](../../clusters/aws-dev-management/manifests/langfuse-app-ingress.yaml)
