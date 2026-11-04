# Charlie ClickStack chart

ClickStack Helm chart 3.5.0의 리소스 템플릿과 기본 구성을 기반으로 한 로컬 배포 chart다. 실제 Secret 생성이나 클러스터 설치는 하지 않는다.

- 원본: https://github.com/ClickHouse/ClickStack-helm-charts/releases/tag/clickstack-3.5.0
- 원본 archive SHA256: `a8a781cf8fb0c272351c39daf01ced7db85769eacd6f92fc18897b85f451513a`
- HyperDX/ClickStack Collector: `2.39.1`, 이미지 digest는 `helm/images.lock.json` 참조
- API·앱·Collector 소스 조사: https://github.com/hyperdxio/hyperdx/commit/6db385c
- ClickHouse operator: `v0.0.7`, MongoDB operator: `1.7.0`

## 보완 사항

1. Secret 생성 템플릿 두 개를 제거했다. runtime·DB·datasource·TLS는 기존 Secret 이름/key만 참조한다. 기본 비밀번호나 Mongo 접속 URI를 ConfigMap·Deployment 문자열에 렌더링하지 않는다.
2. HyperDX는 required secretKeyRef로 세션·토큰 암호화 키·Mongo URI를 받는다. UI가 개발용 기본 session secret으로 조용히 돌아가지 않는다.
3. ClickHouse 사용자는 Secret 기반 env와 `password: [{ '@from_env': ... }]`를 사용한다. `grants.query` 배열, 읽기 profile과 별도 ingest 계정을 사용한다. 고정 operator의 CRD와 설정 코드를 조사했으며 reconciliation·DB 인증은 실행 단계 검증이다.
4. MongoDBCommunity는 기존 passwordSecretRef와 명시적 영속 data/log 볼륨을 사용한다. 원본의 MongoDB 5.0 기본값 대신 신규 설치용 8.0.17 이미지 digest를 선택했다. 기존 DB 업그레이드가 아니며 HyperDX·operator와의 실제 조합은 배포 전/후 관문이다.
5. 중앙 Collector는 공식 배포 이미지의 standalone entrypoint를 직접 실행한다. 범용 OTel subchart가 붙이는 relay.yaml이 공식 기본 파이프라인을 덮어쓰지 않도록 중앙 Deployment를 작게 분리했다. 추가 chart dependency는 없다.
6. OTLP는 HTTPS·bearer 인증으로 받는다. `additionalIngestTokenKeys`로 collector Secret의 클러스터별 추가 토큰 key를 참조할 수 있다. 인증 토큰을 입력 데이터의 cluster 속성에 암호학적으로 결합하거나 프로젝트별 테넌트 격리를 제공하지는 않는다. 팀 내부 신뢰 경계다.
7. 중앙 queue는 PVC, agent checkpoint는 노드 디스크, gateway queue는 별도 PVC에 둔다. 중앙 메모리 제한과 batch 상한을 명시했다. 런타임 손실·재시작 동작은 아직 검증하지 않았다.
8. HyperDX external Prometheus 플래그를 켰고 실제 UI 동작은 배포 검증 대상으로 남긴다. 고정 소스는 next-runtime-env와 refresh-env.js를 사용하므로 이번 구성에는 커스텀 프론트 이미지 빌드가 필요하지 않다. 공개 변수에는 boolean 플래그만 넣는다.
9. 메트릭 수집/ClickHouse remote write와 세션 replay 수집은 활성화하지 않는다. 공식 Collector의 schema migration이 빈 metrics/session 테이블을 만들 수 있으나 해당 신호를 적재한다는 뜻은 아니다.

`ci/connections.json`과 `ci/sources.json`은 **합성 테스트 fixture**다. fixture 암호를 실제 Secret에 사용하지 않는다. source 연결은 이름으로 연결하고 로그·트레이스 간 참조도 이름으로 초기화한다. 이미 존재하는 팀의 source를 자동으로 갱신하는 설정으로 간주하지 않는다.

원본 갱신 시 위 보완과 `scripts/check-clickstack.py`를 다시 검증한다. 이 chart를 운영 배포용으로 사용하기 전 `docs/clickstack-deployment-review.md`의 미확정 항목을 완료해야 한다.
