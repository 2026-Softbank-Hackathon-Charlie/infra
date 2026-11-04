# 0043. GKE sealed-secrets를 고정 값 파일과 수동 server-side apply로 관리한다

- 날짜: 2026-11-04
- 상태: 채택
- 관련 ADR: [ADR 0031](0031-reproduce-cnpg-poc-on-gke.md), [ADR 0039](0039-add-gke-app-environment.md), [ADR 0040](0040-use-gcs-for-gke-app-buckets.md), 이슈 [#33](https://github.com/2026-Softbank-Hackathon-Charlie/infra/issues/33)

## 배경과 제약

GKE의 sealed-secrets 컨트롤러 `charlie-apps/sealed-secrets-gcp`는 chart 2.20.0을 렌더링해 직접 적용했다. Helm 릴리스와 Argo Application이 없고 저장소에 값도 없었다. 11/4에 범위를 모든 namespace로 넓히고 키 자동 갱신을 껐다([#33 댓글](https://github.com/2026-Softbank-Hackathon-Charlie/infra/issues/33#issuecomment-6627058432)). 다시 설치하면 이 변경이 사라질 수 있었다.

- 확인한 사실:
  - GKE의 다른 구성요소(cert-manager, CNPG PoC)도 `clusters/gcp-dev-workload/values`의 값으로 직접 설치한다([ADR 0031](0031-reproduce-cnpg-poc-on-gke.md)).
  - GitHub runner는 GKE API 허용망 밖이라 CI가 GKE에 적용할 수 없다.
  - 11/4 `kubectl diff --server-side`로 새 값 파일의 렌더 결과와 실제 클러스터를 비교했고 차이가 없었다(`generation` 제외).

## 검토한 대안

- Argo Application으로 관리: Argo가 GKE에 등록돼 있지만 addon ApplicationSet(`helm/gitops`)은 AWS 클러스터 전용이다. 고치는 범위가 크고, 컨트롤러가 잘못 바뀌면 봉인 키를 잃을 위험이 있다.
- Terraform helm_release: GKE API에 닿는 실행 경로가 없고, 기존 자원 인수가 필요하다.
- 값 파일 + 수동 server-side apply(선택): 다른 GKE 구성요소와 같은 방식이고 지금 상태를 그대로 코드로 남긴다.

## 결정과 이유

- 값 [clusters/gcp-dev-workload/values/sealed-secrets.yaml](../../clusters/gcp-dev-workload/values/sealed-secrets.yaml):
  - 이름 `sealed-secrets-gcp`, namespace `charlie-apps`, 키 접두사 `sealed-secrets-gcp-key`
  - 컨테이너 인자를 실제 값으로 고정: `--key-renew-period=0`, `--all-namespaces` 없음(모든 namespace 처리)
  - ClusterRole `secrets-unsealer`와 ClusterRoleBinding `sealed-secrets-gcp`, service-proxier 권한은 만들지 않음(원래 설치와 같음)
  - 이미지는 `helm/images.lock.json`의 digest
- `scripts/check-helm.py`가 렌더해서 위 조건을 확인한다.
- 적용(GKE 접속 가능한 PC에서, 승인 후):

```bash
helm pull sealed-secrets --repo https://bitnami.github.io/sealed-secrets --version 2.20.0 --untar
helm template sealed-secrets-gcp ./sealed-secrets -n charlie-apps \
  -f clusters/gcp-dev-workload/values/sealed-secrets.yaml > sealed-secrets-gcp.yaml
kubectl diff  --server-side --field-manager=charlie-gcp-sealed-secrets -f sealed-secrets-gcp.yaml
kubectl apply --server-side --field-manager=charlie-gcp-sealed-secrets -f sealed-secrets-gcp.yaml
```

  CRD는 이미 설치돼 있어 위 렌더에 넣지 않는다(chart를 올릴 때만 `--include-crds`로 따로 확인).

### 봉인 키 백업과 복구

- 키는 `charlie-apps`의 Secret(라벨 `sealedsecrets.bitnami.com/sealed-secrets-key`)이다. 키 갱신을 껐으므로 키는 하나로 유지된다.
- 백업: `kubectl -n charlie-apps get secret -l sealedsecrets.bitnami.com/sealed-secrets-key -o yaml`을 팀 비밀 저장소(예: AWS Secrets Manager)에만 둔다. **Git, Terraform state, 로그, Notion에 넣지 않는다.**
- 복구: 컨트롤러를 다시 설치하기 전에 같은 Secret을 먼저 적용하고 컨트롤러를 재시작한다. 공개 인증서(SSM `/hopcharlie/sealed-secrets/gke`)와 지문이 같은지 확인한다.

## 영향과 후속 작업

- 기대 효과: 재설치해도 11/4 설정이 유지되고, CI가 값의 핵심 조건을 검사한다.
- 단점과 위험: 실제 적용은 여전히 사람이 한다. 값과 클러스터가 달라져도 자동으로 알 수 없으므로, 바꿀 때마다 `kubectl diff`를 먼저 한다.
- 후속(별도 승인): 키 백업을 실제로 실행할지와 보관 위치 결정.

## 관련 자료

- [scripts/check-helm.py](../../scripts/check-helm.py)
- [Sealed Secrets: 키 백업과 복구](https://github.com/bitnami-labs/sealed-secrets#how-can-i-do-a-backup-of-my-sealedsecrets)
