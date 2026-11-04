# AWS CNPG PoC 실행·복구 안내

코드 구현과 로컬 검사는 실제 환경 실행 승인이 아니다. 아래 AWS/backend 조회·apply, Secret/봉인 키 생성·등록, Argo operation, Job/백업/복원·Pod 재생성은 **대상·권한·비용을 명시한 실행 승인 후** 운영자가 수행한다. 이 문서의 명령을 그대로 일괄 실행하지 않는다. [계획](../plans/cnpg-aws-poc.md)의 게이트를 따른다.

## 대상과 변경하지 않는 것

- AWS `ap-northeast-2`, workload `charlie-dev-workload`, DB namespace `charlie-db`, source `postgres-poc`, restore `postgres-poc-restore`.
- Argo Application은 management `charlie-dev-management/argocd`에 등록하지만 DB/Operator/plugin/cert-manager/Sealed Secrets는 workload에 설치한다.
- 관리 `hopcharlie-postgres`, API/gateway/console, 기존 PVC/SSM, platform Terraform, root/기존 앱 자동화, GKE·RDS를 변경하지 않는다. `-rw`는 workload 내부 주소이며 관리 플랫폼의 새 DSN으로 등록하지 않는다.
- 단일 인스턴스, synthetic A/B fixture, 합계 1GiB 이하·앱 연결 합계 10개 이하. HA·노드/AZ/리전 장애 SLA·상시 자동 경보는 제공하지 않는다.

## 고정 버전과 로컬 검사

| 구성 | 고정값 |
| --- | --- |
| Helm / Kubernetes | 3.19.1 / 1.35 |
| CNPG chart / Operator | 0.29.1 / 1.30.1 |
| PostgreSQL | 18.6-minimal-trixie, registry digest 고정 |
| Barman chart / plugin | 0.8.1 / v0.15.1 |
| cert-manager | v1.21.3 |

[버전·호환성 자료](../../helm/versions.json)와 [이미지 lock](../../helm/images.lock.json)을 함께 확인한다. Barman v0.15.1의 고정 소스는 boto3/botocore 1.43.105를 설치하므로 Pod Identity SDK 최소 요건을 충족한다. 이는 **실제 sidecar에 credential 환경변수·토큰 volume이 주입됐다는 증명은 아니다**. 실제 실행 전 관련 환경변수의 이름·volume 경로만 확인하며 토큰/환경변수 값/Secret 값은 출력하지 않는다. 정적 AWS key를 추가하거나 IRSA로 임의 우회하지 않는다.

```bash
make check PYTHON=/path/to/venv/bin/python HELM=/path/to/helm-3.19.1
```

backend 없는 Terraform validate/mock, fake CI/프로브, Helm 렌더링·고정 CRD 구조 검사는 실제 admission/CEL, 스케줄링, S3·TLS·백업·SQL 동작을 대체하지 않는다. 공개 chart 다운로드에 네트워크가 필요하며 테스트에 AWS 자격 증명은 필요하지 않다.

## 실행 전 승인·사전 확인

1. 코드와 실행할 40자리 commit을 검토한다. main merge/PR 게시도 이번 코드 작성 승인의 일부로 간주하지 않는다. 배포할 저장소·revision을 Argo가 실제 읽는지 확인한다.
2. 지정 두 EKS와 AWS 계정·bridge 신원, Argo root/기존 앱의 **전체 automated 필드**와 진행·대기 operation 부재, 신규 이름 충돌을 조회한다. 자동화가 발견되면 임의 패치하지 않고 중단한다. 기존 조회의 `<none>` 필드 일부만으로 수동 상태를 단정하지 않는다.
3. source+restore의 DB/sidecar, 새 controller, 두 서비스 Job, 기존 시스템·metrics 및 CPU/메모리 20% 여유를 확인한다. 복구 Job은 임시 disk emptyDir 최대 2GiB와 ephemeral-storage request 512MiB를 사용하므로 노드 임시 디스크도 확인한다. source/restore 각 PVC 20GiB와 Retain 잔여 EBS·S3 보관 비용을 포함한다. 부족하면 자동 증설하지 않는다.
4. workload baseline/StorageClass·Sealed Secrets가 이미 설치됐다면 소유권과 diff를 먼저 확인한다. 준비되지 않았다면 승인한 해당 앱만 설치한다. EBS CSI·Pod Identity agent는 재설치하지 않는다.
5. 기존 서비스 Pod/PVC 식별·Ready 상태와 제외 앱 spec/operation을 전후 비교할 수 있게 메타데이터를 기록한다. 다른 사용자의 터널·operation을 종료하지 않는다.

