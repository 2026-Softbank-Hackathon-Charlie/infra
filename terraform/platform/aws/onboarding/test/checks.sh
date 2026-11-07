#!/usr/bin/env bash
# The checks of run.sh, run inside a container with the docker socket and
# the scripts at /scripts. Not for a real VM.
set -euo pipefail
cd "$(mktemp -d)"
s=/scripts
project=${HC_TEST_PROJECT:?}
fails=0

ok() { echo "ok   $1"; }
fail() {
	echo "FAIL $1" >&2
	fails=$((fails + 1))
}
expect() { # description command...
	local d=$1
	shift
	if "$@" >/dev/null 2>&1; then ok "$d"; else fail "$d"; fi
}

openssl genpkey -algorithm RSA -pkeyopt rsa_keygen_bits:2048 -out priv.pem 2>/dev/null
pub=$(openssl pkey -in priv.pem -pubout -outform DER | base64 | tr -d '\n')

# decrypt prints the plaintext of an envelope (the Go side's construction).
decrypt() {
	local env=$1 k b
	k=${env#hcenc1.}
	b=${k#*.}
	k=${k%%.*}
	printf '%s' "$k" | base64 -d >wk
	printf '%s' "$b" | base64 -d >body
	openssl pkeyutl -decrypt -inkey priv.pem -pkeyopt rsa_padding_mode:oaep -pkeyopt rsa_oaep_md:sha256 \
		-pkeyopt rsa_mgf1_md:sha256 -in wk -out w
	head -c 64 w >pass
	openssl enc -d -aes-256-cbc -pbkdf2 -iter 10000 -md sha256 -pass file:pass -in body -out plain
	if [ "$(tail -c 32 w | od -An -tx1 | tr -d ' \n')" != "$(sha256sum <plain | cut -c1-64)" ]; then
		echo "digest mismatch" >&2
		return 1
	fi
	cat plain
}

# payload reads the steps' outputs (files out.0 ...) and prints the document.
payload() {
	local n i
	n=$(head -1 out.0 | sed -n 's/.* chunks=\([0-9]*\) .*/\1/p')
	: >all
	for i in $(seq 0 $((n - 1))); do
		sed -n 2p "out.$i" | tr -d '\n' >>all
	done
	[ "$(sha256sum <all | cut -c1-64)" = "$(head -1 out.0 | sed -n 's/.*sha256=//p')" ] || {
		echo "payload sha256 mismatch" >&2
		return 1
	}
	base64 -d <all | gunzip
}

# --- inspect, one chunk ---

HC_PROJECT=$project HC_PUBLIC_KEY=$pub HC_WORK_DIR=/tmp/hcwork HC_WAL_SAMPLE=1 bash $s/inspect.sh >out.0 2>inspect.err || {
	cat inspect.err >&2
	fail "inspect.sh exits 0"
}
expect "header line" grep -Eq '^HOPCHARLIE-INSPECT v1 chunk=0 chunks=1 sha256=[0-9a-f]{64}$' <(head -1 out.0)
payload >doc.json
expect "document is JSON" jq -e '.version == 1' doc.json
# shellcheck disable=SC2016 # jq variables
expect "three containers" jq -e --arg p "$project" '[.containers[] | select(.project == $p)] | length == 3' doc.json
expect "db facts" jq -e '.db[0].facts.serverVersionNum >= 170000 and .db[0].facts.user == "app" and .db[0].facts.database == "app"' doc.json
expect "musl version()" jq -e '.db[0].facts.version | test("musl")' doc.json
expect "reindex candidate" jq -e '[.db[0].facts.indexes[].name] | index("public.users_email_idx") != null' doc.json
expect "C index left out" jq -e '[.db[0].facts.indexes[].name] | index("public.users_email_c_idx") == null' doc.json
expect "collation-dependent check and partition key" jq -e '.db[0].facts.collationDependent == ["public.events.partition_key", "public.users.users_name_check"]' doc.json
expect "superusers" jq -e '.db[0].facts.superusers == ["app"] and .db[0].facts.postgresRole == false' doc.json
expect "env names" jq -e '[.containers[] | select(.service == "app") | .env[].name] | index("APP_SECRET") != null' doc.json
expect "image env marked" jq -e '[.containers[] | select(.service == "app") | .env[] | select(.name == "PATH") | .image] == [true]' doc.json
expect "multi-line value is one name" jq -e '[.containers[] | select(.service == "app") | .env[].name | select(startswith("line"))] == []' doc.json
expect "network subnet" jq -e '.networks[0].ipam[0].Subnet | test("/")' doc.json
expect "volumes listed" jq -e '[.volumes[].name] | length == 2' doc.json
expect "free disk" jq -e '.freeDiskBytes > 0' doc.json
for v in s3cr3t-value-for-test aws-secret-for-test pw-db-0123456789 looks-like-a-name; do
	if grep -q -- "$v" out.0 doc.json; then fail "no clear value $v in the output"; else ok "no clear value $v in the output"; fi
done
decrypt "$(jq -r .secrets doc.json)" >secrets.json
# shellcheck disable=SC2016 # jq variables
expect "secrets decrypt" jq -e --arg c "${project}-app-1" '.[$c] | index("APP_SECRET=s3cr3t-value-for-test") != null' secrets.json
# shellcheck disable=SC2016 # jq variables
expect "db container env in secrets" jq -e --arg c "${project}-db-1" '.[$c] | index("POSTGRES_PASSWORD=pw-db-0123456789") != null' secrets.json

# --- inspect, many chunks ---

key_pub=$pub
rm -f out.*
HC_PROJECT=$project HC_PUBLIC_KEY=$key_pub HC_WORK_DIR=/tmp/hcwork HC_WAL_SAMPLE=0 HC_MAX_CHUNK=1000 HC_MAX_CHUNKS=20 bash $s/inspect.sh >out.0 2>/dev/null || fail "inspect.sh with small chunks"
n=$(head -1 out.0 | sed -n 's/.* chunks=\([0-9]*\) .*/\1/p')
expect "split in ${n:-0} chunks" test "${n:-0}" -gt 1
for i in $(seq 1 9); do
	HC_MODE=chunk HC_CHUNK=$i HC_PUBLIC_KEY=$key_pub HC_WORK_DIR=/tmp/hcwork HC_MAX_CHUNK=1000 bash $s/inspect.sh >"out.$i"
done
expect "chunk past the end is a header" test "$(wc -l <out.9)" -eq 1
if payload >doc2.json; then ok "chunks reassemble"; else fail "chunks reassemble"; fi
expect "reassembled document" jq -e '.version == 1' doc2.json
HC_MODE=clean HC_PUBLIC_KEY=$key_pub HC_WORK_DIR=/tmp/hcwork bash $s/inspect.sh
rc=0
HC_MODE=chunk HC_CHUNK=1 HC_PUBLIC_KEY=$key_pub HC_WORK_DIR=/tmp/hcwork bash $s/inspect.sh >/dev/null 2>&1 || rc=$?
expect "cleaned chunk is exit 6" test "$rc" -eq 6
rc=0
HC_PUBLIC_KEY='not a key' bash $s/inspect.sh >/dev/null 2>&1 || rc=$?
expect "bad key is exit 2" test "$rc" -eq 2

expect "other database read" jq -e '[.db[0].others[] | select(.database == "other") | .indexes[].name] == ["public.notes_body_idx"]' doc.json
expect "config writable" jq -e '.db[0].configWritable == true' doc.json
expect "standby parameters" jq -e '.db[0].facts.maxWorkerProcesses > 0 and .db[0].facts.maxLocksPerTransaction > 0 and .db[0].facts.pendingConfig == []' doc.json

# --- connect ---

net=${project}_default
subnet=$(docker network inspect --format '{{range .IPAM.Config}}{{.Subnet}}{{end}}' "$net")
connect() {
	HC_DB_CONTAINER=${project}-db-1 HC_NETWORK=$net HC_SUBNET=$subnet HC_HOSTNAME=hop-db-legacy-hctest \
		HC_PUBLIC_KEY=$pub HC_APP=hctest HC_SKIP_TAILSCALE=1 HC_STATE_DIR=/tmp/hcstate bash $s/connect.sh
}
sql() { docker exec "${project}-db-1" psql -XAtq -U app -d app -c "$1"; }

# A server with a setting waiting for a restart: nothing is changed.
sql "alter system set shared_buffers = '200MB'" && sql 'select pg_reload_conf()' >/dev/null
rc=0
connect >/dev/null 2>&1 || rc=$?
expect "pending restart is exit 8" test "$rc" -eq 8
expect "pending restart changed nothing" test "$(sql "select count(*) from pg_roles where rolname = 'hopcharlie_replicator'")" -eq 0
sql 'alter system reset shared_buffers' && sql 'select pg_reload_conf()' >/dev/null

# A larger wal_keep_size stays.
sql "alter system set wal_keep_size = '2GB'" && sql 'select pg_reload_conf()' >/dev/null
connect >connect-wal.out 2>/dev/null || fail "connect.sh with wal_keep_size 2GB exits 0"
expect "larger wal_keep_size is info" grep -q '"info":"wal_keep_size is already 2048MB"' connect-wal.out
expect "larger wal_keep_size is not a change" bash -c "! grep -q wal_keep_size_added connect-wal.out"
HC_DB_CONTAINER=${project}-db-1 HC_APP=hctest HC_STATE_DIR=/tmp/hcstate bash $s/disconnect.sh >/dev/null || fail "disconnect after the wal test"
sql 'alter system reset wal_keep_size' && sql 'select pg_reload_conf()' >/dev/null

connect >connect.out 2>connect.err || {
	cat connect.err >&2
	fail "connect.sh exits 0"
}
expect "postgres role created" grep -q '"kind":"postgres_role_created","target":"postgres",.*"new":true' connect.out
expect "replication role created" grep -q '"kind":"replication_role_created",.*"new":true' connect.out
expect "hba line added" grep -q "\"kind\":\"hba_line_added\",\"target\":\"host replication hopcharlie_replicator $subnet scram-sha-256\",.*\"new\":true" connect.out
expect "wal_keep_size added" grep -q '"kind":"wal_keep_size_added",.*"new":true' connect.out
expect "serve config" jq -e '.TCP["5432"].TCPForward == "db:5432"' /tmp/hcstate/hctest/serve.json
pw=$(decrypt "$(tail -1 connect.out | jq -r .replicationPassword)")
expect "password is 64 hex" grep -Eq '^[0-9a-f]{64}$' <(printf '%s\n' "$pw")
if grep -q -- "$pw" connect.out; then fail "no clear password in the output"; else ok "no clear password in the output"; fi
expect "replication connection" docker run --rm --network "$net" -e PGPASSWORD="$pw" postgres:17.11-alpine \
	psql "host=db user=hopcharlie_replicator dbname=app replication=database" -c IDENTIFY_SYSTEM
expect "replication account cannot read data" bash -c "! docker run --rm --network $net -e PGPASSWORD=$pw postgres:17.11-alpine psql 'host=db user=hopcharlie_replicator dbname=app' -c 'select count(*) from users'"
expect "postgres has no password login" bash -c "! docker run --rm --network $net -e PGPASSWORD= postgres:17.11-alpine psql -w 'host=db user=postgres dbname=app' -c 'select 1'"

expect "first run is not a rotation" grep -q '"passwordRotated":false' connect.out
connect >connect2.out 2>/dev/null || fail "connect.sh again exits 0"
expect "rerun rotates the password" grep -q '"passwordRotated":true' connect2.out
expect "rerun adds nothing" bash -c "! grep -q '\"new\":true' connect2.out"
expect "rerun reports the roles" grep -q '"kind":"postgres_role_created",.*"new":false' connect2.out
hba=$(docker exec "${project}-db-1" sh -c 'cat "$PGDATA/pg_hba.conf"')
expect "one hba line" test "$(printf '%s\n' "$hba" | grep -c hopcharlie_replicator)" -eq 1
conf=$(docker exec "${project}-db-1" sh -c 'cat "$PGDATA/postgresql.conf"')
expect "one wal_keep_size line" test "$(printf '%s\n' "$conf" | grep -c hopcharlie-onboarding)" -eq 1
expect "files still owned by postgres" test "$(docker exec "${project}-db-1" sh -c 'stat -c %U "$PGDATA/pg_hba.conf" "$PGDATA/postgresql.conf" | sort -u')" = postgres

# --- disconnect ---

HC_DB_CONTAINER=${project}-db-1 HC_APP=hctest HC_STATE_DIR=/tmp/hcstate bash $s/disconnect.sh >disconnect.out 2>disconnect.err || {
	cat disconnect.err >&2
	fail "disconnect.sh exits 0"
}
for k in wal_keep_size_added hba_line_added replication_role_created postgres_role_created; do
	expect "undid $k" grep -q "\"kind\":\"$k\"" disconnect.out
done
expect "hba line gone" bash -c "! docker exec ${project}-db-1 sh -c 'grep -q hopcharlie_replicator \"\$PGDATA/pg_hba.conf\"'"
expect "roles gone" test "$(docker exec "${project}-db-1" psql -XAtq -U app -d app -c "select count(*) from pg_roles where rolname in ('postgres', 'hopcharlie_replicator')")" -eq 0
expect "wal_keep_size back" test "$(docker exec "${project}-db-1" psql -XAtq -U app -d app -c 'show wal_keep_size')" = 0
HC_DB_CONTAINER=${project}-db-1 HC_APP=hctest HC_STATE_DIR=/tmp/hcstate bash $s/disconnect.sh >disconnect2.out
expect "disconnect again removes nothing" test "$(cat disconnect2.out)" = '{"result":"ok"}'

# A source still fenced read-only (ALTER SYSTEM, as the cutover does): the
# disconnect of a DELETE still undoes everything and leaves the fence.
connect >/dev/null 2>&1 || fail "connect.sh before the fenced disconnect"
sql 'alter system set default_transaction_read_only = on' && sql 'select pg_reload_conf()' >/dev/null
HC_DB_CONTAINER=${project}-db-1 HC_APP=hctest HC_STATE_DIR=/tmp/hcstate bash $s/disconnect.sh >disconnect-fenced.out 2>disconnect-fenced.err || {
	cat disconnect-fenced.err >&2
	fail "disconnect.sh on a fenced source exits 0"
}
for k in wal_keep_size_added hba_line_added replication_role_created postgres_role_created; do
	expect "fenced: undid $k" grep -q "\"kind\":\"$k\"" disconnect-fenced.out
done
expect "fenced: roles gone" test "$(sql "select count(*) from pg_roles where rolname in ('postgres', 'hopcharlie_replicator')")" -eq 0
expect "fenced: hba line gone" bash -c "! docker exec ${project}-db-1 sh -c 'grep -q hopcharlie_replicator \"\$PGDATA/pg_hba.conf\"'"
expect "fenced: wal_keep_size back" test "$(sql 'show wal_keep_size')" = 0
expect "fenced: fence kept" test "$(sql 'show default_transaction_read_only')" = on
sql 'alter system reset default_transaction_read_only' && sql 'select pg_reload_conf()' >/dev/null
expect "fence reset" test "$(sql 'show default_transaction_read_only')" = off

# remove_lines in a container whose directory is read-only: it fails and
# the file stays as it was (the file is a writable bind mount from a
# volume, in the read-only /etc).
ro=hcro-$$
docker volume create "$ro" >/dev/null
docker run --rm -v "$ro:/v" alpine:3.22 sh -c 'printf "a\nhost replication hopcharlie_replicator 10.0.0.0/24 scram-sha-256\nb\n" > /v/x.conf'
docker run -d --rm --read-only --name "$ro" -v "$(docker volume inspect -f '{{.Mountpoint}}' "$ro")/x.conf:/etc/x.conf" alpine:3.22 sleep 120 >/dev/null
rc=0
# shellcheck disable=SC1091,SC2034 # sourced from the mounted scripts; used by remove_lines
(HC_DB_CONTAINER=$ro && source $s/disconnect.sh && remove_lines /etc/x.conf "$HBA_RE") >ro.out 2>/dev/null || rc=$?
expect "read-only remove_lines fails" test "$rc" -ne 0
expect "read-only remove_lines printed nothing" test ! -s ro.out
expect "read-only file unchanged" test "$(docker exec "$ro" grep -c '' /etc/x.conf)" -eq 3
docker rm -f "$ro" >/dev/null
docker volume rm "$ro" >/dev/null

# A database container that is not running: not done.
docker stop "${project}-db-1" >/dev/null
rc=0
HC_DB_CONTAINER=${project}-db-1 HC_APP=hctest HC_STATE_DIR=/tmp/hcstate bash $s/disconnect.sh >/dev/null 2>&1 || rc=$?
expect "stopped database is exit 4" test "$rc" -eq 4
docker start "${project}-db-1" >/dev/null

if [ "$fails" -gt 0 ]; then
	echo "$fails checks failed" >&2
	exit 1
fi
echo "all checks passed"
