# 0025. 이미지 게시 workflow는 infra PR 대신 infra main에 바로 커밋한다

- 날짜: 2026-11-03
- 상태: 제안
- 관련 ADR: [ADR 0024](0024-publish-images-from-github-actions.md)(infra PR과 사람 머지 부분을 대체), [ADR 0016](0016-run-hopcharlie-platform-on-management-cluster.md)

## 배경과 제약

- ADR 0024에 따라 platform과 frontend의 workflow는 이미지를 올린 뒤 infra PR을 열고, 사람이 머지했다. workflow도 손으로 실행했다.
- 해커톤 기간에 두 저장소의 main은 하루 수십 번 바뀐다. push마다 workflow 실행과 PR 머지를 사람이 하면, 서버에 반영되는 버전이 main보다 계속 늦고 담당자가 그 일에 묶인다.
- workflow가 바꾸는 것은 플랫폼 values의 이미지 한 줄과 `images.lock.json`의 해당 항목뿐이다. 둘은 workflow가 같은 digest로 함께 만들어서 서로 어긋날 수 없다.
- 조직은 Free 플랜이고 저장소가 비공개라 브랜치 보호와 PR 자동 머지 조건을 쓸 수 없다.
- 2026-11-03 오후에 사용자와 팀이 이 방식(B안)을 고르고, 동윤 님과 종욱 님께 알리기로 했다.

## 검토한 대안

- 지금처럼 사람이 PR을 머지하기: 리뷰는 남지만 push 수만큼 클릭이 필요하다.
- workflow가 PR을 열고 infra CI가 통과하면 스스로 머지하기: PR 기록과 CI 확인이 남지만 반영이 3~5분 늦고, 콘솔과 플랫폼 PR이 같은 `images.lock.json` 끝부분을 바꿔 자주 충돌한다. 실제로 #20과 #21이 다른 PR 머지 뒤 충돌해 다시 만들었다.
- Argo CD Image Updater로 ECR을 보게 하기: 클러스터에 컴포넌트와 ECR 읽기 권한을 더해야 하고, infra Git이 원하는 상태라는 원칙에서 벗어난다.
- `latest` 같은 움직이는 태그를 쓰기: 무엇이 떠 있는지 알 수 없고, 태그 내용이 바뀌어도 Argo CD가 다시 배포하지 않는다.

## 결정과 이유

- 두 workflow는 main에 push될 때 자동으로 실행된다. platform은 main push의 `ci`가 성공한 뒤(`workflow_run`), frontend는 push 직후 자체 테스트를 거친 뒤 게시한다. 새 push가 오면 진행 중인 이전 실행을 취소해 마지막 커밋만 게시한다.
- 게시한 이미지는 PR 없이 infra main에 커밋 하나로 고정한다. 커밋은 해당 이미지의 values 한 줄과 lock 항목만 바꾸고, 메시지에 원본 커밋과 빌드 링크를 남긴다.
- main ref는 fast-forward로만 옮긴다(`force: false`). 그사이 main이 바뀌어 거부되면 새 main을 다시 읽어 최대 5번 다시 만든다. 다른 사람의 커밋을 덮어쓰지 않는다.
- 토큰과 OIDC 역할은 ADR 0024의 것을 그대로 쓴다. 토큰은 infra의 Contents 쓰기 권한만 쓰고 Pull requests 권한은 쓰지 않는다.
- 이미지 외의 infra 변경은 지금처럼 PR과 리뷰를 거친다.

## 영향과 후속 작업

- 기대 효과: main push 뒤 몇 분 안에 infra main이 그 커밋의 이미지를 가리킨다. 플랫폼 앱의 Argo CD 자동 sync가 켜지면 사람 손 없이 서버까지 반영된다.
- 단점과 위험:
  - 운영 이미지가 리뷰 없이 바뀐다. 대신 platform은 `ci`를, frontend는 테스트와 빌드를 통과한 커밋만 게시한다.
  - infra main에 사람이 아닌 커밋이 자주 쌓인다. 커밋 제목이 `chore: 플랫폼 이미지를 ...로 교체`, `chore: 콘솔 이미지를 ...로 교체`로 정해져 있어 구분할 수 있다.
  - 토큰이 탈취되면 infra main에 아무 커밋이나 넣을 수 있다. ADR 0024와 같이 토큰은 infra 하나에만, 대회 종료 직후까지만 유효하다.
  - 문제가 생기면 해당 infra 커밋을 되돌려(revert) 이전 이미지로 돌아간다.
- 필요한 후속 작업 및 검증:
  - platform#42, frontend#13 머지 뒤 main push 한 번으로 infra main 커밋까지 되는지 확인한다.
  - 서버 자동 반영은 `charlie-management-hopcharlie-platform`의 Argo CD 인수와 자동 sync 활성화가 끝나야 한다.

## 관련 자료

- platform 저장소 `.github/workflows/publish-image.yml`, `.github/scripts/pin-infra-image.py`
- frontend 저장소 `.github/workflows/deploy.yml`, `.github/scripts/pin-console-image.py`
