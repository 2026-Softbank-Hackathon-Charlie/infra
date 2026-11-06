# 0054. 공유 VPC의 S3 요청을 Gateway endpoint로 보낸다

- 날짜: 2026-11-05
- 상태: 제안
- 관련 ADR: [ADR 0035](0035-app-storage-and-secrets-infra.md)

## 배경과 제약

공유 VPC(`10.40.0.0/16`)에는 VPC endpoint가 없다. 사설 subnet 4개(`management-0`, `management-1`, `workload-0`, `workload-1`)의 route table은 `0.0.0.0/0`을 AZ별 NAT gateway 2개로 보낸다. EKS 노드가 ECR 이미지를 받을 때 layer는 서울 리전 S3에서 내려오고, 앱 버킷(`hopcharlie-eks-*`), Loki, CNPG 백업도 서울 S3를 쓴다. 이 트래픽은 모두 NAT를 지나므로 GB당 처리 요금이 붙는다. S3 Gateway endpoint는 요금이 없다.

endpoint를 지나면 S3가 보는 출발지 주소가 NAT 공인 IP에서 VPC 사설 IP로 바뀐다. 그래서 출발지 IP에 기대는 정책이 있는지 2026-11-05에 읽기 전용으로 확인했다.

- 계정의 버킷 5개는 모두 `ap-northeast-2`에 있다. 3개(`charlie-dev-cnpg-...`, `charlie-dev-loki-...`, `hopcharlie-tfstate-...`)의 버킷 정책은 `aws:SecureTransport` 조건만 쓰고, 나머지 2개(`charlie-tfstate-...`, `hopcharlie-eks-moimroom`)는 버킷 정책이 없다. 어느 버킷에도 `aws:SourceIp`, `aws:SourceVpce`, `aws:SourceVpc` 조건은 없다. S3 Access Point는 없다.
- 계정에 적용되는 조직 정책도 확인했다. SCP는 `FullAWSAccess`와 `ManagedAccountSecurityControlPolicy`(조직 탈퇴와 관리 역할 변경 차단), RCP는 `RCPFullAWSAccess`와 `ManagedAccountResourceControlPolicy`(`aws:PrincipalOrgID`, `aws:SourceOrgID`로 조직 밖 주체 차단)다. 네트워크 조건은 없다.
- IAM 사용자, 역할, 그룹의 inline 정책과 고객 관리형 정책 8개에도 위 조건이 없다. 앱 IAM 사용자의 permissions boundary(`hopcharlie-app-boundary`)는 `s3:ResourceAccount`만 쓰고, moimroom 사용자의 inline 정책은 조건이 없다.
- infra와 platform 코드에는 `aws:SourceIp`나 `aws:SourceVpc` 조건을 만드는 곳이 없다.
- VPC 안 인스턴스 7대(EKS 노드, SSM bridge, 앱 router)는 모두 보안 그룹에서 `0.0.0.0/0`으로 나가는 TCP 443을 허용하고, network ACL은 기본값(전체 허용)이다. 목적지 IP는 그대로 S3 공인 대역이므로 NetworkPolicy 판단도 바뀌지 않는다.
- 앱 CodeBuild 프로젝트는 VPC 밖에서 돌기 때문에 이 변경과 관계없다.

## 검토한 대안

- 지금처럼 NAT 사용: 바꿀 것이 없지만 이미지 pull과 S3 트래픽의 NAT 처리 요금이 계속 붙는다.
- S3 Interface endpoint: 다른 리전이나 VPC 밖에서도 쓸 수 있지만 시간당 요금과 GB당 요금이 붙는다. 이 VPC에는 필요하지 않다.
- ECR API(`ecr.api`, `ecr.dkr`) Interface endpoint 추가: NAT 요금을 더 줄이지만 endpoint마다 AZ별 시간 요금이 있고 보안 그룹과 private DNS를 다뤄야 한다. 이번 범위에서 제외한다.
- S3 Gateway endpoint: 요금이 없고 route table 연결만 있으면 된다. 이 방법을 선택한다.

## 결정과 이유

foundation의 [network.tf](../../terraform/environments/aws/dev/foundation/network.tf)에 서울 리전 S3 Gateway endpoint(`com.amazonaws.ap-northeast-2.s3`)를 만들고, NAT 경로를 가진 사설 route table 4개에 연결한다. public route table과 VPC 기본 route table에는 사설 workload가 없으므로 연결하지 않는다. 태그는 다른 network 리소스와 같은 소유 태그(`Component = network`)를 붙인다.

endpoint 정책은 기본값(전체 허용)을 쓴다. 접근 통제는 지금처럼 IAM 정책과 버킷 정책이 맡는다. 정책을 좁히면 ECR layer 버킷처럼 AWS가 소유한 버킷까지 따로 허용해야 해서, 지금은 이득보다 장애 위험이 크다.

endpoint route는 서울 S3 주소 대역에만 걸린다. 다른 리전 버킷으로 가는 요청은 클라이언트가 그 버킷 리전의 endpoint(`s3.<리전>.amazonaws.com`)를 부를 때만 지금처럼 NAT로 나간다. 서울 endpoint로 다른 리전 버킷을 부르면 요청이 Gateway endpoint를 지나고 실패할 수 있다. 지금 계정의 버킷은 모두 서울이다. GKE에서 Tailscale 라우터를 거쳐 이 VPC로 들어오는 트래픽은 Gateway endpoint를 쓸 수 없다(VPC 밖으로 확장되지 않음).

## 영향과 후속 작업

- 기대 효과: 노드의 ECR layer 다운로드와 앱, Loki, CNPG의 S3 트래픽이 NAT 처리 요금 없이 AWS 내부 경로로 간다.
- 단점과 위험: route가 추가되는 순간 S3로 열려 있던 TCP 연결이 끊긴다(AWS 문서). 그 순간 진행 중이던 업로드나 이미지 pull은 한 번 실패하고 재시도된다. 앞으로 S3 정책에서 NAT IP를 `aws:SourceIp`로 허용하면 VPC 안의 요청과 맞지 않는다. 이때는 `aws:SourceVpce`나 `aws:VpcSourceIp`를 쓴다.
- main에 merge하면 CI가 foundation을 자동 apply한다. CI 역할(`charlie-dev-github-terraform`)에는 AdministratorAccess가 붙어 있어 VPC endpoint 권한을 따로 주지 않는다. 범위를 좁힌 `charlie-dev-foundation-network` 정책에는 VPC endpoint 동작이 없으므로, AdministratorAccess를 떼는 날에는 그 정책에 함께 추가해야 한다.
- 검증: endpoint가 `available`이고 사설 route table 4개에 S3 prefix list route가 생겼는지 본다. moimroom 사진 업로드와 다운로드, 새 Pod의 ECR 이미지 pull이 되는지 확인한다. CNPG Cluster의 `ContinuousArchiving` 조건이 `True`이고 마지막 WAL 보관 시각이 apply 이후로 넘어가는지, Loki 로그에 S3 flush 오류가 없는지 본다.
- 되돌리기: 이 리소스를 지우는 PR을 merge하면 route가 빠지고 NAT 경로로 돌아간다. 이때도 열린 연결은 한 번 끊긴다.

## 관련 자료

- [network.tf](../../terraform/environments/aws/dev/foundation/network.tf), [mock 검사](../../terraform/environments/aws/dev/foundation/tests/network.tftest.hcl)
- [Gateway endpoints for Amazon S3](https://docs.aws.amazon.com/vpc/latest/privatelink/vpc-endpoints-s3.html)
