#!/usr/bin/env bash
# HopCharlie-Connect: prepares the source database of an onboarding for
# streaming replication to the platform and joins it to the tailnet
# (platform ADR 0042, "3. 승인과 원본 연결", step 4). SSM Run Command runs it
# as root after an operator approved the onboarding. It is idempotent: a
# rerun finds what an earlier run did, does the rest and reports all of it.
#
# Parameters (environment, set by the SSM document from its parameters):
#
#   HC_DB_CONTAINER     the postgres container (dbContainer)
#   HC_DB_SERVICE       its compose service name, the host name the
#                       Tailscale container forwards to (default: the
#                       container's com.docker.compose.service label)
#   HC_NETWORK          the compose network both containers are on (network)
#   HC_SUBNET           that network's subnet, as discovery read it (subnet);
#                       it must still be the network's
#   HC_TS_AUTHKEY       a one-time Tailscale auth key, tag:hop-legacy
#                       (tailscaleAuthKey)
#   HC_HOSTNAME         the tailnet hostname, hop-db-legacy-<app> (hostname)
#   HC_PUBLIC_KEY       the one-time RSA public key, base64 DER (publicKey)
#   HC_APP              the app label (appLabel)
#   HC_SKIP_TAILSCALE   1 skips starting the Tailscale container (tests)
#   HC_STATE_DIR        where the serve config is kept on the VM
#                       (default /var/lib/hopcharlie-onboarding)
#
# Output: one JSON object per line. Every change it made or found made:
#
#   {"kind":"<OnboardingLegacyChangeKind>","target":"...","at":"<UTC>","new":<bool>}
#
# with kinds postgres_role_created, replication_role_created, hba_line_added,
# wal_keep_size_added, tailscale_volume_created, tailscale_container_started
# and tailscale_device_joined, in the order done ("new" false: an earlier
# run did it). The last line is
#
#   {"result":"ok","replicationPassword":"<envelope>","passwordRotated":<bool>}
#
# where the envelope (inspect.sh describes it) holds the new password of
# hopcharlie_replicator, generated here. Every run sets a new one;
# passwordRotated true says the role existed, so whatever holds the old
# password (the target's Secret) must take the new one. A wal_keep_size of
# 1GB or more that was already set stays, and is an "info" line, not a
# change.
#
# The Tailscale container is on the compose network: a manual docker
# compose down cannot remove that network while it runs (the source's own
# deploy is disabled from connecting on).
#
# Exit codes: 0 done; 2 bad parameters; 3 docker or the containers are not
# as expected; 4 encryption failed; 5 a database step failed; 6 the
# settings did not take effect; 7 the Tailscale device did not come up; 8
# the server has configuration changes waiting for a restart or a file with
# errors (nothing was changed).
set -euo pipefail
umask 077

OPENSSL_IMAGE='alpine/openssl:3.5.9@sha256:6821afa2476e11be343f29275f2ca24291720ff0a7148bd131fd8e314ff26d9e'
TAILSCALE_IMAGE='tailscale/tailscale:v1.104.4@sha256:ed6480b33c6eb67c9cbd203ed42b9fb57e361986725b6856def71b7a3d9e276c'
REPLICATION_USER=hopcharlie_replicator
TAILSCALE_TAG=tag:hop-legacy
MARKER=hopcharlie-onboarding
WAL_KEEP_LINE="wal_keep_size = '1GB'  # $MARKER"
# Every session: no statement logged (a password is in one), no waiting
# behind a lock.
SESSION_SQL="set log_statement = 'none'; set log_min_duration_statement = -1; set log_min_error_statement = 'panic'; set pg_stat_statements.track_utility = off; set password_encryption = 'scram-sha-256'; set lock_timeout = '5s';"

HC_DB_CONTAINER=${HC_DB_CONTAINER:-}
HC_DB_SERVICE=${HC_DB_SERVICE:-}
HC_NETWORK=${HC_NETWORK:-}
HC_SUBNET=${HC_SUBNET:-}
HC_TS_AUTHKEY=${HC_TS_AUTHKEY:-}
HC_HOSTNAME=${HC_HOSTNAME:-}
HC_PUBLIC_KEY=${HC_PUBLIC_KEY:-}
HC_APP=${HC_APP:-}
HC_SKIP_TAILSCALE=${HC_SKIP_TAILSCALE:-0}
HC_STATE_DIR=${HC_STATE_DIR:-/var/lib/hopcharlie-onboarding}

die() {
	echo "connect: $2" >&2
	exit "$1"
}

