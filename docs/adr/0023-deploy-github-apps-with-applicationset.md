# 0023. GitHub에서 배포하는 앱의 차트와 ApplicationSet을 infra에서 관리한다

- 날짜: 2026-11-03
- 상태: 제안
- 관련 ADR: [ADR 0016](0016-run-hopcharlie-platform-on-management-cluster.md), [ADR 0021](0021-configure-platform-github-app-deploy.md), platform 저장소 ADR 0011(GitHub 앱 배포)

## 배경과 제약

- platform ADR 0011에 따라 플랫폼은 gitops 저장소에 `apps/<app>/<env>.yaml`을 커밋하고, infra의 ApplicationSet이 파일마다 Application을 만든다. 차트와 ApplicationSet은 platform #35가 platform 저장소의 `deploy/charts/hopcharlie-app`, `deploy/argocd`에 만들고 kind의 Argo CD로 검증했다. ApplicationSet은 차트를 infra의 `helm/charts/hopcharlie-app`에서 읽도록 되어 있는데, infra에는 그 경로가 없다.
- Argo CD는 관리 클러스터에 있고, root(`charlie-root`)가 `helm/gitops`를 그린다. root 프로젝트는 argocd 네임스페이스에 Application과 AppProject만 만들 수 있다.
- 앱은 워크로드 클러스터에서 돈다(플랫폼의 환경 `eks`). 워크로드 endpoint는 Git에 없고 root의 `targets` 값으로만 들어온다.
- 게이트웨이(관리 클러스터)는 앱의 Service에 붙은 내부 NLB로 HTTP를 보낸다. 플랫폼 pod의 egress는 80, 443, 5432만 열려 있다.
- 배포 실행기는 argocd 네임스페이스의 ApplicationSet과 Application에 refresh annotation을 patch하고 Application을 읽는다([ADR 0021](0021-configure-platform-github-app-deploy.md)).

## 검토한 대안

- 차트와 ApplicationSet을 platform 저장소에 두고 Argo CD가 그 저장소를 읽게 하기: Argo CD에 저장소 자격 증명이 하나 더 필요하고, 클러스터에 무엇이 배포되는지 infra만 보고 알 수 없다.
- ApplicationSet을 `helm/gitops`에 직접 넣기: root 프로젝트가 ApplicationSet, Role, 정책까지 만들어야 해서 손으로 적용한 root 매니페스트를 다시 적용해야 한다.
- 기존 `charlie-addons-management` 프로젝트를 쓰기: 이 프로젝트는 argocd 네임스페이스를 대상으로 허용하지 않는다. 허용하면 모든 애드온이 argocd에 무엇이든 만들 수 있다.
- 플랫폼 egress에 앱의 포트를 열기: 앱마다 포트가 달라서 앱을 추가할 때마다 infra를 고치거나 VPC 전체에 모든 포트를 열어야 한다.
- 플랫폼 권한을 Role만으로 제한하기: RBAC는 같은 네임스페이스에 있는 infra Application과 앱 Application을 구분하지 못하고, patch로 바꿀 수 있는 필드도 제한하지 못한다. infra Application의 spec을 바꾸면 Argo CD가 cluster-admin 권한으로 sync한다.

## 결정과 이유

