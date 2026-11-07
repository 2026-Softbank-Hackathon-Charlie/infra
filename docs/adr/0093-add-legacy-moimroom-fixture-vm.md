# 0093. 기존 앱 가져오기 시험용 moimroom-legacy VM을 기본 VPC에 둔다

- 날짜: 2026-11-07
- 상태: 제안
- 관련 ADR: platform ADR 0042(플랫폼 밖 compose 앱 가져오기)

## 배경과 제약

platform ADR 0042의 범위는 "VM 한 대에서 docker compose로 돌던 앱"을 조사하고 우리 클러스터로 옮기는 것이다. 시험하려면 우리 플랫폼에 한 번도 올라온 적 없는 고객 앱처럼 보이는 대상이 실제 AWS에 있어야 한다. ADR 0042는 시연 앱을 moimroom 사본으로 정했다. 파일은 AWS S3 버킷에 두고, compose에 TLS 리버스 프록시를 더해 `moimroom-legacy.hopcharlie.dev`(VM을 가리키는 개별 A 레코드)로 연다.

- 계정의 EC2 vCPU 한도는 16이고 12를 쓰고 있다(t3.small 2개를 더하면 14).
- 배포 workflow는 조직 self-hosted 러너(label `hopcharlie`)에서 돈다. GitHub 호스트 러너는 결제 문제로 쓸 수 없다.
- infra ADR 0036에 따라 Cloudflare 레코드는 사람이 만든다.

## 검토한 대안

- 공유 VPC 안에 VM을 둔다: 고객 VM이 우리 네트워크 안에 있게 되어 조사 결과(VPC, SG, 경로)가 실제 고객 상황과 달라진다. 제외.
- 기존 platform/aws 같은 큰 root에 넣는다: 시험이 끝나면 지울 자원이 다른 자원과 state를 공유한다. 제외.
- 앱이 instance profile로 S3에 접근한다: 일반 compose 앱은 `.env`의 접근 키를 쓰는 경우가 많고, ADR 0042가 다루는 것도 그 형태다. 이번에는 버킷 하나로 제한한 IAM 사용자 접근 키를 쓴다.

## 결정과 이유

`terraform/fixtures/aws/legacy-moimroom/`을 독립 root로 둔다. state는 `charlie-tfstate-525091405886-ap-northeast-2`의 `fixtures/aws/legacy-moimroom/terraform.tfstate`에 둔다.

- 서울 기본 VPC 공개 서브넷의 Ubuntu 24.04 t3.small 한 대, Elastic IP, IMDSv2 필수, 암호화된 gp3 20GiB, instance profile은 `AmazonSSMManagedInstanceCore`만 붙인다. user_data가 Docker Engine과 Compose, Buildx 플러그인, swap 2GiB, `deploy` 계정을 만든다.
- SG는 80, 443을 전체에 열고, 22는 변수로 받은 러너 공인 IP만 연다.
- 사진 버킷 `moimroom-legacy-photos-<account>`(비공개, SSE-S3, 버전 관리 없음)와 `/fixtures/` 경로의 IAM 사용자, 그 버킷의 Get/Put/Delete/List만 허용하는 정책을 만든다.
- DNS 자원은 만들지 않는다. 출력으로 만들 레코드를 알려 준다.
- 자원 태그는 평범한 회사 앱처럼 `Project=moimroom`, `Environment=production`, `Component=web`, `ManagedBy=Terraform`만 단다. 온보딩 조사가 인스턴스 태그를 화면에 보여 주므로 팀이나 플랫폼 이름을 넣지 않는다. 정리는 이 root의 state와 `Name=moimroom-legacy`로 찾는다. EKS나 ASG 태그, 플랫폼 IAM이 거부하는 `Component` 값(`access`, `app-private-network`, `eks`)은 달지 않는다.
- CI의 `terraform-verify` 대상에는 넣지 않는다. 시험이 끝나면 destroy할 임시 root다.

## 영향과 후속 작업

- 기대 효과: ADR 0042의 조사, 연결, 전환을 실제 EC2, SSM, S3, DNS로 시험할 수 있다.
- 단점과 위험: IAM 접근 키의 비밀값이 이 root의 state에 남는다(암호화된 팀 state 버킷). GCP 러너의 공인 IP는 임시 IP라 VM을 다시 시작하면 바뀌고, 그때는 `ssh_allowed_cidrs`를 고쳐 다시 apply해야 한다. 비용은 하루 약 0.8 USD다.
- 필요한 후속 작업 및 검증: apply, Cloudflare 레코드, 첫 배포는 승인 후 사람이 실행한다. 시험이 끝나면 destroy하고 레코드를 지운다.

## 관련 자료

- [terraform/fixtures/aws/legacy-moimroom/README.md](../../terraform/fixtures/aws/legacy-moimroom/README.md)
