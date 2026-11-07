# HyperDX Prometheus BasicAuth 수정

고정 HyperDX2.39.1의 외부 Prometheus 요청 4곳에 저장된 connection credential을 BasicAuth header로 전달한다. exemplar 요청은 password도 선택한다. 기존 TLS·팀별 연결 조회·ClickHouse 경로를 유지한다. 원본 compiled API SHA와 일치하지 않으면 빌드가 실패한다.

새 workflow나 CodeBuild 프로젝트는 만들지 않는다. 기존 `hopcharlie-app-build`에 전달할 buildspec만 생성한다:

```sh
python3 images/hyperdx-prometheus-auth/render-buildspec.py > /tmp/hyperdx-auth-buildspec.json
```

출력은 공개 패치/테스트만 포함하며 AWS 호출·로컬 컨테이너 실행을 하지 않는다. 실제 StartBuild는 실행 승인 후 수행한다. CodeBuild에서 patched image의 실제 API route를 시험하고 원본 image에서 회귀 테스트 실패를 확인한다. image push·registry token 발급·클러스터 배포는 포함하지 않는다.

현재 상태: 코드 작성, 실제 공개 이미지의 API 파일에서 패치 적용·문법 확인 완료. 기존 CodeBuild 원격 검증에서 이미지 빌드와 실제 route 테스트17개가 모두 통과했다(build42019641-7966-41f1-b967-30c2bb57a48b). 원본 image에서는 인증 회귀12개가 실패하고 보호경계5개가 통과해 결함을 재현했다. 운영 이미지 게시/교체는 아직 하지 않았으며 운영401은 아직 해결되지 않았다. 로컬 컨테이너 및 추가 로컬 테스트는 실행하지 않는다.


## 게시 완료·배포 대기

- 검증 소스: `91eaba5cf7d519c66a509d5dc66670badf1d99ec`
- 게시 빌드: `8715c740-30eb-440a-b758-4b8a937e37e8`, 성공. 패치17/17pass, 원본5pass/12fail.
- 확정 image: `525091405886.dkr.ecr.ap-northeast-2.amazonaws.com/hopcharlie/apps/hyperdx-prometheus-auth:2.39.1-prom-auth-91eaba5cf7d5@sha256:d97c85f1393e69f5b77e18a44369298c53a16208419d62382288bbec02eabf85`
- 기존 CodeBuild 역할에서 허용하는 BatchGetImage로 조회했고 IAM 변경은 하지 않았다. 첫 게시 시 DescribeImages 권한 부족으로 중단된 뒤 기존 허용 API로 수정해 두 번째 게시 빌드에서 성공했다.
- management override와 image lock에만 digest를 반영했다. 사용자 PR122 merge 전 운영 image를 변경하지 않는다.
- merge 후 기존 manual ClickStack Application의 Git ref를 merge SHA로 바꾸고 HyperDX Deployment1개만 선택 sync한다. 기존 source/policy/image를 비밀값 없이 기록하고, 실패 시 원래 source로 Deployment1개만 복구한다. root/collector/DB/PVC는 전체 sync하지 않는다. 실제 HyperDX vector(1)·label 조회와 기존 source가 정상임을 확인하기 전 운영401이 해결됐다고 보고하지 않는다.
