# 0052. DB 네트워크 차트는 DNS(53)를 목적지 제한 없이 허용한다

- 날짜: 2026-11-05
- 상태: 제안
- 관련 ADR: [0048](0048-connect-cloud-databases-with-scoped-migration-access.md)

## 배경과 제약

`hopcharlie-db-network` 차트는 네임스페이스 전체를 default-deny로 막고, Tailscale operator와 proxy에 DNS(kube-system의 `k8s-app: kube-dns` 파드), TCP443만 연다. proxy는 여기에 더해 가변 목적지 UDP를 연다.

2026-11-05 운영 moimroom 이전 중 GKE operator가 새 ingress proxy를 만들지 못했다. 오류는 `lookup controlplane.tailscale.com: i/o timeout`였다. 확인한 사실:

- GKE 클러스터에 `node-local-dns`(NodeLocal DNSCache) DaemonSet이 있다. 이 파드는 hostNetwork(노드 IP)로 돈다
- operator 파드의 resolver는 kube-dns ClusterIP `10.82.0.10`이다
- 같은 네임스페이스의 proxy 파드에서는 같은 이름이 조회됐다. proxy는 UDP를 목적지 제한 없이 허용하므로 DNS도 통과한다
- operator 파드에만 53 포트를 목적지 제한 없이 여는 정책을 추가하자 1분 안에 proxy가 만들어졌다(`ProxyCreated`)
- operator는 설치 직후(약 5시간 전)에는 동작했다. 그때는 이미 연결과 토큰이 있어 DNS가 필요 없었던 것으로 추정한다(확인 안 함)

NodeLocal DNSCache가 응답하는 DNS 패킷은 kube-dns 파드 selector와 일치하지 않는다. EKS 클러스터에는 NodeLocal DNSCache가 없어 이 문제가 없었다.

## 검토한 대안

- kube-dns 파드 selector 유지 + NodeLocal DNSCache 주소(`169.254.20.10/32`)와 노드 CIDR 추가: 범위가 가장 좁다. 그러나 GKE Dataplane이 정책을 어느 주소로 평가하는지 확인하지 못했고, 클러스터마다 노드 CIDR이 달라 값이 늘어난다
- 클러스터별 값으로 DNS 목적지를 받기: 정확하지만 지금 확인한 값이 없고 설정 실수가 곧 장애가 된다
- 53 포트를 목적지 제한 없이 허용: 선택한다. 실제로 operator DNS 장애를 해소한 형태다. 새 허용 범위는 operator의 임의 목적지 UDP/TCP 53과 proxy의 임의 목적지 TCP 53이다. proxy의 UDP 53은 기존 가변 목적지 UDP 허용에 이미 포함돼 있다

## 결정과 이유

- 차트의 공통 control egress(`db-network.controlEgress`)에 UDP/TCP 53을 목적지 제한 없이 허용하는 규칙을 더한다. 기존 kube-dns selector 규칙과 TCP443 규칙은 그대로 둔다
- operator와 proxy 모두에 적용된다

## 영향과 후속 작업

- 기대 효과: NodeLocal DNSCache가 있는 클러스터(GKE)에서도 operator가 Tailscale API에 접속한다
- 단점과 위험: operator는 임의 목적지 UDP/TCP 53에, proxy는 추가로 임의 목적지 TCP 53에 연결할 수 있다. operator와 proxy의 DNS를 통한 정보 유출 경로가 넓어진다. 데이터 경로(TCP5432)와 grant는 바뀌지 않는다
- 운영 상태: GKE에는 operator DNS 장애를 해소한 임시 정책 `hopcharlie-db-network/db-network-operator-node-local-dns`(operator 전용)가 수동으로 적용돼 있다. 이 PR을 merge하고 `helm upgrade --server-side=false`로 반영한 뒤 임시 정책을 지운다
- 필요한 후속 작업 및 검증:
  1. 반영 뒤 GKE operator 로그에 DNS 오류가 없는지, 새 proxy가 만들어지는지 확인한다
  2. 범위를 좁히려면 GKE에서 `169.254.20.10/32`만 허용했을 때 동작하는지 따로 시험한다

## 관련 자료

- [helm/charts/hopcharlie-db-network/templates/_helpers.tpl](../../helm/charts/hopcharlie-db-network/templates/_helpers.tpl)
- [scripts/tests/test-db-network.py](../../scripts/tests/test-db-network.py)
- [docs/runbooks/aws-gcp-db-migration.md](../runbooks/aws-gcp-db-migration.md)
- GKE: [NodeLocal DNSCache](https://cloud.google.com/kubernetes-engine/docs/how-to/nodelocal-dns-cache)
- 이전 기록: migration-engine `docs/test-records` 2026-11-05 운영 moimroom 실제 이전
