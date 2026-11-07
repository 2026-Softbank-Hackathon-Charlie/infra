# 단일 VM K3s 설치 참고

- 날짜: 2026-11-07
- 상태: 운영 식별정보가 없는 재사용 참고 문서

공개용 템플릿이며 실제 운영 기록은 포함하지 않는다. `<BOOT_DISK_GB>`와 quota를 확인한 뒤 같은 디스크/파일시스템을 확대한다. K3s v1.35.9+k3s1과 Tailscale 1.104.5를 공식 자산/checksum·서명 저장소로 검증한다. SQLite·K3s 기본 addon 소유권과 root 0600 kubeconfig·secret encryption을 유지한다.

새 VM은 사용자가 기존 tailnet에 브라우저로 승인한다. route/DNS 수용·Tailscale SSH는 비활성인 초기 profile이다. 자기 tailnet 주소를 TLS SAN에 넣고 Ready·DNS·Service HTTP·metrics를 검증한다. 단일 VM·local storage는 HA/DR이 아니며 외부 백업이 필요하다. 기본 metrics APIService의 serving TLS 검증 예외는 별도 검토한다. 공개 API 포트를 추가 개방하지 않는다.

## 관련 자료

- [준비](onprem-k3s-workload.md)
- [설치 결정](../adr/0024-bootstrap-single-node-k3s-on-gce.md)
