# AWS↔GCP DB 연결과 임시 이전 권한

관련: [ADR0048](../adr/0048-connect-cloud-databases-with-scoped-migration-access.md), [infra #47](https://github.com/2026-Softbank-Hackathon-Charlie/infra/issues/47).
이 문서는 **설치 전 코드 준비**다. 아래 실행은 클러스터·tailnet·Secret 변경 승인을 받은 운영자만 수행한다. 기본 예제는 모두 꺼져 있으며 웹앱 라우터와 기존 DB를 수정하지 않는다.

## 1. 실행 전 확인

1. 관리 EKS·workload EKS·workload GKE의 **운영자** kubeconfig/context를 준비한다. 기존 API kubeconfig는 `charlie-system/hopcharlie-platform`이며 operator 설치·권한 부여에는 사용하지 않는다. `kubectl config use-context`로 사용자 기본 컨텍스트를 바꾸지 않는다.
2. source namespace/Cluster와 **새 target standby**를 확정한다. 예제의 `moimroom/moimroom-db`(EKS)와 `charlie-apps/moimroom-db-gke`(GKE, 보호 네임스페이스라 [ADR0060](../adr/0060-allow-live-moimroom-db-in-protected-namespace-for-migration-access.md)의 예외 쌍)는 catalog 기준이다. 기존 `charlie-apps/moimroom-gcp-db`·PoC primary를 target으로 덮어쓰지 않는다. 같은 CNPG Cluster의 다른 DB도 물리 복제 영향을 받는다.
3. DB/API namespace의 기존 default-deny·DNS·Kubernetes API·CNPG·백업 허용 정책을 확인한다. 새 차트의 Egress allow만 먼저 설치하면 기존 정책이 없는 클라이언트를 격리할 수 있다. 노드 여유도 확인한다(standalone 합계 9 Pods, proxy 요청 각각 50m/128Mi, operator 각각 50m/128Mi; 측정값 아님). 고정 버전의 proxy/init 기본값은 **privileged**다. 전용 namespace의 Pod Security 허용을 별도 검토·승인하고, 정책이 막으면 앱 namespace의 보안을 낮추거나 우회하지 않는다.
4. source·target의 양방향 `externalClusters`·replica topology, replication CA/client cert/key와 source 앱 비밀번호를 운영자가 준비한다. 엔진은 Secret을 복사하지 않는다. target은 `pg_is_in_recovery()=true`여야 하고 Plan의 `target.manifest`는 **생략**한다.
5. 앱의 Argo self-heal이 `/spec/replica`를 되돌리지 않도록 합의된 동기화 제어·GitOps 반영/재개 절차를 준비한다. HTTP gateway 외부 writer(배치·직접 SQL 등)는 운영자가 중지한다. 현재 엔진 fence는 gateway 쓰기 hold이며 SQL 쓰기 주체 전체를 차단하지 않는다. 이전 도중 앱 업그레이드는 하지 않는다.
6. 원본 Cluster에 백업(Barman Cloud plugin, platform ADR 0025와 [ADR0057](../adr/0057-back-up-gke-moimroom-db-with-workload-identity.md))이 켜져 있으면 WAL 보관이 되는지 확인한다. CNPG는 원본을 강등할 때 마지막 WAL을 보관해야 demotion token을 내므로, 보관이 막혀 있으면 엔진은 원본을 펜싱하고 쓰기를 붙잡은 채 `halted`로 멈춘다(migration-engine ADR 0008, 자동 원복 없음). 첫째 항목과, 둘째나 셋째 항목 중 하나를 확인하고 맞지 않으면 이전을 시작하지 않는다. 쓰기가 없는 원본은 몇십 분 동안 WAL을 올리지 않아도 정상이므로 마지막 업로드 시각만으로 판단하지 않는다.
   - `kubectl get cluster <원본> -n <ns> -o jsonpath='{.status.conditions[?(@.type=="ContinuousArchiving")].status}'`가 `True`이고, `spec.plugins`에 `isWALArchiver: true`인 Barman Cloud plugin이 있다. plugin이 없어도 조건은 True다.
   - 원본 primary에서 세그먼트를 강제로 바꾸고 1분 안에 그 WAL이 올라가는지 본다. `kubectl cnpg psql <원본> -n <ns> -- -c 'CHECKPOINT' -c 'SELECT pg_switch_wal()'`을 실행하고, ObjectStore의 `destinationPath` 아래 `<serverName>/wals/`에 새 파일이 생기는지 확인한다(`gcloud storage ls -l` 또는 `aws s3 ls --recursive`). cnpg 플러그인이 없으면 `kubectl exec -n <ns> <원본>-1 -c postgres -- psql -c 'CHECKPOINT' -c 'SELECT pg_switch_wal()'`로 같다.
   - 위를 할 수 없으면(예: 원본이 replica라 `pg_switch_wal`이 안 됨) 보관 대기열과 통계로 확인한다. `kubectl exec -n <ns> <원본>-1 -c postgres -- sh -c 'ls /var/lib/postgresql/data/pgdata/pg_wal/archive_status | grep -c "\.ready$" || true'`가 `0`이고, `psql -Atc 'SELECT last_archived_time, last_failed_time FROM pg_stat_archiver'`에서 `last_failed_time`이 비었거나 `last_archived_time`보다 이르다.

SQL 계정은 DB CONNECT, schema USAGE, 검증할 전체 행/컬럼 SELECT와 조회 함수 EXECUTE가 필요하다. RLS 등으로 일부 행만 보는 계정이면 검증 결과를 신뢰할 수 없다. superuser·replication 계정을 엔진에 줄 필요는 없지만, 현재 앱 계정의 적합성은 실제 grants와 함께 확인한다. 원본 파일 저장소 이전과 앱별 gateway scope 수정은 별도 과제다.

### 기존 GKE moimroom 앱 설정 재현

`charlie-apps/moimroom-gcp`는 수동 적용한 Deployment이며 Helm release나 Argo CD 관리 대상이 아니다. 이미지 게시 workflow는 배포하지 않는다. [GKE profile](../../clusters/gcp-dev-workload/values/moimroom-gcp.yaml)은 이전 후 설정(`replicaCount: 1`, `seedSpaces: false`, `DATABASE_URL`은 기존 `moimroom-gke-dburl/uri` Secret 참조)과 현재 이미지·공개 S3 설정·기존 Secret 참조·resources를 함께 보존한다. Secret 값은 넣지 않는다. DB 대상 결정은 [ADR0053의 후속 작업](https://github.com/2026-Softbank-Hackathon-Charlie/infra/pull/72)을 따른다.

운영자가 준비한 moimroom 저장소의 `ca3c8f8e852ab6b57bd6390bcc7b5c69bee0ddb8` checkout을 사용한다. 다음 명령은 클러스터에 접근하지 않고 Deployment 하나만 파일로 출력한다. chart나 이미지를 바꿀 때는 profile과 전체 Deployment diff를 다시 검토한다.

```bash
mkdir -p .generated
make render-gke-moimroom MOIMROOM_CHART=/path/to/moimroom/deploy/helm/moimroom > .generated/gke-moimroom.yaml
```

전체 chart의 Service template에는 GKE Internal LoadBalancer annotation 지원이 없다. 전체 chart를 적용하거나 `helm upgrade --install`로 인수하지 않는다. 기존 `moimroom-gcp`·`moimroom-gcp-internal` Service, CNPG Cluster, PVC, Secret은 이 경로에 포함하지 않는다. 렌더링은 향후 수동 배포의 입력이며 merge만으로 운영 리소스가 바뀌지 않는다.

실제 조회·변경 승인을 별도로 받은 운영자만 명시적인 kubeconfig/context로 Deployment diff를 검토하고 적용한다. template은 `metadata.namespace`를 생략하므로 **apply에도 `--namespace charlie-apps`가 필수**다. Secret 갱신은 별도 작업이며, DB 연결이 바뀌었다면 이 profile의 참조와 승인된 Secret 대상이 같은지 먼저 확인한다.

```bash
kubectl --kubeconfig "$OperatorKubeconfig" --context "$Context" --namespace charlie-apps diff -f .generated/gke-moimroom.yaml
# 위 diff의 Deployment 변경 승인을 받은 뒤에만 실행한다.
kubectl --kubeconfig "$OperatorKubeconfig" --context "$Context" --namespace charlie-apps apply -f .generated/gke-moimroom.yaml
kubectl --kubeconfig "$OperatorKubeconfig" --context "$Context" --namespace charlie-apps rollout status deployment/moimroom-gcp --timeout=5m
```

## 2. Tailnet 정책과 operator 인증

[policy-fragment](../../helm/db-network/policy-fragment.json.example)은 **추가할 조각**이다. 기존 정책 전체를 대체하면 앱 경로·route autoApprover를 잃을 수 있다. 기존 설정에 병합하고 TCP80 앱 규칙·autoApprovers를 유지한다. 기존 allow-all ACL/grant가 DB를 허용하면 제거·축소하고 포함된 accept/deny tests를 tailnet 정책 편집기에서 통과시킨다. grant는 합산되므로 새 규칙 추가만으로 deny가 생기지 않는다.

클러스터마다 OAuth client를 별도로 만들고 아래 operator tag만 지정한다. 공식 operator 설치 문서의 **Devices/Core, Keys/Auth Keys, General/Services write** 권한과 tag ownership을 확인한다. proxy tag는 자기 클러스터 operator만 소유한다. 장치/태그 추가·정책 변경은 별도 승인 대상이다.

| clusterId | operator tag | default proxy tag |
|---|---|---|
| management | `tag:hop-db-operator-management` | `tag:hop-db-client-management` |
| eks | `tag:hop-db-operator-eks` | `tag:hop-db-client-eks` |
| gke | `tag:hop-db-operator-gke` | `tag:hop-db-client-gke` |

각 클러스터의 `hopcharlie-db-network` namespace에 `operator-oauth` Secret(`client_id`, `client_secret`)을 사전 생성한다. 값은 Terraform·Helm values·명령행 인자·평문 파일·채팅에 넣지 않는다. 계정 소유자 터미널의 다음 PowerShell 예시는 입력을 메모리에 보관하고 JSON을 kubectl stdin으로만 전달한다. `$OperatorKubeconfig`, `$Context`는 승인한 운영자 경로/컨텍스트로 먼저 지정한다.

```powershell
kubectl --kubeconfig $OperatorKubeconfig --context $Context create namespace hopcharlie-db-network
try {
  $clientId = Read-Host 'Tailscale client ID'
  $clientSecret = Read-Host 'Tailscale client secret' -AsSecureString
  $manifest = @{apiVersion='v1'; kind='Secret'; type='Opaque'; metadata=@{name='operator-oauth'; namespace='hopcharlie-db-network'};
    stringData=@{client_id=$clientId; client_secret=[System.Net.NetworkCredential]::new('', $clientSecret).Password}}
  $manifest | ConvertTo-Json -Depth 6 | kubectl --kubeconfig $OperatorKubeconfig --context $Context create -f -
} finally { Remove-Variable clientId,clientSecret,manifest -ErrorAction SilentlyContinue }
```

기존 namespace는 재사용하고 기존 Secret은 자동 교체하지 않는다. `AlreadyExists`와 다른 오류를 구분한다. operator OAuth 비밀값은 Helm release/state에 들어가지 않으며, 컨트롤러의 장치 상태용 Kubernetes Secret은 별도로 생성된다. 현재 operator는 WIF도 지원하지만 GKE issuer discovery/JWKS 외부 접근이 미확인이므로 이 PR의 기본값에 넣지 않는다.

## 3. operator와 DB 경로 설치

기존 라우터 VM·VPC 경로·Argo GKE 앱용 자격 증명은 변경하지 않는다. 다음 operator 명령을 위 표의 clusterId와 해당 `$Context`로 세 번 실행한다. 리소스 설치에는 operator의 cluster RBAC·CRD도 포함되므로 적용 범위를 먼저 검토한다.

```powershell
# $ClusterId = 'management', 'eks' or 'gke'; no credentials in these flags.
helm upgrade --install operator tailscale-operator --repo https://pkgs.tailscale.com/helmcharts --version 1.104.4 `
  --namespace hopcharlie-db-network --kubeconfig $OperatorKubeconfig --kube-context $Context `
  -f helm/db-network/operator-values.yaml `
  --set "operatorConfig.hostname=hop-db-operator-$ClusterId" `
  --set "operatorConfig.defaultTags[0]=tag:hop-db-operator-$ClusterId" `
  --set "proxyConfig.defaultTags=tag:hop-db-client-$ClusterId" --wait=false --timeout 5m
```

strict CNI에서 새 namespace에 정책이 없으면 operator가 연결하지 못할 수 있다. 먼저 `--wait=false`로 CRD와 operator를 등록하고, 바로 아래 DB chart로 NetworkPolicy를 설치한 뒤 Ready를 기다린다. operator를 먼저 `--wait`하거나 이 중간 상태에서 작업을 끝내지 않는다.

[management](../../helm/db-network/management.yaml.example), [eks](../../helm/db-network/eks.yaml.example), [gke](../../helm/db-network/gke.yaml.example)에서 namespace/Cluster·고유 장치 이름·실제 tailnet DNS suffix를 확인한다. `example.ts.net`은 실제 주소가 아니다. 각 파일은 `enabled: false`이므로 다음 활성화는 별도 승인 후 수행한다.

```powershell
helm template db-network helm/charts/hopcharlie-db-network --namespace hopcharlie-db-network `
  -f "helm/db-network/$ClusterId.yaml.example" --set enabled=true
helm upgrade --install db-network helm/charts/hopcharlie-db-network --namespace hopcharlie-db-network `
  --kubeconfig $OperatorKubeconfig --kube-context $Context `
  -f "helm/db-network/$ClusterId.yaml.example" --set enabled=true --wait --timeout 5m
kubectl --kubeconfig $OperatorKubeconfig --context $Context --namespace hopcharlie-db-network `
  rollout status deployment/operator --timeout=5m
```

CNPG가 만든 `-rw`가 아닌 별도 primary selector Service를 사용한다. 일반 클라이언트는 local `db-eks`/`db-gke` Service에 연결하고, proxy만 remote tailnet FQDN을 사용한다. operator는 ExternalName Service의 `externalName`을 headless Service 이름으로 변경한다. 별도 Argo Application이 이 필드를 `placeholder`로 self-heal하지 않게 한다. 장치 이름 중복으로 tailnet 이름에 suffix가 붙으면 예제 FQDN이 맞지 않으므로 실제 이름을 확인한다.

Tailscale proxy의 TCP443·DNS·가변 목적지 UDP는 control/DERP/direct transport용이다. DB 데이터 grant는 TCP5432만이다. 설치된 proxy의 부모 Service labels, CNPG primary label, CNI의 SNAT 뒤 출발지와 실제 허용/거부를 확인한다. direct 연결이 안 되고 DERP만 된다면 base backup·WAL 처리량과 HTTP 영향부터 측정한다.

## 4. 엔진 연결·TLS·임시 권한

관리 EKS API의 catalog는 [apps.yaml 예제](../../helm/db-network/apps.yaml.example), DSN 허용 suffix는 [platform values 예제](../../helm/db-network/platform-values.yaml.example)를 참고한다. 실제 catalog 전체를 예제로 덮어쓰지 않는다. 기존 앱·환경과 SSM 갱신·API 재시작은 별도 승인하고 필요한 항목만 반영한다. DB 경로에 tailnet 이름 대신 local Service DNS를 썼으므로 `.hopcharlie-db-network.svc.cluster.local`이 필요하다.

복제 TLS는 `verify-full`을 유지한다. local egress DNS를 접속 host로 쓰면 원격 CNPG 서버 인증서 SAN에 해당 이름이 있어야 한다. 예를 들어 EKS는 `db-eks.hopcharlie-db-network.svc.cluster.local`, GKE는 `db-gke.hopcharlie-db-network.svc.cluster.local`을 승인된 `spec.certificates.serverAltDNSNames`에 포함한다. 필요한 CA·streaming_replica 인증서는 운영자가 준비하고 실제 handshake를 검사한다. proxy는 TLS를 종료하지 않는다. 현재 platform draft의 `sslmode=require`는 복제의 hostname 검증과 다르며 검증 우회를 위해 설정을 낮추지 않는다.

권한 helper는 기본 render-only이며 파일·kubeconfig를 읽거나 외부 호출을 하지 않는다.

`charlie-apps` 같은 인프라 보호 네임스페이스는 helper가 거부한다. 예외는 코드의 `PROTECTED_EXCEPTIONS`에 정확히 일치하는 `(네임스페이스, CNPG Cluster)` 한 쌍뿐이며 지금은 GKE 운영 DB `charlie-apps/moimroom-db-gke`만 있다([ADR0060](../adr/0060-allow-live-moimroom-db-in-protected-namespace-for-migration-access.md)). 같은 네임스페이스의 다른 Cluster(예: 낡은 `moimroom-gcp-db`)는 계속 거부한다.

```powershell
python scripts/db-migration-access.py render --kubeconfig $OperatorKubeconfig --context $Context `
  --namespace $DbNamespace --cluster $CnpgCluster --session $Session
# Run for BOTH workload contexts only after explicit grant approval.
python scripts/db-migration-access.py grant --kubeconfig $OperatorKubeconfig --context $Context `
  --namespace $DbNamespace --cluster $CnpgCluster --session $Session
kubectl --kubeconfig $OperatorKubeconfig --context $Context --namespace $DbNamespace `
  auth can-i patch "clusters.postgresql.cnpg.io/$CnpgCluster" --as system:serviceaccount:charlie-system:hopcharlie-platform
```

기존 SA 토큰·SSM·kubeconfig를 재발급하지 않는다. Role은 지정 Cluster의 `get/patch`만 허용한다. **patch는 해당 CNPG 객체의 전체 필드에 대한 권한이며 promotion 필드만으로 제한되지는 않는다.** Secret·다른 Cluster·cluster RBAC 접근은 허용하지 않는다. Kubernetes RBAC에는 TTL이 없어 helper 종료만으로 회수되지 않는다. source와 target 양쪽에서 같은 세션의 revoke를 반드시 실행한다.

```powershell
python scripts/db-migration-access.py revoke --kubeconfig $OperatorKubeconfig --context $Context `
  --namespace $DbNamespace --cluster $CnpgCluster --session $Session
```

helper는 다른 내용의 기존 Role/RoleBinding을 인수하지 않는다. 새 grant 중 부분 실패는 이번에 만든 Role만 조건부 정리하며, revoke는 Binding부터 소유권·UID·resourceVersion을 확인해 삭제한다. API 응답 유실 뒤 남은 객체는 같은 session으로 다시 확인·회수한다. `AlreadyExists`·충돌·권한 오류를 성공으로 무시하지 않는다.

## 5. 실제 시험·중단·정리 기준

1. API→양쪽 SQL5432·workload DB→상대 DB5432 양방향·API→양쪽 Kubernetes443, TLS와 제한된 권한을 확인한다. Secret 읽기·다른 Cluster patch는 거부되어야 한다.
2. 시험용 데이터의 초기 복사를 시연 전에 끝내고 standby lag를 확인한다. 성공 응답 데이터의 누락·중복과 실제 SQL grants/RLS를 검사한다. target이 primary거나 app namespace/Cluster/DSN이 다르면 진행하지 않는다.
3. 실제 Plan의 app-scoped gateway URL, `target.manifest` 생략과 Argo 동기화 제어를 확인한 뒤 복제·검증·전환을 시험한다. 앱/DB 준비 실패·연결 단절의 정지 상태도 확인한다. 승격 뒤 target에 새 쓰기가 생겼다면 역복제·데이터 보존 확인 없이 경로만 원본으로 돌리지 않는다.
4. 성공·실패·취소 모두 권한 revoke와 `auth can-i` 재확인, GitOps 최종 topology 반영·동기화 복구를 수행한다. namespace·DB·PVC 삭제는 이 helper의 범위가 아니다.
5. 새 연결 자체를 철거하려면 DB가 더 이상 해당 복제 경로에 의존하지 않는지 확인한 뒤 network release를 먼저 제거한다. operator가 proxy·tailnet 장치를 정리한 것을 확인한 다음 operator release·이번에 생성한 Secret/OAuth client를 회수한다. 기존 앱용 router와 다른 장치는 삭제하지 않는다.

파일 이전을 포함하면 catalog 반영 후 **새 Plan을 draft**하고 `files.source`/`files.target`가 방향에 맞게 EKS `s3:hopcharlie-eks-moimroom`, GKE `gcs:charlie-414068132732-moimroom-gcp`인지 확인한다. 기존 Plan의 파일 경로는 catalog 수정으로 자동 갱신되지 않는다. 실행 서버의 `s3`/`gcs` rclone remote 설정은 미확인이다. 별도 승인한 운영자가 실제 실행 서버에서 backend·계정·bucket 일치, 양쪽 목록·원본 읽기·대상 쓰기/삭제 권한과 대상 기존 파일의 삭제 영향을 검토하기 전에는 이전을 시작하지 않는다.

이 PR의 로컬 렌더·mock tests는 경로·역할 설정의 계약만 검사한다. 실제 WAL 처리량, 복제 성공, 무손실 전환과 예산은 설치 후 시험 결과로 기록한다.
