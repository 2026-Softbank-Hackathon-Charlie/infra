# 0099. hopai가 온프레미스 k3s를 리허설 대상으로 쓴다

- 날짜: 2026-11-08
- 상태: 제안
- 관련 ADR: [0089](0089-run-ai-recommendations-without-cluster-credentials.md)

## 배경과 제약

GKE → onprem 이전을 AI 계획으로 만들면 hopai Job이 원본 측정 뒤 `stat /config/onprem.yaml: no such file or directory`로 실패했다. hopai는 SSM에서 `hopai/kube-eks`, `hopai/kube-gke`만 받고, 실행 인자도 `-env gke`, `-env eks`만 넘긴다. 리허설은 대상 클러스터에서 `-sandbox-kubeconfig /config/$TO.yaml`로 샌드박스를 만들므로, 대상이 onprem이면 항상 실패한다.

- 확인된 사실: 플랫폼의 onprem kubeconfig(`/hopcharlie/kube/onprem`)는 읽기 위주 권한이라([hopcharlie-platform-access.yaml](../../clusters/onprem-dev-workload/hopcharlie-platform-access.yaml)) 샌드박스를 만들 수 없다.
- 확인된 사실: 관리 EKS에서 노트북 k3s API로 가는 길은 Tailscale egress 프록시 `k3s-onprem-api`(6443)뿐이다. `charlie-platform`의 `application-boundary`는 443만 밖으로 보내고, 프록시의 NetworkPolicy `k3s-onprem-api-proxy`(손으로 만든 것, 이 레포에 없음)는 플랫폼 API와 Argo CD만 받는다.
- 미확인: 샌드박스 리허설이 k3s(local-path StorageClass, 노드 1대)에서 끝까지 도는지는 실제 실행으로 확인해야 한다.

## 검토한 대안

- 대안 A: 시연에서 onprem 대상 AI 계획을 쓰지 않는다. 변경이 없지만, onprem 이전을 AI로 계획하는 시나리오를 보여 줄 수 없다.
- 대안 B: 플랫폼의 `/hopcharlie/kube/onprem`을 그대로 hopai에 준다. 권한이 모자라 샌드박스를 만들 수 없어 제외했다.
- 대안 C: 노트북 k3s에 hopai 전용 cluster-admin ServiceAccount를 만들고, 그 토큰으로 만든 kubeconfig를 `/hopcharlie/hopai/kube-onprem`에 둔다. eks, gke의 sandbox-admin kubeconfig와 같은 수준이다.

## 결정과 이유

대안 C를 선택한다. 사용자가 2026-11-08 시연 준비 중 onprem 대상 AI 검증을 요청했다.

- 노트북 k3s: [hopai-sandbox-access.yaml](../../clusters/onprem-dev-workload/hopai-sandbox-access.yaml)을 손으로 적용한다(`kube-system/hopai-sandbox-admin`, cluster-admin).
- SSM: `/hopcharlie/kube/onprem`의 서버, CA, tls-server-name을 그대로 쓰고 사용자 토큰만 위 계정의 토큰으로 바꾼 kubeconfig를 `/hopcharlie/hopai/kube-onprem`(SecureString)에 둔다. hopai Pod 역할은 이미 `hopai/*`를 읽을 수 있다.
- 차트: hopai fetch가 `hopai/kube-onprem`이 있으면 `/config/onprem.yaml`로 받고, 실행이 그 파일이 있으면 `-env onprem=/config/onprem.yaml`을 더한다. 없으면 지금과 같다.
- 네트워크: NetworkPolicy `hopcharlie-hopai-onprem-api`로 hopai Pod가 `hopcharlie-db-network`의 `k3s-onprem-api` 프록시 Pod 6443으로 나가게 한다. 프록시 쪽 `k3s-onprem-api-proxy`의 ingress에 hopai Pod를 손으로 더한다.

## 영향과 후속 작업

- 기대 효과: GKE/EKS → onprem 이전을 AI 계획과 샌드박스 리허설로 검증할 수 있다.
- 단점과 위험: hopai(AI 실행기)가 노트북 k3s의 cluster-admin 토큰을 갖는다. 토큰은 만료가 없는 ServiceAccount 토큰이다. AI 계정 자체는 샌드박스 토큰(최소 권한)으로 돌지만, 관리 토큰이 hopai Pod 메모리 볼륨에 있다.
- 필요한 후속 작업 및 검증:
  - 노트북에서 매니페스트 적용, SSM 저장, 프록시 NetworkPolicy 수정(운영자)
  - GKE → onprem AI 계획 실행으로 리허설 완주 확인
  - 해커톤 뒤 `hopai-sandbox-admin` 삭제와 `/hopcharlie/hopai/kube-onprem` 삭제

## 관련 자료

- [hopai.yaml](../../helm/charts/hopcharlie-platform/templates/hopai.yaml)
- [hopai-sandbox-access.yaml](../../clusters/onprem-dev-workload/hopai-sandbox-access.yaml)
- ai-sandbox README 12장(`-sandbox-kubeconfig`)