- platform `2aea035`의 `deploy/charts/hopcharlie-app`을 [helm/charts/hopcharlie-app](../../helm/charts/hopcharlie-app/README.md)으로, `deploy/argocd`를 `helm/charts/hopcharlie-apps`로 옮긴다. 이후 운영에 쓰는 원본은 infra다.
- root가 관리 클러스터에 AppProject `charlie-hopcharlie-apps`와 Application `charlie-management-hopcharlie-apps`(sync-wave 2)를 그린다. 이 프로젝트는 argocd 네임스페이스의 AppProject, ApplicationSet, Role, RoleBinding과 클러스터 범위의 ValidatingAdmissionPolicy, ValidatingAdmissionPolicyBinding만 허용한다.
- Application은 helm parameter로 `environments.eks.cluster.server`에 root의 워크로드 endpoint를, `chart.repoURL`과 `chart.revision`에 root의 저장소와 revision을 넘긴다.
- 환경 `eks`의 프로필은 [clusters/aws-dev-management/values/hopcharlie-apps.yaml](../../clusters/aws-dev-management/values/hopcharlie-apps.yaml)에 둔다. 공개 서비스는 LBC가 만드는 내부 NLB(ip 대상)의 80 포트, DB는 CNPG `gp3` 1Gi, S3는 버킷 접두사 `hopcharlie-eks-`와 Secret `app-s3`이다. 네임스페이스마다 ResourceQuota, LimitRange, NetworkPolicy를 두고, 게이트웨이는 VPC `10.40.0.0/16`에서 들어오는 것으로 허용한다.
- 앱 차트에 `profile.publicService.port`를 추가하고 `eks`에서는 80으로 둔다. 그래서 게이트웨이의 egress는 지금처럼 80만 열면 된다. 기본값 0은 서비스의 포트를 그대로 쓰므로 platform E2E의 동작은 바뀌지 않는다.
- 앱이 쓸 수 없는 네임스페이스에 워크로드 클러스터의 `charlie-apps`, `headlamp`를 더한다.
- 플랫폼 API의 ServiceAccount에 Role `hopcharlie-platform-deployer`를 준다. ApplicationSet `hopcharlie-apps`의 get, patch와 Application의 get, patch만 허용한다. ValidatingAdmissionPolicy가 이 ServiceAccount의 UPDATE를 ApplicationSet과 라벨 `hopcharlie.dev/app`이 있는 Application으로 제한하고, spec을 바꾸는 요청을 거부한다.

## 영향과 후속 작업

- 기대 효과: 플랫폼이 gitops에 커밋하면 Argo CD가 워크로드 클러스터에 앱을 만든다. 차트와 환경 프로필은 infra PR로 바꾼다.
- 단점과 위험:
  - ApplicationSet이 만드는 Application은 자동 sync, prune, selfHeal이다. gitops 파일이 지워지면 네임스페이스째 지운다. DB 데이터와 Secret도 함께 지워진다(platform ADR 0011).
  - 플랫폼은 앱 Application의 `operation`을 patch해 sync를 시작할 수 있다. spec은 바꿀 수 없다.
  - platform 저장소에 남은 복사본과 내용이 달라질 수 있다. platform E2E는 그 복사본을 쓴다.
- 필요한 후속 작업 및 검증:
  - Argo CD에 gitops 저장소의 읽기 자격 증명을 등록한다. 없으면 ApplicationSet이 파일을 읽지 못한다.
  - 워크로드 클러스터에 CNPG operator(`cnpg-system`)를 설치한다. 지금 애드온 목록에 없다.
  - S3 버킷과 `app-s3` Secret을 누가 만들지 정한다. moimroom은 `photos` 버킷을 쓴다.
  - 플랫폼이 워크로드 클러스터의 Service를 읽을 kubeconfig(SSM `/hopcharlie/kube/<이름>`)를 넣는다. 배포 실행기가 NLB 주소를 읽어 게이트웨이에 등록한다.
  - `*.hopcharlie.dev` DNS와 ALB 규칙이 게이트웨이로 가야 `<app>.hopcharlie.dev`가 열린다.
  - 지금 Argo CD의 root는 자동 sync가 꺼져 있다. 이 Application은 Helm 리소스를 Argo CD로 넘기는 작업([계획](../plans/argocd-root-bootstrap.md)) 뒤에 생긴다.
  - platform 저장소의 `deploy/charts/hopcharlie-app`, `deploy/argocd`를 E2E 전용으로 표시하거나 infra를 따르도록 정리한다.

## 관련 자료

- [helm/charts/hopcharlie-apps/templates/applicationset.yaml](../../helm/charts/hopcharlie-apps/templates/applicationset.yaml)
- [helm/charts/hopcharlie-apps/templates/platform-rbac.yaml](../../helm/charts/hopcharlie-apps/templates/platform-rbac.yaml)
- [helm/gitops/templates/applications.yaml](../../helm/gitops/templates/applications.yaml), [helm/gitops/templates/projects.yaml](../../helm/gitops/templates/projects.yaml)
- [scripts/check-helm.py](../../scripts/check-helm.py)
- platform 저장소 `test/e2e/deploy/run.sh`(kind의 Argo CD로 등록, 빌드, 커밋, sync, 삭제까지 검사)
