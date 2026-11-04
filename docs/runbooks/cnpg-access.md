# AWS CNPG PostgreSQL 접속 가이드

이 문서는 이미 설치한 AWS PoC DB에 접속하는 절차다. DB 배포·migration·데이터 주입·복원은 [운영 runbook](cnpg-poc.md)을 따른다. 명령 예시는 문법과 기존 설정을 확인한 안내이며 실제 SQL 접속 시험 결과가 아니다.

## 접속 대상과 준비 상태

2026-11-04 마지막 확인에서 source Ready1/1, Database2개·Role4개의 적용 상태가 최신이고 Argo 앱은 Synced/Healthy였다. 이번 작업에서는 SQL 접속·스키마 생성·runtime GRANT·합성 데이터 주입·실제 복원을 아직 실행하지 않았다. 앱 테이블 CRUD 준비 완료를 뜻하지 않는다.

| 항목 | 값 |
| --- | --- |
| AWS 계정 / 리전 | `525091405886` / `ap-northeast-2` |
| CLI profile | `charlie-admin` |
| workload EKS | `charlie-dev-workload` |
| DB namespace / CNPG Cluster | `charlie-db` / `postgres-poc` |
| PostgreSQL | `18.6` |
| 내부 읽기·쓰기 Host | `postgres-poc-rw.charlie-db.svc.cluster.local` |
| Port / TLS | `5432` / `sslmode=verify-full` |

외부 공개 DB endpoint는 구성하지 않았다. 같은 workload의 앱은 위 `-rw` Service를 사용하고, 로컬 운영자는 SSM으로 private EKS API에 연결한 뒤 DB Service를 port-forward한다. 다른 클러스터나 관리 플랫폼에서 이 DNS로 바로 접속하는 구성은 준비하지 않았다. Primary Pod IP를 접속 설정에 고정하지 않는다.

| 용도 | Database | PostgreSQL 사용자 | Kubernetes Secret | Secret namespace |
| --- | --- | --- | --- | --- |
| A migration·초기 확인 | `db_a` | `svc_a_owner` | `svc-a-owner` | `charlie-db` |
| A 앱 실행 | `db_a` | `svc_a_runtime` | `svc-a-runtime` | `charlie-db`, `charlie-apps` |
| B migration·초기 확인 | `db_b` | `svc_b_owner` | `svc-b-owner` | `charlie-db` |
| B 앱 실행 | `db_b` | `svc_b_runtime` | `svc-b-runtime` | `charlie-db`, `charlie-apps` |

Secret 키는 `username`·`password`다. owner는 자기 DB의 migration용이며 앱은 runtime을 사용한다. 전체 `max_connections=50`, owner별 연결 제한3개, runtime별10개다. PoC 시험의 앱 연결 합계 목표는10개 이하다. 접속 DB를 생략하면 사용자 이름과 같은 DB가 기본값이 되므로 항상 `db_a` 또는 `db_b`를 지정한다.

## 로컬 준비