기존 설치를 재사용하는 경우에는 전제를 다시 구분한다. 2026-11-03 후속 실행은 기존 `charlie-workload-cloudnative-pg` Operator 앱과 `charlie-cnpg-poc-workload` Project를 보존하고 관리 플랫폼 앱의 기존 자동 Sync를 유지하는 수정 계획을 리뷰·승인받았다. 이 예외가 새 root/대상 앱 자동화나 다른 환경의 실행 승인을 뜻하지 않는다. 자동 플랫폼의 정상 stateless Pod 교체는 이번 작업과 구분하며, 관리 DB/PVC·root 설정과 기존 Operator를 보호한다.

기존 앱의 실제 Helm releaseName/valueFiles와 override 존재·키를 먼저 확인한다. 미검토 inline 값·fileParameter가 있으면 실행하지 않는다. 앱 변경은 UID·resourceVersion·예상 source/destination/syncPolicy/operation을 검사하는 조건부 갱신으로 수행하고 충돌 시 덮어쓰지 않는다. 신규 앱은 create-only이며 이미 있는 Operator·Project를 예제 전체 apply로 덮어쓰지 않는다.

## Terraform과 선행 addon

기존 공유 입력/wrapper·선택 CI를 사용해 **foundation plan 검토/apply → workload plan 검토/apply** 순서로만 적용한다. account/bootstrap/management/all-runtime은 필요하지 않다. 기존 CI AdministratorAccess/OIDC 정책은 변경하지 않는다. 로컬 apply는 CI foundation 삭제 guard를 자동 실행하지 않으므로 같은 plan의 삭제·교체·guard 검토를 별도로 완료해야 한다.

foundation의 비밀 아닌 출력은 `cnpg_bucket_name`, `cnpg_source_role_arn`, `cnpg_restore_role_arn`이다. 출력 전체를 덤프하지 않고 필요한 항목만 승인된 state 조회로 가져온다. source/restore 역할은 정확한 workload namespace/ServiceAccount에 연결한다. Barman HeadBucket을 위해 전용 버킷의 ListBucket/GetBucketLocation은 공유하지만, 객체 내용·변경 권한은 source/restore prefix로 분리한다. restore는 source 객체를 덮어쓰거나 삭제할 수 없다.

설치 순서:

1. workload baseline만 수동 sync: Namespace `charlie-apps`, `observability`, `charlie-db`, `cnpg-system`, `cert-manager`와 기본 gp3·정책 준비. Namespace는 이 baseline만 선언한다.
2. workload Sealed Secrets 앱만 수동 sync, controller/CRD와 공개 봉인 인증서 준비 확인. controller 봉인 키 생성과 복구 키 보존 책임을 실행 승인에 포함한다. 개인키를 일반 로그/Git/문서에 저장하지 않는다.
3. [수동 Application 예제](../../helm/cnpg-poc-applications.yaml.example)를 보호된 로컬 복사본에서 정확한 workload endpoint·검토 commit·전용 버킷 이름으로 치환한다. `REPLACE_WITH_*`가 남으면 등록하지 않는다. 새 AppProject/Application만 생성하고 **root를 sync하지 않는다**.
4. `charlie-workload-cnpg-cert-manager` → `charlie-workload-cloudnative-pg` → `charlie-workload-plugin-barman-cloud`를 하나씩 수동 sync하고 각각 API/CRD/컨트롤러/인증서 준비를 기다린다. 고정 chart version·Git revision과 `prune=false`, Force/Replace 없음, 기본 Hook 전략을 확인한다. 새 앱의 `CreateNamespace=false`와 automation 없음은 유지한다.
5. 아직 DB 앱은 sync하지 않는다. 다음의 자격 증명 준비를 먼저 끝낸다.

