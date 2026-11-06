# HyperDX Prometheus BasicAuth 수정

고정 HyperDX2.39.1의 외부 Prometheus 요청 4곳에 저장된 connection credential을 BasicAuth header로 전달한다. exemplar 요청은 password도 선택한다. 기존 TLS·팀별 연결 조회·ClickHouse 경로를 유지한다. 원본 compiled API SHA와 일치하지 않으면 빌드가 실패한다.

새 workflow나 CodeBuild 프로젝트는 만들지 않는다. 기존 `hopcharlie-app-build`에 전달할 buildspec만 생성한다:

```sh
python3 images/hyperdx-prometheus-auth/render-buildspec.py > /tmp/hyperdx-auth-buildspec.json
```

출력은 공개 패치/테스트만 포함하며 AWS 호출·로컬 컨테이너 실행을 하지 않는다. 실제 StartBuild는 실행 승인 후 수행한다. CodeBuild에서 patched image의 실제 API route를 시험하고 원본 image에서 회귀 테스트 실패를 확인한다. image push·registry token 발급·클러스터 배포는 포함하지 않는다.

현재 상태: 코드 작성, 실제 공개 이미지의 API 파일에서 패치 적용·문법 확인 완료. 기존 CodeBuild 원격 검증에서 이미지 빌드와 실제 route 테스트17개가 모두 통과했다(build42019641-7966-41f1-b967-30c2bb57a48b). 원본 image에서는 인증 회귀12개가 실패하고 보호경계5개가 통과해 결함을 재현했다. 운영 이미지 게시/교체는 아직 하지 않았으며 운영401은 아직 해결되지 않았다. 로컬 컨테이너 및 추가 로컬 테스트는 실행하지 않는다.
