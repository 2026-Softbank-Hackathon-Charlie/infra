# 독립 K3s workload 준비

- 날짜: 2026-11-07
- 상태: 운영 식별정보가 없는 재사용 참고 문서

이 문서는 공개용 매개변수 템플릿이다. 실제 프로젝트·계정·주소·tailnet·접속 사용자·실행 로그는 포함하지 않는다.

단일 VM의 K3s를 기존 관리 클러스터와 분리한다. VM 용량·Linux 아키텍처·디스크·CIDR 충돌을 확인하고, 사설 API 경로와 서비스 인증을 각각 검증한다. 기존 클라우드 workload target을 교체하지 않는다. Secret·kubeconfig·state는 Git에 저장하지 않는다.

입력: `<CLOUD_PROJECT>`, `<ZONE>`, `<VM_NAME>`, `<TAILNET_FQDN>`, `<APP_NAMESPACE>`, `<KUBE_CONTEXT>`. 설치·등록·앱/DB 전환을 구분해 승인받는다.

## 관련 자료

- [설치 계획](onprem-k3s-installation.md)
- [연결 계획](onprem-k3s-management-connection.md)
- [독립 대상 결정](../adr/0021-add-independent-onprem-k3s-workload.md)