이미 Operator·Project가 준비된 환경에서는 3번 예제에서 이 두 문서를 제외하고, 호환성을 확인한 기존 자원을 그대로 사용한다. 미존재하는 cert-manager/plugin/DB 앱 세 개만 등록하며 4번에서 기존 Operator Sync도 생략한다. baseline이 수동 생성한 Namespace/StorageClass를 인수해야 한다면 변경할 추적 annotation·관리 필드와 설정 불변 조건을 별도로 검토·승인받는다. Argo 추적 ID는 cluster-scoped 자원에도 Application destination namespace를 포함할 수 있으므로 실제 관리 주체·GVK·이름·UID로 확인한다.

## 자격 증명과 TLS

source와 restore는 단일 인스턴스이므로 `primaryUpdateStrategy: unsupervised`를 사용한다. `supervised`와의 조합은 실제 webhook이 거부한다. 이미지·자원·설정·Operator 변경으로 rollout이 시작되면 Primary 재시작이 자동 진행될 수 있으며 단일 DB 중단이 발생한다. [ADR0029](../adr/0029-use-unsupervised-updates-for-single-instance-cnpg.md)를 따른다. 스키마 검사만으로 admission 성공을 단정하지 않고 승인한 최종 SHA의 source Cluster를 server dry-run한 뒤 실제 Sync한다.

2026-11-03 실행에서는 새 봉인키 백업과 DB 비밀번호4개를 사용자가 승인한 `.generated/cnpg-poc-credentials/`에 디렉터리0700·파일0600으로 보관했다. 이 경로는 Git 제외이며 암호화된 보관소는 아니다. 사용자 인수·별도 삭제 지시 전까지 유지한다. 단계 중단 후에는 설치 기록의 정확한 UID와 소유권을 확인해 기존 키·암호·SealedSecret을 재사용하고 재생성·회전·덮어쓰지 않는다. 개인키 백업 확인에는 새 키와 로컬 더미 값의 봉인/복호화 검사를 사용했으며 실제 DB 복원 시험과 구분한다.

실행 승인은 새 비밀번호·봉인 키·내부 TLS 키 생성과 각 Secret 등록을 명시해야 한다. 자동 데이터베이스 비밀번호 생성·회전은 사용하지 않는다. 운영자가 제공하는 암호 파일은 0600·UTF-8·개행 없는 값이어야 한다. 비밀번호를 CLI 인자·values·Terraform·Git에 넣지 않는다.

| Secret | namespace | username |
| --- | --- | --- |
| svc-a-owner / svc-b-owner | charlie-db | svc_a_owner / svc_b_owner |
| svc-a-runtime / svc-b-runtime | charlie-db 및 charlie-apps에 각각 봉인 | svc_a_runtime / svc_b_runtime |

Secret 타입은 `kubernetes.io/basic-auth`, 키는 username/password, `cnpg.io/reload: "true"` label을 사용한다. strict 봉인은 이름·namespace에 묶이므로 runtime 두 사본도 **각각** 봉인한다. ciphertext를 namespace만 바꿔 재사용하지 않는다. Sealed Secrets 인증서는 공개 전용 경로로 얻고 기존 Secret/SSM 값은 가져오지 않는다.

예를 들어 승인된 source runtime 암호 파일로 DB namespace의 봉인 문서를 로컬에서 준비하는 흐름은 다음과 같다. `kubeseal`은 검토한 controller와 호환되는 CLI를 사용한다. 결과 적용은 별도 확인 후 한다.

```bash
set -euo pipefail
umask 077
# .generated/cnpg/는 0700, 암호 파일은 0600. 내용은 출력하지 않는다.
kubectl create secret generic svc-a-runtime --namespace charlie-db \
  --type=kubernetes.io/basic-auth --from-literal=username=svc_a_runtime \
  --from-file=password=.generated/cnpg/svc-a-runtime.password \
  --dry-run=client -o json |
  kubectl label --local -f - cnpg.io/reload=true -o json |
  kubeseal --cert .generated/cnpg/sealed-secrets-public.pem --scope strict --format yaml \
  > .generated/cnpg/svc-a-runtime-db.sealed.yaml
```

같은 암호 파일로 charlie-apps 사본을 별도로 만든다. owner 암호는 charlie-apps에 전달하지 않는다. 생성 ciphertext의 이름·namespace·template type/label과 필수 키를 검증한 뒤 승인된 workload에 등록하고 Secret 준비를 확인한다. 값은 조회하지 않는다.