- Python3·GNU Make·AWS CLI v2·session-manager-plugin·kubectl·psql이 PATH에 있어야 한다.
- 유효한 기존 AWS profile과 인가된 EKS·SSM·Kubernetes 조회/port-forward 권한이 필요하다. 계정 설정은 Git 제외 `config/account.local.json`을 사용한다. [예제](../../config/account.local.json.example)와 [기존 EKS 접근 안내](../../README.md#private-eks-접근과-argo-cd-설치)를 참고한다. 이 가이드는 권한을 생성하거나 바꾸지 않는다.
- DB 비밀번호와 DB 공개 CA를 준비한다. 현재 작업자의 파일은 아래 경로에 있고, 팀원은 자신의 인가된 파일 경로를 사용한다. 비밀번호/복구 개인키는 Git·채팅·접속 URI에 기록하지 않는다.

```text
/Users/timbergrizzzz/dev/charlie-infra/.generated/cnpg-poc-credentials/
├── svc-a-owner.password
├── svc-a-runtime.password
├── svc-b-owner.password
├── svc-b-runtime.password
└── postgres-poc-public-ca.pem
```

보관 디렉터리 권한은0700, 파일은0600이며 Git 제외 로컬 파일이다. `.password` 파일은 raw password이므로 libpq `PGPASSFILE`에 그대로 지정할 수 없다. DB TLS에는 `postgres-poc-public-ca.pem`을 사용한다. Sealed Secrets 봉인 인증서나 복구 개인키는 DB 접속에 사용하지 않는다.

## 로컬에서 접속하기

**터미널 세 개를 각각 infra 저장소 루트에서 시작한다.** 현재 컴퓨터의 기존 경로는 `/Users/timbergrizzzz/dev/charlie-infra`다. 별도 worktree/새 checkout에는 Git 제외 계정·인증서·암호 파일이 자동 복사되지 않는다. 아래 상대 경로가 있는 기존 checkout을 사용하거나 본인의 경로로 바꾼다.

### 1. 터미널1: workload EKS API 터널

```bash
make aws-tunnel CLUSTER=workload AWS_PROFILE=charlie-admin
```

SSM 세션 시작 안내가 나오면 이 터미널을 계속 열어 둔다. 기본 로컬 API 포트는11443이고 `.generated/access/workload.kubeconfig`가 생성된다. 글로벌 kubeconfig/current-context는 변경하지 않는다.

기존 CLI login 인증이 만료된 경우에는 본인이 다음 명령과 브라우저 인증을 완료한 뒤 터널을 다시 시작한다. Make 명령은 로그인을 자동 실행하지 않는다.

```bash
aws login --profile charlie-admin
```

### 2. 터미널2: DB 포트 전달

```bash
kubectl --kubeconfig .generated/access/workload.kubeconfig \
  --context charlie-dev-workload -n charlie-db \
  port-forward --address 127.0.0.1 svc/postgres-poc-rw 15432:5432
```

`Forwarding from 127.0.0.1:15432`가 나오면 이 터미널도 유지한다. DB 포트는 로컬 loopback에만 열린다. 15432가 이미 사용 중이면 예를 들어15433으로 바꾸고 다음 psql의 port도 같은 값으로 바꾼다. 다른 사용자의 프로세스를 종료하지 않는다.

### 3. 터미널3: PostgreSQL 접속

A owner로 최초 접속을 확인하는 예다. 비밀번호 프롬프트에 `svc-a-owner.password`에 보관한 값을 입력한다.

```bash
psql -X -W \
  "host=postgres-poc-rw.charlie-db.svc.cluster.local hostaddr=127.0.0.1 port=15432 dbname=db_a user=svc_a_owner sslmode=verify-full sslrootcert=.generated/cnpg-poc-credentials/postgres-poc-public-ca.pem connect_timeout=5"
```

`hostaddr`는 연결을 로컬 터널로 보내고 `host`는 서버 인증서의 호스트명 검증에 사용한다. host를 localhost로 바꾸거나 TLS 검증을 끄지 않는다. B는 `dbname=db_b`, `user=svc_b_owner`와 `svc-b-owner.password`를 사용한다. runtime을 확인하려면 해당 서비스의 runtime 사용자·비밀번호를 사용하되, 스키마/테이블 권한 검증은 migration/grant 이후에 한다.

psql에서 다음 명령으로 현재 대상·사용자와 연결 정보를 확인한다. 접속이 성공하면 Database가 `db_a`, 사용자가 `svc_a_owner`여야 한다. 아래는 실행 예시이며 이번 문서 작성 중에는 SQL을 실행하지 않았다.

```sql
\conninfo
SELECT current_database(), current_user;
\q
```

### 4. 종료

psql에서 `\q` → 터미널2의 port-forward에 Ctrl+C → 터미널1의 SSM 터널에 Ctrl+C 순서로 종료한다. 선택한 DB Pod가 종료되면 port-forward도 종료될 수 있으므로 같은 Service 명령을 다시 실행한다. port-forward는 Kubernetes 관리 경로이며 앱 Pod의 NetworkPolicy 격리 시험을 대체하지 않는다.

## 클러스터 내부 앱에서 접속하기

앱은 `charlie-apps` namespace에 두고 Deployment의 `spec.template.metadata.labels`에 `charlie.dev/cnpg-client: "true"`를 지정한다. Deployment 자체 metadata의 label만 추가하면 Pod에는 적용되지 않는다. `charlie-db`의 클라이언트도 같은 label이 필요하고 다른 namespace는 현재 허용 대상이 아니다.

해당 namespace의 runtime Secret을 `secretKeyRef` 또는 보호된 파일로 전달한다. 앱 namespace의 공개 CA 전용 Secret `postgres-poc-ca`에서 `ca.crt`만 `/ca/ca.crt` 등에 마운트한다. DB namespace의 CA Secret 전체에는 개인키가 포함될 수 있으므로 전체를 복사하지 않는다. 기존 [검증 Job 예제](../../examples/cnpg-poc/templates/job.yaml)의 Secret·CA 마운트 구성을 참고할 수 있다.

```text
PGHOST=postgres-poc-rw.charlie-db.svc.cluster.local
PGPORT=5432
PGDATABASE=db_a
PGUSER=svc_a_runtime
PGPASSWORD=<svc-a-runtime Secret의 password>
PGSSLMODE=verify-full
PGSSLROOTCERT=/ca/ca.crt
```

B는 DB를 `db_b`, 사용자를 `svc_b_runtime`, 인증 Secret을 `svc-b-runtime`으로 바꾼다. 위 값은 연결 설정이며 실제 앱 Deployment나 Secret을 수정하는 명령이 아니다. schema·table·sequence와 runtime GRANT는 [migration 절차](cnpg-poc.md#수동-migration기능격리-시험)를 실행한 뒤 확인한다.

## Argo CD에서 상태 보기

management EKS의 `argocd` namespace에 있는 `charlie-workload-cnpg-poc` 앱을 찾는다. DB 접속 터널과 별도로 **관리용 터미널 두 개**를 사용한다.

첫 터미널:

```bash
make aws-tunnel CLUSTER=management AWS_PROFILE=charlie-admin
```

두 번째 터미널:

```bash
make argo-port-forward AWS_PROFILE=charlie-admin
```

https://localhost:8080 에서 기존 Argo 계정으로 로그인한다. 기본 관리 API 포트10443과 workload11443은 병행할 수 있다. 화면에서 상태를 확인하는 것과 Sync 실행은 별도 작업이다.

## 오류 확인 순서

| 증상 | 확인할 내용 |
| --- | --- |
| AWS 인증 만료 | 기존 profile의 CLI login 갱신 후 터널 재실행 |
| kubeconfig 없음 / API11443 연결 거부 | 저장소 루트, 터미널1의 SSM 연결, `.generated/access/workload.kubeconfig` 경로 |
| 로컬15432 사용 중 | 빈 로컬 포트로 변경하고 port-forward와 psql에 같은 값 적용 |
| TLS hostname 검증 실패 | 실제 DB DNS를 host에, 127.0.0.1을 hostaddr에 지정했는지 확인 |
| CA 파일을 찾지 못함 / 인증서 검증 실패 | DB 공개 CA 파일의 경로와 대상 Cluster가 일치하는지 확인. 봉인 인증서와 구분 |
| password authentication failed | db_a/db_b·owner/runtime·비밀번호 파일이 같은 서비스/용도인지 확인 |
| schema/table 접근 거부 또는 테이블 없음 | migration·runtime GRANT 실행 여부 확인. 현재 작업에서는 미실행 |
| 앱에서만 접속 timeout | 실제 Pod namespace·client label·Secret·CA 마운트 확인. 관리 port-forward 성공만으로 앱 경로 통과를 단정하지 않음 |
| 기존 port-forward 연결 종료 | DB Pod/터널 상태를 확인하고 같은 Service 대상으로 재실행 |

## 관련 자료

- [전체 설치·복구 runbook](cnpg-poc.md), [배포 상태 기록](../plans/cnpg-aws-poc.md)
- [기존 로컬 EKS 접근 결정 ADR0014](../adr/0014-local-eks-access-commands.md)
- [libpq host/hostaddr·TLS 연결 옵션](https://www.postgresql.org/docs/18/libpq-connect.html#LIBPQ-PARAMKEYWORDS)
- [kubectl port-forward 동작](https://kubernetes.io/docs/reference/kubectl/generated/kubectl_port-forward/)
