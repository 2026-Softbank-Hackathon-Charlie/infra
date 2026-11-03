# 0031. 기존 AWS PostgreSQL PoC 구성을 GKE에 재사용

- 날짜: 2026-11-04
- 상태: 채택. 실제 적용·Git 게시는 구체적인 승인 후 수행한다.
- 관련 ADR: [0005](0005-scope-aws-cnpg-poc.md), [0006](0006-back-up-cnpg-poc-with-barman.md), [0013](0013-add-gcp-workload.md)

## 배경과 제약

사용자는 이미 AWS 워크로드에 있는 `charlie-db/postgres-poc`과 기반 구성을 GCP에 맞추도록 지정했다. 두 워크로드 전체 조회에서 AWS는 PostgreSQL 18.6 단일 인스턴스·20Gi와 관리 도구, GKE는 관리 도구만 확인했다. 모임룸 앱과 새 AWS 원본 생성은 이번 범위가 아니다.

GKE에는 CNPG 1.30.1·Workload Identity·Calico·NodeLocal DNSCache가 준비돼 있다. DB namespace·Retain 디스크·Barman·cert-manager는 추가가 필요하다. 기존 `charlie-apps`의 Sealed Secrets와 앱 파일 저장소는 보존한다.

## 검토한 대안

- AWS 매니페스트 그대로 복사: AWS 디스크·메타데이터 주소·S3 인증과 operator label이 GKE에서 맞지 않는다.
- 별도 GCP 차트 작성: 동일 DB·권한·SQL 정의가 갈라진다.
- 기존 `cnpg-poc` 차트에 환경 값 추가: AWS 기본 렌더를 보존하며 선택한다.

## 결정과 이유

| 항목 | 재사용 또는 GCP 값 |
| --- | --- |
| DB | 같은 PG18.6 digest, 단일 인스턴스, 20Gi, 요청 500m/1Gi·한도 1CPU/2Gi |
| 구조·계정 | `charlie-db/postgres-poc`, `db_a/db_b`, owner/runtime 4개, 기존 SQL fixture |
| 디스크 | GKE CSI `pd-balanced`, `cnpg-gcp-pd-retain`, 삭제 후 볼륨 보존 |
| 백업 | 같은 Barman 0.15.1·gzip·7일 보존·03:00 KST 스케줄, 전용 GCS |
| 인증 | 전용 GSA와 `charlie-db/postgres-poc` KSA의 Workload Identity, 버킷 한정 IAM |
| 접근 | 기존 네트워크 격리·TLS·계정 분리를 유지하고 GKE operator·DNS·메타데이터 경로만 반영 |

- 차트의 Kubernetes 지원 범위에 실제 GKE 1.36을 포함한다. AWS 1.35 기본 렌더는 객체 단위로 비교한다.
- cert-manager와 Barman은 AWS에서 쓰는 고정 차트·이미지를 재사용한다. 기존 CNPG operator와 GKE·노드는 재생성하지 않는다.
- GCP bootstrap이 백업 버킷·GSA·WI 신뢰·버킷 IAM 2개를 관리한다. 활성화 입력은 운영 tfvars에 유지한다. 개인키·HMAC은 발급하지 않는다.
- DB 암호 4개는 GKE용으로 생성해 Secret에만 전달한다. Git·평문 파일·Terraform state에 보관하지 않는다. CNPG가 별도의 `charlie-db`에서 DB를 관리하므로 기존 Sealed Secrets의 감시 범위는 확대하지 않는다.
- 기존 SQL fixture로 새 GCP DB 구조와 합성 데이터를 초기화한다. AWS의 기존 행 데이터 복사·클러스터 간 복제는 별도 단계다.

## 영향과 후속 작업

- 효과: 공통 DB·계정·백업 정의를 유지하면서 디스크와 인증은 각 클라우드의 기본 기능을 쓴다.
- 위험: cert-manager/Barman 설치는 CRD·클러스터 RBAC·webhook을 포함하며, DB는 20Gi 디스크와 백업 저장 비용을 사용한다. 단일 인스턴스는 고가용성 구성이 아니다.
- 승인 범위: Git 게시·병합, 기존 bootstrap state 대상 계획과 신규 5자원 적용, 새 namespace·컨트롤러·DB·Secret 및 검증 Job을 구체적으로 확인한다. 대상 밖 변경은 중단한다.
- 완료 기준: DB Ready, A/B 초기화·owner/runtime 권한·네트워크 격리 검증, GCS 첫 백업 성공과 임시 Job·접속 정리. 노션 태스크는 실제 완료 후 한 번 반영한다.

## 관련 자료

- [공통 DB 차트](../../helm/charts/cnpg-poc), [기존 SQL 검증](../../examples/cnpg-poc)
- [GCP 환경 값](../../clusters/gcp-dev-workload/values/cnpg-poc.yaml), [namespace](../../clusters/gcp-dev-workload/namespaces.yaml)
- [백업 Terraform](../../terraform/bootstrap/gcp/cnpg-poc-backup.tf)
- [Barman GCS 인증](https://cloudnative-pg.io/plugin-barman-cloud/docs/object_stores/#gke-workload-identity)
- [GKE 메타데이터 네트워크 정책](https://docs.cloud.google.com/kubernetes-engine/docs/how-to/network-policy#network_policy_and_workload_identity_federation_for_gke)
- [NodeLocal DNSCache 정책](https://docs.cloud.google.com/kubernetes-engine/docs/how-to/nodelocal-dns-cache#network_policy_with_nodelocal_dnscache)