DB 앱 `charlie-workload-cnpg-poc`를 수동 sync하고 source Cluster가 Ready, Database/DatabaseRole의 applied 및 observedGeneration이 최신인지 확인한다. Bootstrap은 db_a/svc_a_owner를 사용하며 불필요한 app DB/자동 app 암호를 만들지 않는다. db_b와 runtime 역할은 선언형 리소스로 생성한다.

클라이언트는 `sslmode=verify-full`과 해당 Cluster CA를 사용한다. source의 `postgres-poc-ca`, restore의 `postgres-poc-restore-ca`에는 개인키가 있을 수 있으므로 **전체 Secret을 복사하지 않는다**. 승인된 CA의 `ca.crt` 키만 보호된 파일로 추출하고 charlie-apps에 같은 이름의 public-only trust Secret(ca.crt만)을 등록한다. ca.key는 읽거나 전송하지 않는다. 다른 namespace에 신뢰 번들을 전달하는 작업도 Secret 등록 승인 범위에 포함한다.

## DB 앱의 Argo 비교 정책

[ADR0034](../adr/0034-compare-cnpg-admission-defaults-in-argocd.md)에 따라 AWS `charlie-workload-cnpg-poc`만 server-side diff와 mutation webhook 결과를 반영한다. CNPG가 추가한 기본값은 API의 SSA dry-run 예상 결과로 비교한다. 정확히 지정한 Role 네 개에서 권한5개가 false이거나 inRoles가 []일 때만 생략과 동등하게 비교한다. true 권한·새 membership·connectionLimit/login/inherit 변경은 정규화 대상이 아니다. RespectIgnoreDifferences는 추가하지 않아 실제 Sync는 Git에 선언한 값을 사용한다. GKE·다른 앱·global Argo 설정에 자동 적용하지 않는다.

같은 네 Role의 서버 파생 `/metadata/generation` 카운터만 비교에서 제외한다. SSA dry-run의 예상 카운터 차이는 DB 설정 변경이 아니다. 이 예외와 별개로 live Role의 applied=true와 metadata.generation/status.observedGeneration 일치를 확인한다. 전체 객체에서 generation 이외 차이가 없음을 먼저 확인하고, generation+실제 spec drift가 함께 있는 반례도 계속 차이로 감지되어야 한다.

기존 DB가 Healthy지만 OutOfSync라면 먼저 실제 drift와 기본값 차이를 확인한다. Argo3.5.3의 비교 순서는 원 config로 SSA 예측 → predicted/live 조건부 정규화 → diff다. 실행 전 실제 버전과 CNPG CRD의 inRoles topology가 atomic/기본 atomic인지 확인하고, set/map 또는 기존 미검토 ignore/compare-options가 있으면 중단한다. 생성 전 자원은 server-side diff가 생략되므로 초기 admission dry-run 검사는 계속 필요하다. webhook 규칙 변경도 예측을 바꿀 수 있다.

비교 설정 변경도 실행 승인이 필요하다. 승인된 앱 UID/resourceVersion·source/destination/project/수동 syncPolicy·operation 부재를 검사하고 해당 앱의 compare-options와 이름별 ignoreDifferences만 조건부 갱신한다. hard refresh는 비교 캐시 갱신이며 새 Sync를 요청하지 않는다. 앱 Synced/Healthy와 Cluster/Role/Pod/PVC UID/spec·revision/수동 정책 불변을 확인한다. 실패·동시 변경 시 덮어쓰기/Sync로 우회하지 않고, 승인된 경우 이번에 추가한 비교 설정만 조건부 원복한다.

## 현재 접속 정보와 로컬 접속

DB·Role·Secret 매핑, 로컬 세 터미널 접속, 앱 설정, Argo 상태 확인과 오류 대응은 [별도 접속 가이드](cnpg-access.md)를 따른다. 2026-11-04 마지막 확인에서 source Ready1/1·DB2/Role4 적용·Argo Synced/Healthy였으며 SQL 접속·migration/grant·데이터 주입·실제 복원은 아직 미실행이다.

## 수동 migration·기능·격리 시험