check() { # value regex what
	printf '%s' "$1" | grep -Eq "$2" || die 2 "$3 is not valid"
}

name_re='^[A-Za-z0-9][A-Za-z0-9_.-]{0,127}$'
check "$HC_DB_CONTAINER" "$name_re" dbContainer
check "$HC_NETWORK" "$name_re" network
check "$HC_SUBNET" '^[0-9]{1,3}(\.[0-9]{1,3}){3}/[0-9]{1,2}$' subnet
check "$HC_APP" '^[a-z0-9]([a-z0-9-]{0,38}[a-z0-9])?$' appLabel
check "$HC_HOSTNAME" "^hop-db-legacy-$HC_APP\$" hostname
check "$HC_PUBLIC_KEY" '^[A-Za-z0-9+/]+={0,2}$' publicKey
[ ${#HC_PUBLIC_KEY} -ge 200 ] && [ ${#HC_PUBLIC_KEY} -le 1200 ] || die 2 "publicKey is not valid"
if [ "$HC_SKIP_TAILSCALE" != 1 ]; then
	check "$HC_TS_AUTHKEY" '^tskey-auth-[A-Za-z0-9_-]{10,200}$' tailscaleAuthKey
fi

ts_container=hopcharlie-ts-$HC_APP
ts_volume=$ts_container-state
hba_line="host replication $REPLICATION_USER $HC_SUBNET scram-sha-256"

command -v docker >/dev/null 2>&1 || die 3 "docker is not installed"
docker info >/dev/null 2>&1 || die 3 "docker does not answer"

[ "$(docker container inspect --format '{{.State.Status}}' "$HC_DB_CONTAINER" 2>/dev/null)" = running ] ||
	die 3 "container $HC_DB_CONTAINER is not running"
docker container inspect --format '{{range $k, $v := .NetworkSettings.Networks}}{{$k}}{{println}}{{end}}' "$HC_DB_CONTAINER" | grep -qxF "$HC_NETWORK" ||
	die 3 "container $HC_DB_CONTAINER is not on network $HC_NETWORK"
docker network inspect --format '{{range .IPAM.Config}}{{.Subnet}}{{println}}{{end}}' "$HC_NETWORK" | grep -qxF "$HC_SUBNET" ||
	die 3 "network $HC_NETWORK does not have subnet $HC_SUBNET"
if [ -z "$HC_DB_SERVICE" ]; then
	HC_DB_SERVICE=$(docker container inspect --format '{{index .Config.Labels "com.docker.compose.service"}}' "$HC_DB_CONTAINER")
fi
check "$HC_DB_SERVICE" "$name_re" "the database service name"

now() { date -u +%Y-%m-%dT%H:%M:%SZ; }

change() { # kind target new
	local t=${2//\\/\\\\}
	t=${t//\"/\\\"}
	printf '{"kind":"%s","target":"%s","at":"%s","new":%s}\n' "$1" "$t" "$(now)" "$3"
}

# psql runs SQL from stdin as the container's POSTGRES_USER over the local
# socket and prints unaligned tuples.
psql_db() {
	# shellcheck disable=SC2016 # expanded inside the container
	docker exec -i "$HC_DB_CONTAINER" sh -c 'exec psql -X -A -t -q -v ON_ERROR_STOP=1 -U "${POSTGRES_USER:-postgres}" -d "${POSTGRES_DB:-${POSTGRES_USER:-postgres}}"'
}

encrypt() { # reads the plaintext on stdin, prints the envelope
	# shellcheck disable=SC2016 # expanded inside the container
	docker run --rm -i --pull missing --network none --read-only --cap-drop ALL \
		--security-opt no-new-privileges --tmpfs /w:rw,mode=700,size=16m \
		-e HC_PUB="$HC_PUBLIC_KEY" --entrypoint sh "$OPENSSL_IMAGE" -c '
set -eu
umask 077
cd /w
cat > p
printf "%s" "$HC_PUB" | openssl base64 -d -A > pub.der
openssl rand -hex 32 > k
openssl enc -aes-256-cbc -pbkdf2 -iter 10000 -md sha256 -salt -pass file:k -in p -out c
head -c 64 k > w
openssl dgst -sha256 -binary p >> w
openssl pkeyutl -encrypt -pubin -keyform DER -inkey pub.der -pkeyopt rsa_padding_mode:oaep -pkeyopt rsa_oaep_md:sha256 -pkeyopt rsa_mgf1_md:sha256 -in w -out wk
printf "hcenc1.%s.%s" "$(openssl base64 -A -in wk)" "$(openssl base64 -A -in c)"
'
}

# append_line adds a line to a file in the database container unless it is
# there, keeping the file (inode, owner, mode). It prints true when it added.
append_line() { # file line
	# shellcheck disable=SC2016 # expanded inside the container
	docker exec "$HC_DB_CONTAINER" sh -c '
set -eu
if grep -qxF "$2" "$1"; then echo false; exit 0; fi
[ -z "$(tail -c 1 "$1")" ] || printf "\n" >> "$1"
printf "%s\n" "$2" >> "$1"
echo true' sh "$1" "$2"
}

# --- 1. roles ---

password=$(od -An -tx1 -N32 /dev/urandom | tr -d ' \n')
[ ${#password} -eq 64 ] || die 5 "could not generate a password"

# The statements are not logged (log_statement could print the password);
# the roles this script creates carry the marker as their comment, which is
# how a rerun and disconnect.sh know them from roles that were there.
# Refuse a server that would apply something else with the reload.
pending=$(printf '%s\n' "$SESSION_SQL" "select count(*) from (select name from pg_settings where pending_restart union all select name from pg_file_settings where error is not null) p;" | psql_db) ||
	die 5 "reading the configuration state failed"
[ "$pending" = 0 ] || die 8 "the server has settings waiting for a restart or configuration errors"

roles=$(
	psql_db <<SQL
$SESSION_SQL
select case when exists (select 1 from pg_roles where rolname = 'postgres') then 'postgres:' ||
  coalesce((select shobj_description(oid, 'pg_authid') = '$MARKER' from pg_roles where rolname = 'postgres')::text, 'false')
  else 'postgres:create' end;
select case when exists (select 1 from pg_roles where rolname = '$REPLICATION_USER') then 'replicator:exists' else 'replicator:create' end;
SQL
) || die 5 "creating the roles failed"

case "$roles" in
*postgres:create*)
	psql_db <<SQL >/dev/null || die 5 "creating role postgres failed"
create role postgres superuser login;
comment on role postgres is '$MARKER';
SQL
	change postgres_role_created postgres true
	;;
*postgres:true*) change postgres_role_created postgres false ;;
esac

case "$roles" in
*replicator:create*) verb=create new=true rotated=false ;;
*) verb=alter new=false rotated=true ;;
esac
# printf, not a here-document: bash may keep a here-document in a file.
printf '%s\n' "$SESSION_SQL" "$verb role $REPLICATION_USER login replication password '$password';" \
	"comment on role $REPLICATION_USER is '$MARKER';" | psql_db >/dev/null ||
	die 5 "setting up role $REPLICATION_USER failed"
change replication_role_created "$REPLICATION_USER" "$new"

replication_password=$(printf '%s' "$password" | encrypt) || die 4 "encrypting the password failed"
unset password
case "$replication_password" in
hcenc1.*) ;;
*) die 4 "encrypting the password printed no envelope" ;;
esac

