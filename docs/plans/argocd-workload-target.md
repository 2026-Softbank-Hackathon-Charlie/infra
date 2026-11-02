# Argo CD workload 타겟 등록 및 private 저장소 접근 점검 계획

- 날짜: 2026-11-02
- 상태: 승인된 범위 실행 완료 — workload 등록 완료, 2026-11-03 repository credential 등록 및 private 저장소 읽기 검증 성공, 애드온 배포 미실행
- 분류: 영향이 크거나 불확실한 작업. management Secret, Pod Identity, IAM, workload API 연결 경계를 포함한다.
- 목표: management Argo CD에 `charlie-dev-workload`를 기존 IAM 방식으로 등록하고, 현재 private인 이 저장소를 Argo CD 자체 인증·repo-server 경로로 읽을 수 있는지 확인한다.

## 실제 확인 결과와 한계

사용자는 두 EKS 및 관련 IAM·Pod Identity·SG·SSM 조회와 임시 SSM 접속을 승인했다. 기존 `charlie-admin` 인증으로 계정 `525091405886`, 리전 `ap-northeast-2`를 확인했다. Secret 값·로그 조회, 신규 자격 증명 발급, 리소스 변경은 수행하지 않았다.

| 대상 | 관측 결과 |
| --- | --- |
| 두 EKS | ACTIVE, 같은 VPC, private API만 활성화 |
| management Argo CD | v3.5.3, 5개 Pod 모두 Running/Ready, controller·server·ApplicationSet SA 존재 |
| Pod Identity | Argo SA 3개 모두 management IAM 역할 연결, agent 2/2 Ready; controller/server의 Pod Identity 환경변수 이름 확인 |
| IAM | management 역할의 workload deploy AssumeRole 허용 및 역방향 신뢰 존재 |
| workload EKS 권한 | workload deploy 역할 STANDARD Access Entry, AmazonEKSClusterAdminPolicy cluster 범위 |
| 네트워크 구성 | management 노드 2대에 source SG 연결, workload API에 target SG 연결, 상호 TCP 443 규칙 존재 |
| Argo 등록 상태 | cluster/repository label의 Secret 목록 모두 비어 있음. Application 없음, AppProject는 default만 존재 |
| workload | 노드 2대 Ready. 기본 namespace 및 CoreDNS/VPC CNI/kube-proxy/EBS CSI/Pod Identity agent만 확인 |

SSM bridge를 통해 두 Kubernetes API의 실제 조회는 성공했다. 이 결과는 management Argo Pod에서 workload로의 DNS/TLS/AssumeRole/인증을 통합 검증한 결과가 아니다. SG/IAM 구성 존재와 통합 성공을 구분한다. 표준 label이 없는 임의 Secret 내용은 조사하지 않았다. ApplicationSet 리소스의 존재·generator·template destination·자동 동기화 정책은 이번 조회 범위에 없었으므로 미확인이다. cluster generator가 새 등록을 감지하면 Application이 현재 없어도 자동 배포가 시작될 수 있다.

기존 `scripts/eks-access.py`는 실제 EKS의 대문자 hostname에 대해 `urlsplit().hostname`과 `netloc`를 대소문자 구분 비교하여 실패했다. 원래 ARN·endpoint·CA를 검증하는 임시 접속 스크립트로 조회했다. 저장소 도우미는 수정하지 않았으며 별도 국소 수정 후보로 남긴다.

## 범위와 완료 기준

1단계 범위는 management의 `argocd` namespace에 workload cluster Secret 하나를 생성하는 것이다. 이름은 `dev-workload-cluster`, label은 `argocd.argoproj.io/secret-type: cluster`, logical name은 실제 EKS 이름이다. 실제 endpoint·CA·기존 workload deploy role ARN으로 `awsAuthConfig`를 설정하고 TLS 검증을 유지한다. 전용 보호 로컬 파일만 사용하고 Git·Terraform state에 등록 Secret을 추가하지 않는다.

등록 완료 기준은 의도한 Secret 하나가 존재하고, 생성 입력의 실제 cluster name/endpoint/CA/role ARN이 일치하며, 기존 Argo Pod의 정상 상태와 Application 및 ApplicationSet의 예상 밖 생성·변경이 없는 것이다. Argo의 실제 workload 연결 성공은 별도 검증 항목이다. 연결 상태를 확인할 수 없거나 미사용 클러스터가 Unknown으로 나타나면 '등록 완료 / 통합 연결 미검증'으로 보고하고 완전한 연결 성공으로 표현하지 않는다.