[검증 Job chart](../../examples/cnpg-poc/Chart.yaml)는 Argo Hook이 아니다. `helm template`은 로컬 파일만 만들며 `helm install`로 실행하지 않는다. 다음은 렌더링 예제이며 실제 apply 전에 전체 manifest의 namespace·이미지·Secret 참조를 확인한다.

```bash
helm template cnpg-check examples/cnpg-poc --set service=a --set mode=migrate \
  --set runId=seed-a-001 > .generated/cnpg/seed-a.yaml
```

승인된 workload kubeconfig/context를 **모든** kubectl 명령에 명시한다. 아래 변수는 실행 승인 후 운영자가 지정하며 global context는 바꾸지 않는다.

```bash
CNPG_KUBECONFIG=/protected/path/workload.kubeconfig
CNPG_CONTEXT=charlie-dev-workload
# 승인 후에만:
kubectl --kubeconfig "$CNPG_KUBECONFIG" --context "$CNPG_CONTEXT" \
  apply -f .generated/cnpg/seed-a.yaml
kubectl --kubeconfig "$CNPG_KUBECONFIG" --context "$CNPG_CONTEXT" -n charlie-db \
  wait --for=condition=complete job/cnpg-a-migrate-seed-a-001 --timeout=180s
```

- a/b 각각 migrate를 완료한 뒤 verify를 실행한다. migration은 각 DB owner로 PUBLIC CONNECT/TEMP·schema 권한을 회수하고 runtime 최소 권한과 합성 customer/order/marker만 만든다. 실제 개인정보·기존 플랫폼 DB를 사용하지 않는다.
- verify는 각 runtime 계정으로 CRUD·rollback·sequence·role/default 접근 경계, 다른 DB의 명시적 HBA/CONNECT 거부, 잘못된 TLS hostname 거부를 확인한다. Job 성공만 보지 말고 0 exit와 기대한 pass 결과를 확인한다. 신뢰 오류·인증 실패를 격리 성공으로 처리하지 않는다.
- **동일 source에서 labelled verify 성공 직후, 다른 장애를 주입하지 않은 상태에서** network-denied를 실행한다. 이 Pod는 client label이 없어야 하며 timeout이어야 한다. DB 자체 중단도 timeout을 만들 수 있으므로 negative Job 단독 결과는 증거가 아니다. 필요 시 곧바로 labelled verify를 다시 실행한다.
- 각 실행은 새 runId를 사용한다. 이전 완료 Job을 다시 apply하고 신규 시험이 실행됐다고 주장하지 않는다. Job은 retry 0, 최대 180초다. 마이그레이션/복원 Job만 charlie-db owner Secret을 사용하며 다른 Job은 charlie-apps runtime Secret만 사용한다.
- marker 모드는 `--set mode=marker --set markerLabel=before-failure-001`로 commit marker·UTC 시각을 남긴다. 중복 label은 실패한다. fingerprint 모드는 합성 테이블의 개수·정렬 hash와 최신 marker만 출력한다. DB 합계 용량은 별도 승인된 SQL로 확인한다.

## 초기 백업과 Pod 재생성

[최초 Backup 예제](../../examples/cnpg-initial-backup.yaml.example)의 실행 이름을 검토해 승인 후 생성한다. 일일 ScheduledBackup은 UTC 18시(KST 03시), 최초 immediate는 false다. Backup의 completed·시작/종료 시각과 S3 base backup/WAL 보관을 실제로 확인한다. API 상태만으로 복구 성공을 주장하지 않는다.

marker·fingerprint를 먼저 기록한 뒤 source label `cnpg.io/cluster=postgres-poc`의 Pod 하나만 승인된 정상 delete로 재생성한다. PVC/Cluster는 삭제하지 않으며 force/grace-period=0은 사용하지 않는다. 같은 PVC·새 Pod 및 재연결을 확인하고 marker/fingerprint·verify를 다시 검사한다. 요청부터 CRUD 검증 완료까지 10분 목표를 측정한다.

## 장애 시점 고정 archive와 full restore/PITR