# --- 2. hba and wal_keep_size, applied with a reload ---

files=$(printf 'show hba_file;\nshow config_file;\n' | psql_db) || die 5 "reading the file locations failed"
hba_file=$(printf '%s\n' "$files" | sed -n 1p)
config_file=$(printf '%s\n' "$files" | sed -n 2p)
[ -n "$hba_file" ] && [ -n "$config_file" ] || die 5 "the server did not name its hba and config files"

added=$(append_line "$hba_file" "$hba_line") || die 5 "appending to $hba_file failed"
change hba_line_added "$hba_line" "$added"
# A wal_keep_size of 1GB or more set by someone else stays.
keep=$(printf '%s\n' "$SESSION_SQL" "select setting from pg_settings where name = 'wal_keep_size';" | psql_db) ||
	die 5 "reading wal_keep_size failed"
ours=$(docker exec "$HC_DB_CONTAINER" sh -c 'grep -cxF "$2" "$1" || true' sh "$config_file" "$WAL_KEEP_LINE")
if [ "${ours:-0}" -eq 0 ] && [ "${keep:-0}" -ge 1024 ] 2>/dev/null; then
	printf '{"info":"wal_keep_size is already %sMB","at":"%s"}\n' "$keep" "$(now)"
else
	added=$(append_line "$config_file" "$WAL_KEEP_LINE") || die 5 "appending to $config_file failed"
	change wal_keep_size_added "$WAL_KEEP_LINE" "$added"
fi