다음은 제외한다: Terraform apply/plan 및 state 조회, IAM/SG/Access Entry 변경, Pod 재시작·exec, Secret 값 조회, 별도 승인 없는 Argo 로그인·토큰 발급, repository credential 등록, root/Application/AppProject 생성, 앱·애드온 배포·동기화, CNPG 도입, management 자체의 외부 타겟 등록. 이미 존재하는 광범위 deploy 권한의 축소도 별도 보안 변경이다.

## Private Git 저장소 접근 점검

대상은 `https://github.com/2026-Softbank-Hackathon-Charlie/infra.git`의 `main`이다. 앞선 조회에서 repository label Secret과 Application은 없었지만, **repo-creds label의 credential template 및 Argo Repository API는 확인하지 않았다**. 따라서 자격 증명 미설정이나 저장소 접근 실패를 아직 단정하지 않는다. 기존 로컬 Git/gh의 접근 성공은 Argo CD 접근 성공의 근거로 사용하지 않는다.

1. 실행 승인 범위에 management의 `repository`/`repo-creds` Secret **목록 메타데이터**, Application/AppProject의 저장소·revision·허용 정책, Argo Repository API를 통한 해당 private 저장소의 연결 상태·refs·경로 상세 조회를 추가한다. 기존 인증된 Argo UI/CLI 세션과 management 서비스 port-forward를 사용한다. 로그인 세션이 없거나 만료되었으면 사용자에게 인증을 요청하거나, 로그인·토큰 발급의 구체적 범위를 별도 승인받기 전까지 API 점검을 보류한다. admin password Secret이나 credential 값을 대신 조회하지 않는다.
2. Argo의 저장소 등록 여부와 연결 상태를 확인한다. 등록되어 있으면 연결 상태 갱신의 영향 범위를 먼저 확인한다. v3.5.3의 Get(forceRefresh)는 내부 저장소 목록 처리 중 접근 가능한 다른 등록 저장소까지 연결을 갱신할 수 있으므로, 무관한 등록 저장소가 있으면 `argocd repo get <URL> --refresh hard`/forceRefresh를 실행하지 않는다. 그 경우 대상의 refs·경로 조회로 점검하거나 포괄 갱신의 대상·영향을 별도 승인받는다. 대상 저장소만 등록된 경우에만 forceRefresh를 사용한다. repo-creds template만 있는 미등록 저장소는 곧바로 실패로 단정하지 않고, 기존 자격 증명을 적용하는 아래 refs/경로 조회가 허용되는지 확인한다. 이 과정에서 저장소 등록·credential 변경을 하지 않는다.
3. Argo Repository API의 `ListRefs`로 `main` 해석 여부를 확인하고, `ListApps` 또는 `GetAppDetails`로 `helm/gitops`와 `helm/charts/cluster-baseline`의 경로·Helm chart 정보를 확인한다. 가능하면 main이 해석된 commit SHA로 경로 상세 조회를 고정한다. 기존 AppProject의 sourceRepos 허용 범위를 확인하고 해당 appProject와 기존 Application 이름에 충돌하지 않는 가상 appName을 지정한다. 이 API는 읽는 연산이지만 Argo RBAC상 applications create/update/get 권한을 요구할 수 있다. 필요한 기존 권한이 없으면 RBAC/프로젝트 변경이나 Application 생성으로 우회하지 않고 미검증으로 보고한다. Application을 만들지 않고 Argo repo-server가 실제 Git 소스를 읽는 경로를 사용하며, 로컬 checkout이나 단순 캐시 상태 표시만으로 성공을 선언하지 않는다. API/RBAC가 미등록 저장소 조회를 허용하지 않으면 미등록 상태와 검증 불가를 분리해 보고한다.
4. 결과를 **설정 존재 / 현재 원격 연결 / main 및 실제 경로 읽기 / Application 렌더링·동기화**로 나누어 보고한다. 앞의 세 항목이 성공해야 저장소 읽기 접근 검증 완료로 표시한다. refs 또는 경로 조회에서 반환된 repo-server 인증·권한·DNS/TLS 오류는 민감값을 제거하고 분류한다. 연결 상태 Success만 확인되면 경로 읽기는 미검증으로 남긴다. 기존 Application이 없는 현재 상태에서는 지속적인 배포용 fetch·렌더링·동기화가 확인되었다고 표현하지 않는다.
5. credential 누락·권한 부족이 확인되면 접근 실패 또는 미설정을 결과로 남기고, repository Secret 추가나 토큰 발급·권한 변경은 대상·최소 권한·저장 위치를 구체화한 별도 변경 계획과 실행 승인을 받은 뒤 수행한다. 자동 등록·인증 방식 전환은 이번 범위에 없다.