1. 성공한 base backup 종료 이후 marker를 일정 간격으로 넣고 자연 WAL 보관을 관찰한다. marker 간격·clock 기준을 기록한다. 수동 WAL switch로 RPO가 좋아진 결과를 기본 목표 통과 근거로 삼지 않는다.
2. 모의 장애 시각 `t0`를 UTC로 기록한다. 원본은 계속 보존하되, `t0` 전에 완료된 base backup/WAL만 시험 입력으로 고정한다. [archive 고정 helper](../../scripts/cnpg-freeze-archive.py)의 도움말과 실패 조건을 확인한다. 실제 S3 읽기·copy 승인을 받은 경우에만 `--bucket`, `--run-id`, `--t0`, `--profile` 및 `--execute-approved`로 실행한다. 미실행 기본값은 AWS 호출을 하지 않는다. 조건부 CopyObject/PutObject 옵션을 지원하는 AWS CLI v2가 필요하며 구형 CLI에서 안전 옵션을 제거해 재시도하지 않는다. [조건부 복사 공식 문서](https://docs.aws.amazon.com/cli/latest/reference/s3api/copy-object.html)를 따른다.
3. helper는 source archive를 바꾸지 않고 빈 `restore/<run-id>/input/`에 복사한다. source ETag/완료 시각과 복사 조건이 바뀌거나 필요한 base backup/WAL이 없으면 실패한다. 부분 사본은 복원에 사용하지 않고 자동 정리하지 않는다. 로컬 manifest와 원격 `freeze-status.json` **모두** `state=complete`인지 확인한다. 이 상태는 전송 검증 완료이며 `restoreValidated=false`를 유지한다. 실제 복원 전에는 복구/RPO 통과가 아니다. 입력 사본·metadata manifest는 비공개로 관리한다.
4. 이전 restore Cluster/DatabaseRole/ObjectStore/PVC의 명시적 정리 승인과 source/backup 무변경 확인을 마친다. 고정 `postgres-poc-restore` Cluster가 없고 `cnpg.io/cluster=postgres-poc-restore`의 기존 PVC가 없음을 확인한다. Retain PV/EBS는 자동 삭제되지 않으며 UID·비용을 추적한다. **새 PVC**로 시작하고 이전 PV를 재결합하지 않는다. 고정 ServiceAccount는 그대로 사용하지만 runId는 매번 새로 만든다. runId만 바꾸고 기존 Cluster를 업데이트하는 것으로 재-bootstrap됐다고 간주하지 않는다.
5. [복원 chart](../../examples/cnpg-restore/Chart.yaml)를 실제 bucket·고정 입력과 같은 runId로 렌더링한다. targetTime이 없으면 frozen archive 끝까지, PITR이면 선택한 base backup 종료 시각 이후의 UTC targetTime을 지정한다. 입력은 `input/`, 출력은 `output/`, 원본 serverName은 postgres-poc, 신규 출력 serverName은 postgres-poc-restore다. empty archive 안전 검사는 끄지 않는다.

helper는 `t0` 이전의 **최신 완료 full backup**을 선택한다. manifest의 `backupEndTime`보다 이른 임의 target은 지원하지 않는다. before/after marker를 그 backup 종료 이후에 만들고, target과 t0 사이 더 새 일일 백업이 끝나 선택이 바뀌지 않았는지 확인한다. helper의 초기 PoC 제한은 단일 timeline·연속 WAL·객체당 5GiB 이하이며 timeline 전환/증분·대용량 복구는 별도 절차로 검토한다.

```bash
helm template cnpg-restore examples/cnpg-restore --namespace charlie-db \
  --set backup.bucketName=REPLACE_WITH_CNPG_BACKUP_BUCKET \
  --set runId=trial-001 > .generated/cnpg/restore-trial-001.yaml
# PITR은 별도 새 runId/새 PVC에서 --set-string targetTime=2026-11-03T05:00:00Z 등
# 실제 시험에서 선정한 시간으로 지정한다. 위 날짜를 그대로 사용하지 않는다.
```

6. 승격 완료 후 restore 대상 DatabaseRole이 **현재** owner/runtime Secret으로 재조정됐는지 확인한다. 과거 물리 catalog의 암호가 현재 Secret과 같다고 가정하지 않는다. restore CA의 공개 키만 앱 namespace에 배포하고 `clusterName=postgres-poc-restore`, mode=fingerprint로 접속·데이터를 확인한다. 전체 restore의 정확한 시점 검증 전에 verify(rollback이라도 sequence는 증가함)나 migration을 실행하지 않는다.
7. 지정 시점 이전 marker 존재·이후 marker 부재를 확인하고 hash·최신 marker 시각을 기록한다. `t0` 이후 source 업로드는 복구 입력에 추가하지 않는다. RPO는 `t0 - frozen archive에서 복구한 최신 marker 시각`으로 계산하되 marker 주기 한계를 함께 보고한다. 전체 복원 기능 성공과 RPO15분 통과를 별도 판정한다.
8. 전체 DB 손실 복구 RTO는 실행 시작부터 현재 Secret/TLS·데이터·접속 전환·CRUD 성공까지 60분 목표다. 탐지·사용자 승인 대기 시간은 따로 기록한다. 실제 서비스가 없는 이 PoC에서는 source를 삭제하지 않고 Job의 clusterName을 restore로 바꿔 endpoint 전환을 시험한다. 관리 플랫폼의 DSN을 바꾸지 않는다.

## A만 선택 복구

이 절차는 **합성 fixture의 app schema**만 지원하며 임의 서비스의 전체 DB/extension/large object 복구 도구가 아니다.

1. B의 source fingerprint를 저장한다. A에 새 marker를 넣어 목표 시점과 현재 시점을 구분한다.
2. A의 모든 writer·Job을 중지/완료시키고 기존 트랜잭션·연결이 남지 않음을 확인한다. 이 write fence는 recover-a Job이 자동으로 해주지 않는다. 실제 API/worker를 연결했다면 중단 방법을 별도 검토한다.
3. 위 절차대로 원하는 시점으로 임시 restore를 만든 뒤 현재 자격 증명과 TLS·restore A의 fingerprint를 확인한다.
4. source clusterName, service=a, mode=recover-a로 새 수동 Job을 렌더링·승인·실행한다. 이 Job은 restore의 app schema를 pg_dump로 **완전히 받은 뒤** source에 단일 트랜잭션으로 적용하고 grants만 다시 적용한다. seed를 다시 넣지 않는다. dump는 Pod의 제한된 임시 디스크에만 두고 종료 시 제거하며 외부 로그/문서로 내보내지 않는다. b 또는 restore를 쓰기 대상으로 지정한 렌더링은 거부된다.
5. A의 기대 fingerprint·시점, runtime 권한·sequence/CRUD와 B의 기존 fingerprint 보존을 확인한다. grants 또는 검증이 실패하면 A의 write fence를 풀지 않는다. B 데이터가 그대로여도 공유 CPU/I/O에 영향이 없다고 주장하지 않는다.
6. 검증 완료 후 A writer를 재개하고 실행 시작부터 결과 검증까지 60분 목표를 측정한다. 임시 restore 정리는 별도 승인한 대상만 수행한다.

## 수동 확인·실패·인수

- DB Ready, backup 실패/마지막 성공 26시간 초과, 활동 중 WAL 보관 지연 10분, PVC 여유 20% 주의/10% 중단, 연결 사용80%를 시험 중 확인한다. 자동 수집/경보·외부 알림은 이번 범위가 아니다.
- 실패하면 다음 sync/시험을 시작하지 않는다. 승인 범위에 포함한 경우에만 이번에 시작한 해당 Argo operation의 종료를 요청한다. 자동화 해제만으로 operation이 중지됐다고 표현하지 않는다. 일반 재시도도 원인·고정 revision·영향을 확인한 뒤 승인 범위에서 수행한다.
- Force/Replace/uninstall, CRD/namespace/원본 PVC 삭제, source archive 수정, SG/권한/TLS 약화는 우회책으로 사용하지 않는다. 생성된 리소스·부분 snapshot·Retain PV/EBS를 목록화하고 제거는 별도 승인한다.
- 새 runtime 암호 교체는 DB/app 두 사본·CNPG Role 재조정·재연결까지 한 절차로 수행한다. Sealed Secrets 개인키 복구 책임과 보존 위치는 별도로 인수하되 Git·일반 문서에 키를 기록하지 않는다.
- 결과에는 승인 대상·실제 수행 범위, commit/chart/image, marker·시각·hash·RPO/RTO, 임시 자원·연결 정리 및 남은 비용, 기존 플랫폼/제외 앱 보존과 미검증 항목을 기록한다. 민감 데이터·Secret·state·plan 내용은 기록하지 않는다.
