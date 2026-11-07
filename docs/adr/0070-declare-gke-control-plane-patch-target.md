# 0070. GKE 제어 영역 후속 패치 목표를 Terraform에 선언

- 날짜: 2026-11-07
- 상태: 채택
- 관련 ADR: [0013](0013-add-gcp-workload.md), [0068](0068-record-three-node-gke-workload-capacity.md), [0069](0069-limit-aws-auto-apply-to-deployment-inputs.md)

## 배경과 제약

GKE `moimroom-mig/moimroom-mig-web`의 옛 Service UID에 대해 `service-controller`가 `implemented by alternate to cloud provider` 삭제 오류를 반복한다. 점검 당시 현재 Service의 별도 L4 컨트롤러와 백엔드는 정상이었고, Service 이름·UID·VIP로 확인한 범위에서 분리된 옛 로드밸런서 잔여 자원은 발견되지 않았다.

공개 Kubernetes 코드에서는 옛 Service 정리 오류가 새 UID의 캐시 갱신보다 먼저 반환된다. 관측과 일치하는 원인 가설이지만 관리형 GKE의 내부 캐시는 직접 확인하지 않았다. 공개 GKE 변경 기록에서 이 오류의 수정 내역을 확인하지 못했고, Kubernetes cloud-provider 1.36.5에도 해당 삭제 경로가 남아 있다. 제어 영역 갱신을 영구적인 오류 수정으로 보장할 수 없다.

읽기 전용 조회 시 현재 제어 영역과 노드 버전은 `1.36.4-gke.2046003`, 채널은 `REGULAR`이다. `asia-northeast3-a`의 같은 채널에서 `1.36.4-gke.2326000`과 `1.36.4-gke.2377000`을 제공한다. 사용자는 `1.36.4-gke.2377000`을 목표로 한 Terraform PR과 실제 plan 검토를 요청했으며 실제 업그레이드·이전·AWS strict 적용은 허용하지 않았다.

## 검토한 대안

- 현재 Service나 로드밸런서를 삭제·재생성한다. 정상 연결에 영향을 주며 옛 UID 재시도를 해결한다는 보장이 없어 제외한다.
- CLI로만 제어 영역을 올린다. 팀이 목표 버전과 변경 근거를 Git에서 검토하기 어려워 제외한다.
- 기존 GCP workload 루트의 제어 영역 최소 버전만 명시한다. 기존 채널과 노드 풀 선언을 유지하면서 검토 가능한 변경을 만들 수 있어 선택한다.

## 결정과 이유

`google_container_cluster.workload.min_master_version`을 `1.36.4-gke.2377000`으로 선언한다. 특정 dev 클러스터의 목표 한 개이므로 새 입력 변수나 공용 `config/project.json` 변경은 추가하지 않는다. `min_master_version`은 하한이며 GKE 자동 업그레이드를 막는 고정 상한이 아니다.

별도 `google_container_node_pool.workload`의 `version`을 지정하지 않는다. 기존 노드 3대, `e2-standard-2`, `REGULAR` 채널과 `auto_upgrade=true`·`auto_repair=true`를 유지한다. 이 선언은 노드 버전을 직접 변경하지 않지만 이후 GKE 노드 자동 업그레이드를 막지 않는다. 유지보수 정책 변경은 별도 결정 대상이다.

## 영향과 후속 작업

- 기대 효과: 제어 영역 목표를 Git에서 검토하고, 적용 전에 기존 GCS state와 운영 입력으로 전체 workload plan을 확인할 수 있다.
- 단점과 위험: zonal GKE 제어 영역 갱신 중 API와 스케줄링 등 관리 작업이 일시적으로 불가능할 수 있다. 기존 워커가 계속 동작하더라도 서비스 무중단이나 오류 해소를 보장하지 않는다.
- 검증: mock 검사에서 제어 영역 목표와 기존 채널·노드 설정을 확인한다. 실제 plan은 `-target`이나 refresh 생략 없이 수행하고 제어 영역 외 변경·노드 생성/삭제/교체·다른 drift가 있으면 별도로 보고한다. state·운영 입력·저장된 plan은 Git이나 PR에 게시하지 않는다.
- 실제 적용: PR 게시와 plan 검토만 진행한다. GCP workflow의 main 수동 plan/apply 제한과 AWS 자동 apply 경로 판정은 유지한다. 병합은 GCP 적용 승인이 아니며, 실제 업그레이드는 별도 승인이 필요하다.
- 적용 후 검증이 허용되면 옛 UID 오류의 횟수·발생 시각이 증가하지 않는지와 현재 Service UID·VIP, HTTP 및 백엔드 상태를 확인한다. 실제 이전·strict 적용과 기존 일시중지 goal의 재개는 포함하지 않는다.

## 관련 자료

- [GKE 클러스터·노드 풀](../../terraform/environments/gcp/dev/workload/main.tf), [mock 검사](../../terraform/environments/gcp/dev/workload/tests/workload.tftest.hcl)
- [GCP 운영 절차](../gcp-deployment.md), [GCP workflow](../../.github/workflows/terraform-gcp.yml)
- [GKE 패치 변경 기록](https://docs.cloud.google.com/kubernetes-engine/docs/changelogs/1.36)
- [공개 삭제 처리 코드](https://github.com/kubernetes/cloud-provider/blob/v0.36.5/controllers/service/controller.go#L856)
- [Google provider 7.46.2 문서](https://github.com/hashicorp/terraform-provider-google/blob/v7.46.2/website/docs/r/container_cluster.html.markdown)
- [GKE 제어 영역 갱신](https://docs.cloud.google.com/kubernetes-engine/docs/how-to/upgrading-a-cluster#upgrade_the_clusters_control_plane)