이 점검은 workload 타겟 등록과 독립적이다. 조회·Git fetch 및 Argo 캐시 갱신 외에는 변경하지 않는다. 클러스터 타겟 등록 승인 없이도 점검할 수 있으며, 두 작업의 승인 범위를 구분한다. root/Application 생성, sync, manifest 렌더링 시험용 Application 생성은 포함하지 않는다. 저장소 응답 본문·Helm values·자격 증명·토큰은 보고서나 터미널에 덤프하지 않고 URL·경로·revision·성공 여부만 남긴다.

참고: 배포 버전의 [Argo CD v3.5.3 Repository API 정의](https://github.com/argoproj/argo-cd/blob/v3.5.3/server/repository/repository.proto)에 Get(forceRefresh), ListRefs, ListApps, GetAppDetails가 있다. GetAppDetails는 HTTP POST지만 저장소 경로 상세를 읽는 연산이며 저장소 생성 API와 다르다. manifest 생성·sync 성공은 이 API 확인만으로 입증하지 않는다.

## 단계와 의존성

1. 독립 계획 리뷰를 받고 지적을 반영한 뒤 사용자에게 계획을 제시한다. 최초 요청 단계에서는 조회와 계획 수립만 수행하고, 승인 후 아래 범위를 실행한다.
2. private 저장소 점검과 타겟 등록을 구분해 승인받는다. 위 저장소 점검의 구체적인 추가 조회 범위와 기존 Argo 인증 사용·서비스 port-forward를 승인받은 뒤 점검하고, 후속 실행 시 계획 승인과 함께 **management의 cluster Secret 하나 생성 및 해당 메타데이터·Pod 상태 검증 및 Application/ApplicationSet의 spec·상태 읽기 전용 조회**에 대한 실행 승인을 받는다. 조회 승인을 변경 승인으로 확대하지 않는다.
3. 기존 승인 범위에서 계정·클러스터 식별·등록 Secret 목록·Application 및 ApplicationSet의 존재와 spec을 재확인한다. ApplicationSet의 generator·template destination·자동 동기화 정책이 새 등록에 반응할 가능성이 있거나 판단이 불확실하면 생성을 중단하고 배포 영향까지 재계획한다. 이미 등록되었거나 Application이 추가되었으면 자동 덮어쓰기하지 않고 영향 범위를 재평가한다.
4. 실제 EKS describe 결과에서 name/endpoint/CA를 얻어 0700 디렉터리·0600 임시 파일로 Secret을 준비한다. 예상 계정·리전·ARN·role ARN을 검증한다. CA는 이미 base64인 값을 사용하고 endpoint는 SSM loopback이 아닌 원래 HTTPS 주소로 유지한다. 인증 토큰·장기 키를 넣지 않는다.
5. 로컬 구조 검사로 namespace/label/name/role/TLS 및 허용 필드만 있는지 확인하고, 명시적 management kubeconfig/context로 새 Secret을 `create`한다. 기존 자원을 갱신하는 apply/upsert는 사용하지 않는다. 동일 이름 충돌 시 중단한다.
6. Secret 목록과 Argo Pod 상태, Application/ApplicationSet 목록·상태를 재확인해 등록 결과를 기록한다. 실제 Argo 연결 확인을 위해 인증정보 접근·로그인·Pod exec 또는 시험 Application이 필요하면 구체적 별도 검증 계획과 권한 범위를 먼저 제시한다. 현재 운영자 kubeconfig로의 성공을 Argo 연결 성공으로 대체하지 않는다.
7. 임시 파일과 이번 작업에서 시작한 SSM 세션만 정리하고 결과·한계를 보고한다. 사용자 기존 터널은 종료하지 않는다.

## 후속 배포 단계

등록 자체에는 Git repository credential이 필요하지 않다. 이후 실제 애드온 배포를 요청하면 이번 저장소 점검 결과를 바탕으로 필요 시 read-only repository credential 준비, workload AppProject와 Application 또는 root 방식의 범위를 별도 결정한다. 현 root 예제에는 management/workload 모두 포함되며 workload baseline과 LBC·metrics-server·Prometheus stack·Sealed Secrets·Argo Rollouts가 자동 동기화된다. 따라서 root 예제를 이번 등록 작업에 그대로 적용하지 않는다. 후속 배포는 Git 접근권한·리소스 소유권·노드 용량·CRD/namespace/RBAC·스토리지 영향을 포함해 다시 계획·리뷰·승인한다.

## 파일·검증·위험·ADR

- 현재 변경: 이 계획 문서 및 README 파일 목록 링크만 추가한다. 기존 수정 파일의 다른 변경은 보존한다.
- 실행 때 변경: management의 cluster Secret 하나 및 보호 임시 로컬 파일. 등록은 기존 README 방식이며 구현 코드 변경은 예상하지 않는다.
- 문서 검증: `make inventory-check`, 상대 링크 확인, diff 확인. 실제 조회 결과를 가정과 분리한다.
- 위험: 기존 deploy 역할은 workload 전체 관리자 권한이다. 잘못된 endpoint/CA/role은 연결 실패를 일으킨다. Application 또는 cluster generator ApplicationSet이 존재하면 등록이 자동 배포를 가능하게 하므로 생성 직전 둘 다 재확인한다. 조회와 Secret 생성 사이 다른 운영자의 동시 변경을 원자적으로 차단하는 절차는 아니며 동시 운영 시 작업 시간을 조율한다.
- 실패 대응: 생성 실패는 변경 없음으로 보고한다. 생성 후 문제가 있으면 자동 IAM/SG 변경·재배포하지 않는다. 새 Secret 삭제는 management의 정확한 새 Secret 하나에 대한 별도 삭제 실행 승인 후 수행한다. workload 자원은 이 단계에서 만들지 않는다.
- 관련 ADR: [0002](../adr/0002-require-approval-for-privileged-operations.md), [0010](../adr/0010-grant-eks-operator-access.md), [0014](../adr/0014-local-eks-access-commands.md). 기존 Pod Identity·deploy role·private API 방식 준수로 새 ADR은 현재 필요하지 않다. 소유권·인증·배포 방식을 변경하는 결정이 생기면 다음 번호의 제안 ADR을 작성하고 README에 연결한다.
- 사용자 판단: 등록 및 저장소 점검 계획은 승인받았다. 저장소 credential 등록, 애드온 배포와 통합 연결 검증을 위한 추가 권한은 해당 후속 범위 확정 시 별도로 승인한다.

## 계획 리뷰와 문서 검증 결과

- 독립 plan-reviewer가 ApplicationSet cluster generator의 자동 배포 위험 누락을 주요 지적으로 제시했다. 실제 ApplicationSet 상태는 미확인으로 명시하고, 후속 조회 승인 범위·생성 전 중단 조건·생성 후 확인·동시 변경 한계를 위 단계에 반영했다. 재검토에서 주요 지적 해소 및 GREEN 판정을 받았다. 중대한 미해결 지적은 없다. 리뷰 통과는 실행 승인이 아니다.
- 계획의 상대 링크와 README 목록 링크 검사, `git diff --check` 통과. `make inventory-check`는 이번 작업 전부터 존재한 로컬 `helm/bootstrap/charts/argo-cd-10.9.6.tgz`의 README 목록 누락으로 실패했다. 해당 파일이나 기존 사용자 변경은 수정하지 않았다.
- 조회 종료 후 이번 작업의 SSM 세션 2개와 임시 kubeconfig/접속 스크립트를 정리했다. 실제 인프라·Secret·Application 변경은 없다.

- 추가 요청 반영: private 저장소의 repo-creds 미확인 한계, Argo 자체 연결·refs·경로 읽기 점검, 기존 세션 필요 조건, 자격 증명 변경 제외, fetch와 배포 검증의 차이를 추가했다. 추가안도 새 plan-reviewer의 GREEN 판정을 받았다. 경미한 권고 2건인 forceRefresh의 다른 저장소 영향 및 경로 조회 API의 Application RBAC 요구 조건을 반영했다. 중대한 미해결 사항은 없다.


## 승인된 실행 결과 — 2026-11-02

- 사용자 승인: 수정 계획의 진행과 새 브랜치 생성 승인. 브랜치명은 사용자 지시에 따라 prefix 없는 `argocd-workload-target`을 사용했다. 이후 사용자가 기존 admin 계정으로 직접 로그인하여 토큰을 발급하도록 명시적으로 추가 승인했다. 자격 증명 값은 이 문서에 기록하지 않는다.
- 승인 범위: 기존 AWS 인증 및 SSM·관리 서비스 port-forward를 통한 조회, Application/ApplicationSet 사전 확인, management의 cluster Secret 하나 생성, Argo Repository API 읽기 점검 및 해당 로그인 세션 발급. IAM/SG·repository credential·Application·애드온 변경은 실행하지 않았다.
- 사전 조건: Application과 ApplicationSet 모두 없음. AppProject `default`는 대상 source repository를 허용했다. `cluster`/`repository`/`repo-creds` label Secret 목록 모두 비어 있었다. Argo Pod 5개 모두 Running/Ready였다.
- 등록 구현: 실제 workload EKS의 name/ARN/HTTPS endpoint/CA 및 기존 deploy 역할 Access Entry를 확인하고, 보호 로컬 파일의 구조와 client dry-run을 검증한 뒤 management `argocd/dev-workload-cluster` Secret 하나를 `create`했다. Secret 내용은 Git·Terraform state에 기록하지 않았다.
- 등록 검증: Secret 목록에서 새 Secret을 확인했고 Argo Cluster API(HTTP 200)에서 `charlie-dev-workload`가 실제 EKS endpoint로 나타났다. 연결 상태는 `Unknown`, 이유는 `Cluster has no applications and is not being monitored.`였다. **등록 완료이며 Argo→workload 통합 연결은 미검증**이다.
- 사후 검증: Application/ApplicationSet은 여전히 없고 Argo Pod 5개 모두 Running/Ready, 재시작 0회였다. workload 애드온이나 앱을 배포·동기화하지 않았다.
- 저장소 점검: admin 로그인 HTTP 200으로 점검용 세션 토큰을 발급했다. Argo Repository 목록 API는 HTTP 200, 등록 목록은 비어 있었다. 대상 private 저장소의 ListRefs는 HTTP 500/code 2의 인증 오류였고 main 읽기에 실패했다. GetAppDetails도 `helm/gitops`, `helm/charts/cluster-baseline` 양쪽 모두 HTTP 500/code 2로 실패했다. 비밀값·응답 본문·values는 출력하거나 문서에 저장하지 않았다. 다른 저장소 forceRefresh는 실행하지 않았다.
- 저장소 결론: 현재 Argo CD에서 이 private 저장소를 정상적으로 가져오는 상태가 아니다. repository/repo-creds 설정이 없고 실제 Argo 경로의 읽기가 인증 오류로 실패했다. repository 읽기용 credential 준비·등록과 재검증이 필요하며, 신규 토큰/키 발급이나 repository Secret 생성은 이번 승인 범위 밖이므로 수행하지 않았다. Application 렌더링·동기화도 미검증이다.
- 계획 대비 변경: 기존 인증 세션 부재 시의 대기 조건은 사용자의 admin 직접 로그인·토큰 발급 추가 승인으로 해소했다. 나머지는 승인된 등록/조회 범위대로 수행했다. 기존 접속 도우미의 hostname 비교 문제는 승인 범위를 넓혀 수정하지 않고 임시 접속으로 처리했다.
- ADR: 신규·변경 없음. 기존 인증·리소스 소유권·배포 방식의 반복 적용이다.
- 정리: 점검 토큰·임시 등록 파일·kubeconfig·접속 스크립트는 로컬 임시 경로에서 제거했고, 이번 작업의 port-forward와 SSM 세션을 종료했다. 기존 사용자 세션은 건드리지 않았다.


## Repository credential 등록 추가 계획 — 2026-11-03

- 상태: 제안 — 사용자가 직전 구체적인 등록·검증·임시파일 삭제 계획에 대해 “완료 진행해줘”로 실행 승인. 신규 범위 독립 리뷰 GREEN, Secret 등록 및 Argo 저장소 읽기 검증 성공.
- 분류: 영향이 큰 작업. private Git 접근 자격 증명을 실제 management Kubernetes Secret에 저장한다. 이전에 승인·완료된 cluster Secret 등록과 구분한다.
- 목표/완료 기준: 대상 저장소 전용 repository Secret 하나를 만들고 Argo Repository API로 연결·main refs·두 Helm 경로 읽기가 성공하는지 확인한다. 실패하면 결과를 미완료로 보고하고 자동 권한 확대·재발급·기존 Secret 갱신을 하지 않는다.
- 대상: 계정 525091405886, ap-northeast-2, charlie-dev-management, namespace argocd, Secret `charlie-infra-repository`, URL `https://github.com/2026-Softbank-Hackathon-Charlie/infra.git`.
- 입력: 사용자가 만든 `/private/tmp/argocd-github-token`. 0600·현재 사용자 소유·일반 파일·비어 있지 않음을 값 조회 없이 확인했다. 토큰 권한은 발급 안내의 infra 단일 저장소 Contents Read-only를 전제로 하며 실제 권한 범위를 별도 API로 확인한 것은 아니다. 토큰 값을 출력하거나 쓰기 시험으로 권한을 확인하지 않는다.
- 생성 내용: label `argocd.argoproj.io/secret-type: repository`, `type: git`, 위의 정확한 URL, 비어 있지 않은 HTTPS 사용자명 `x-access-token`, `password`에 제공 PAT. 사용자명은 인증 토큰으로 대체하지 않으며 별도 GitHub 사용자 조회를 하지 않는다. generic repo-creds template을 만들어 다른 저장소에 공유하지 않는다.
- 변경 파일: 이 계획의 실행 결과 기록 및 필요 시 README 설명만. 실제 Secret과 토큰은 Git·Terraform state·CLI 인자·일반 로그에 포함하지 않는다. 신규 ADR은 기존 README의 HTTPS/PAT repository Secret 방식을 그대로 따르는 반복 적용이라 필요하지 않다. 관련 ADR 0002/0010/0014 유지.

실행 순서:

1. 새로운 plan-reviewer의 독립 검토를 받았다. 사용자의 “완료 진행해줘”는 직전 제시한 management/argocd의 이 저장소 전용 Secret 생성·읽기 검증·임시파일 삭제 범위의 승인으로 기록하며, 이 범위를 확대하지 않는다. 리뷰는 GREEN, 중대한 미해결 사항 없음. 경미한 정리 조건 보완을 반영했다.
2. 승인 후 기존 AWS 프로필·SSM·임시 kubeconfig와 loopback Argo 서비스 port-forward를 재사용한다. 기존 승인한 admin 로그인/점검 세션 발급만 수행하며 다른 자격 증명을 조회하지 않는다. 인증서 검증을 유지하고 임시 파일은 0700 디렉터리/0600 파일로 보호한다.
3. Application/ApplicationSet spec과 기존 repository/repo-creds 목록 메타데이터를 확인한다. private 저장소 연결 복구로 기존 generator/자동sync가 배포를 시작할 가능성이 있으면 중단하고 재계획한다. 같은 이름 또는 같은 대상 저장소의 등록/credential 충돌이 의심되면 Secret 값을 조회하거나 덮어쓰지 않고 중단한다. 동시 변경을 원자적으로 막을 수 없으므로 생성 직전에 재확인한다.
4. 입력 파일은 symlink를 따르지 않고 소유자·권한을 재검증하여 메모리에서 읽는다. 빈 값·개행/공백만 있는 값은 거부한다. 보호된 임시 Secret JSON 파일 또는 stdin을 통해 `kubectl create`만 사용하고 update/apply/upsert는 하지 않는다. kubeconfig와 management context를 명시한다. dry-run은 이름/성공 여부만 출력하고 API 오류 원문에 비밀값이 섞일 수 있으므로 필터링해 보고한다.
5. Secret metadata와 Argo의 저장소 목록을 확인한다. 다른 등록 저장소가 없을 때만 기존 계획의 forceRefresh를 사용하며, 그 외에는 대상 refs/경로 조회로 검증한다. main refs와 helm/gitops, helm/charts/cluster-baseline의 경로 상세 읽기를 점검한다. 기존 AppProject default 권한과 충돌하지 않는 가상 appName을 사용한다. 본문·values·토큰은 출력하지 않는다. GitHub 조직의 Pending/권한 부족/만료 등은 인증 실패로 보고하고 변경하지 않는다.
6. 기존 클러스터 등록 유지·Argo Pod 정상·Application/ApplicationSet의 예상 밖 변경이 없는지 확인한다. Secret 생성·원격 연결·main/경로 읽기·배포 검증 여부를 나누어 기록한다. root/Application 생성·sync·Pod exec·IAM/네트워크 변경은 제외한다.
7. 성공 시 사용자가 전달한 PAT 파일과 작업 중 만든 Secret 임시 파일·Argo 세션 토큰·kubeconfig를 삭제하고 이번 작업의 터널만 종료한다. 실패 시 생성했던 Secret은 자동 삭제하지 않고 해당 이름 하나의 삭제 실행 승인을 별도로 받는다. 성공·실패와 관계없이 작업 중 만든 Secret 임시 파일·Argo 세션 토큰·kubeconfig·스크립트와 이번 터널은 정리한다. 재시도에 입력이 필요하면 사용자가 전달한 원본 PAT 파일 하나만 보호 상태로 보존하고 보존 여부를 보고한다. 생성 전 실패 시 원본을 임의 폐기하지 않는다.

권한/영향: 제공 PAT의 보호 파일 읽기·정확한 repository Secret 한 개 생성, 기존 AWS/EKS 및 Argo 읽기·SSM/port-forward·admin 로그인, 대상 private Git fetch, 성공 후 원본 임시 PAT 파일 삭제. GitHub 토큰 신규 발급·권한 변경·조직 정책 변경·기존 Secret 값 조회·workload 배포는 포함하지 않는다. Argo CD가 이 저장소를 읽을 수 있게 되는 지속적 접근 부여이며, PAT 만료 시 재등록이 필요하다. 민감값이 없는 결과만 계획에 남긴다.

검증: 계획 링크/diff 검사 및 make inventory-check. 실행 시 구조/client dry-run, Argo API의 연결·refs·경로 조회, 사전/사후 리소스 상태 확인. 토큰 없는 fake test나 동일 API 반복은 추가하지 않는다. 알려진 inventory-check 실패 원인은 기존 ignored Helm chart tgz의 README 누락이며 범위 밖이다.


### Repository credential 등록 실행 결과 — 2026-11-03

- 승인된 범위: 사용자가 준비한 PAT 파일을 이용해 management argocd에 이 private 저장소 전용 repository Secret 한 개 생성, 기존 admin 로그인 및 Argo 연결/main/경로 읽기 검증, 성공 후 원본 PAT·임시파일 삭제. GitHub 토큰 발급이나 권한 변경은 수행하지 않았다.
- 독립 리뷰: 신규 plan-reviewer GREEN. 성공·실패 관계없이 작업용 자격 증명/설정/터널을 정리하라는 경미한 권고를 반영했다. 중대한 미해결 사항 없음.
- 사전 확인: Application/ApplicationSet 없음. repository/repo-creds Secret 없음. 기존 workload cluster Secret 유지. Argo Pod 5개 정상.
- 구현: management `argocd/charlie-infra-repository`를 create했다. 정확한 HTTPS URL에만 적용되는 repository Secret이며 토큰은 보호 파일에서 메모리로 읽어 stdin으로 전달했다. 자격 증명을 CLI 인자·Git·Terraform state·문서·일반 로그에 기록하지 않았다. 기존 Secret 값은 조회하지 않았다.
- 검증: Argo Repository 목록 HTTP 200에서 대상 하나를 확인했다. 대상만 등록되어 있어 연결 상태 갱신을 수행했고 HTTP 200, `Successful`이었다. Argo ListRefs는 HTTP 200, main 존재를 확인했다. GetAppDetails는 `helm/gitops`, `helm/charts/cluster-baseline` 양쪽 모두 HTTP 200, `Helm` 및 chart 정보 존재를 확인했다. 원문 values나 저장소 내용을 보고서에 복사하지 않았다.
- 사후 확인: repository와 기존 cluster Secret이 모두 존재한다. Application/ApplicationSet은 여전히 없으며 Argo Pod 5개 모두 Running/Ready, 재시작 0회다. workload 통합 연결·Application 렌더링/동기화·실제 배포는 이번 검증에 포함되지 않는다.
- 정리: 검증 성공 후 사용자가 전달한 원본 PAT 임시 파일과 작업용 세션 토큰·인증서 파일을 삭제했다. 작업용 kubeconfig·스크립트와 이번 port-forward/SSM 세션도 정리했다. Argo의 repository Secret에는 지속 접근에 필요한 PAT가 남으며, 만료 시 갱신이 필요하다.
- 계획 대비 변경: 없음. 승인된 HTTPS/PAT 방식과 등록·읽기 검증 범위를 유지했다.
- ADR: 신규·변경 없음. README의 기존 repository Secret 방식을 그대로 적용했다.