applied=$(
	psql_db <<SQL
select pg_reload_conf();
select pg_sleep(1);
select 'hba_errors:' || count(*) from pg_hba_file_rules where error is not null;
select 'hba_line:' || count(*) from pg_hba_file_rules
  where type = 'host' and database = '{replication}' and user_name = '{$REPLICATION_USER}' and auth_method = 'scram-sha-256';
select 'wal_keep_mb:' || setting from pg_settings where name = 'wal_keep_size';
-- an earlier rule that takes replication connections from the network
-- (the official image's loopback trust lines do not)
select 'earlier_rules:' || count(*) from pg_hba_file_rules r
  where r.line_number < (select min(line_number) from pg_hba_file_rules
      where database = '{replication}' and user_name = '{$REPLICATION_USER}' and address = '${HC_SUBNET%/*}')
    and r.type like 'host%' and ('replication' = any(r.database))
    and coalesce(r.address, '') not in ('127.0.0.1', '::1');
SQL
) || die 6 "reloading the configuration failed"
case "$applied" in
*hba_errors:0*) ;;
*) die 6 "pg_hba.conf has errors after the change" ;;
esac
case "$applied" in
*hba_line:0*) die 6 "the replication hba line is not in effect" ;;
esac
kept=$(printf '%s\n' "$applied" | sed -n 's/^wal_keep_mb://p')
[ "${kept:-0}" -ge 1024 ] 2>/dev/null || die 6 "wal_keep_size is under 1GB after the reload"
case "$applied" in
*earlier_rules:0*) ;;
*) die 6 "an earlier pg_hba.conf rule takes replication connections before $REPLICATION_USER's line" ;;
esac

# --- 3. the Tailscale container ---

# tailscale serve forwards the tailnet's TCP 5432 to the database service
# on the compose network.
serve_dir=$HC_STATE_DIR/$HC_APP
mkdir -p "$serve_dir"
chmod 755 "$HC_STATE_DIR" "$serve_dir"
printf '{"TCP":{"5432":{"TCPForward":"%s:5432"}}}\n' "$HC_DB_SERVICE" >"$serve_dir/serve.json"
chmod 644 "$serve_dir/serve.json"

if [ "$HC_SKIP_TAILSCALE" = 1 ]; then
	printf '{"result":"ok","replicationPassword":"%s","passwordRotated":%s,"tailscale":"skipped"}\n' "$replication_password" "$rotated"
	exit 0
fi

if docker volume inspect "$ts_volume" >/dev/null 2>&1; then
	change tailscale_volume_created "$ts_volume" false
else
	docker volume create --label "hopcharlie.dev/onboarding=$HC_APP" "$ts_volume" >/dev/null
	change tailscale_volume_created "$ts_volume" true
fi

state=$(docker container inspect --format '{{.State.Status}}' "$ts_container" 2>/dev/null || true)
case "$state" in
running)
	change tailscale_container_started "$ts_container" false
	joined_before=true
	;;
'')
	# The auth key goes in through the environment, not the command line.
	# No compose label: the source's docker compose up leaves it alone.
	TS_AUTHKEY=$HC_TS_AUTHKEY docker run -d --pull missing --name "$ts_container" \
		--label "hopcharlie.dev/onboarding=$HC_APP" --network "$HC_NETWORK" --restart unless-stopped \
		-v "$ts_volume:/var/lib/tailscale" -v "$serve_dir:/config:ro" \
		-e TS_AUTHKEY -e TS_AUTH_ONCE=true -e TS_USERSPACE=true -e TS_STATE_DIR=/var/lib/tailscale \
		-e TS_HOSTNAME="$HC_HOSTNAME" -e TS_SERVE_CONFIG=/config/serve.json \
		-e TS_EXTRA_ARGS="--advertise-tags=$TAILSCALE_TAG" \
		"$TAILSCALE_IMAGE" >/dev/null || die 3 "starting $ts_container failed"
	change tailscale_container_started "$ts_container" true
	;;
*)
	docker start "$ts_container" >/dev/null || die 3 "starting $ts_container failed"
	change tailscale_container_started "$ts_container" false
	;;
esac
unset HC_TS_AUTHKEY

joined=false
for _ in $(seq 1 60); do
	if docker exec "$ts_container" tailscale status --json 2>/dev/null | grep -Eq '"BackendState": *"Running"'; then
		joined=true
		break
	fi
	sleep 2
done
[ "$joined" = true ] || die 7 "the Tailscale device did not come up in 120s"
if [ "${joined_before:-false}" = true ]; then
	change tailscale_device_joined "$HC_HOSTNAME" false
else
	change tailscale_device_joined "$HC_HOSTNAME" true
fi

printf '{"result":"ok","replicationPassword":"%s","passwordRotated":%s}\n' "$replication_password" "$rotated"
