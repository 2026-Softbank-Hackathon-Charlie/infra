# 0067. GCP runner를 최초 사양의 2배로 줄이고 동시 실행을 6개로 맞춘다

- 날짜: 2026-11-07
- 상태: 채택
- 관련 ADR: [ADR 0045](0045-run-actions-on-self-hosted-runner.md)의 2026-11-06 증설 사양을 변경

## 배경과 제약

사용자는 처음의 3배로 늘린 GCP self-hosted runner를 2배로 줄이도록 요청했다. 최초 사양은 e2-standard-4(4 vCPU/16 GB), runner 3개, 디스크 80 GB였다. 기존 증설 설정은 e2-custom-12-49152(12 vCPU/48 GB), runner 9개, 디스크 200 GB다.

대상은 프로젝트 `project-b47cdba3-db5b-42a2-9a6`, 영역 `asia-northeast3-a`의 `hopcharlie-github-runner` 한 대다. 운영상 중지·재시작과 runner 등록 정리는 검토한 범위에 대한 명시적 승인 후 수행한다. 이 문서와 로컬 수정만으로 실제 축소 완료를 뜻하지 않는다.

## 검토한 대안

- 12 vCPU/48 GB·9개 유지: 동시 처리량은 유지하지만 사용자의 축소 요청을 충족하지 않는다.
- 8 vCPU/32 GB·9개 유지: VM 비용은 줄지만 job당 여유가 줄어 최초·증설 시의 자원 비율을 유지하지 못한다.
- 8 vCPU/32 GB·6개, 디스크 200 GB 유지(선택): 최초 CPU·메모리·동시 실행의 2배다. 디스크 복사·교체 없이 작업 폴더와 캐시를 보존한다.

## 결정과 이유

`gcp_up.py`의 목표를 `e2-standard-8`, runner `hopcharlie-gcp-runner-1`부터 `6`까지로 변경한다. `DISK_GB=200`을 유지한다. 기존 `--resize`는 머신 유형만 바꾸며 디스크 축소를 수행하지 않는다. 방화벽, 서비스 계정 없음, OS Login, label과 공통 bootstrap은 바꾸지 않는다.

`bootstrap.sh`는 지정한 수만 추가·시작하고 초과 runner를 제거하지 않는다. 따라서 수를 6으로 바꾸는 것만으로 기존 7~9가 사라진다고 표현하지 않는다. 적용 시에는 신규 job 수신을 멈추고 기존 job 완료를 확인한 뒤, 7~9의 서비스를 중지·비활성화하고 해당 조직 등록만 별도로 정리한다. 작업 디렉터리·캐시를 삭제하지 않는다. VM을 중지·변경·시작한 후 1~6만 active/online인지 확인한다.

## 영향과 후속 작업

- 기대 효과: CPU·메모리가 기존의 2/3로 줄고 최대 동시 job도 9개에서 6개로 줄어든다. 디스크·작업 폴더·이미지 캐시는 보존한다.
- 단점과 위험: 대기열이 길어질 수 있다. VM 중지 동안 CI가 대기하며 임시 외부 IP가 바뀔 수 있다. busy 조회와 중지 사이에도 새 job이 배정될 수 있으므로, 서비스 수신 중단과 실행 프로세스 확인을 함께 수행한다.
- 검증: 목표 VM 사양·기존 디스크 동일성·CPU/메모리·Docker, runner 1~6의 서비스 및 조직 online 상태를 확인한다. 승인된 작은 CI 또는 다음 실제 CI의 성공으로 job 실행을 검증한다.
- 미확인: 축소 후 peak CI 성능과 실제 청구 절감액. 공개 단가 추정과 최종 청구를 구분한다.
- 복구: 사양 변경·부팅·기본 health 확인이 실패하면 신규 job 수신과 실행 중인 job을 먼저 확인하고, 같은 디스크를 유지한 채 이전 `e2-custom-12-49152`로 되돌린 뒤 runner 1~6을 복구한다. 7~9의 조직 등록을 이미 지웠다면 서비스를 무작정 켜지 않으며, 재등록에 필요한 자격 증명 발급·등록은 해당 승인 범위에서 별도로 수행한다. 복구도 실패하면 확인된 상태와 남은 조치를 기록하고 다른 VM·IAM·디스크를 자동 변경하지 않는다.

## 관련 자료

- [gcp_up.py](../../scripts/github-runner/gcp_up.py)
- [공통 bootstrap.sh](../../scripts/github-runner/bootstrap.sh)
- [Google Cloud VM 머신 유형 변경](https://docs.cloud.google.com/compute/docs/instances/changing-machine-type-of-stopped-instance)

## 운영 적용 확인

2026-11-07 사용자 승인 후 동일 VM을 e2-standard-8로 변경했다. 8 vCPU/32 GiB·동일200GiB 디스크·Docker/DNS·runner1~6 online과 기존 등록ID 보존을 확인했다. 7~9는 재부팅 전에 서비스 비활성화하고, 1~6 복구 확인 후 조직 등록3개를 정리했다. 작업 폴더·캐시는 삭제하지 않았다. peak 성능과 최종 청구 절감액은 미확인이다.
