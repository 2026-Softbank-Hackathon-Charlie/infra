#!/usr/bin/env bash
# HopCharlie-Disconnect: undoes what connect.sh added to the source VM and
# database (platform ADR 0042, DELETE from connecting to ready), in the
# reverse order. SSM Run Command runs it as root. It only removes what
# connect.sh made: the Tailscale container, volume and serve config of the
# app, the replication hba line, the wal_keep_size line with connect.sh's
# marker, hopcharlie_replicator, and the postgres role when its comment is
# connect.sh's marker (a postgres role that was there stays). Running it
# again, or on a VM connect.sh never touched, changes nothing.
#
# Parameters (environment, set by the SSM document from its parameters):
#
#   HC_DB_CONTAINER   the postgres container (dbContainer); a container that
#                     is gone skips the database steps
#   HC_APP            the app label (appLabel)
#   HC_STATE_DIR      as for connect.sh
#
# Output: one JSON object per line for every thing it removed:
#
#   {"kind":"<OnboardingLegacyChangeKind>","target":"...","undoneAt":"<UTC>"}
#
# then {"result":"ok"}. The tailnet device itself is deleted by the
# platform through the Tailscale API; this logs the node out first.
#
# Exit codes: 0 done; 2 bad parameters; 3 docker unavailable; 4 the
# database container is not running (its changes are not undone: run again
# once it runs); 5 a database step failed (a file it could not rewrite is
# left as it was).
set -euo pipefail
umask 077

REPLICATION_USER=hopcharlie_replicator
MARKER=hopcharlie-onboarding
HBA_RE="^host replication $REPLICATION_USER [0-9./]+ scram-sha-256\$"

HC_DB_CONTAINER=${HC_DB_CONTAINER:-}
HC_APP=${HC_APP:-}
HC_STATE_DIR=${HC_STATE_DIR:-/var/lib/hopcharlie-onboarding}

die() {
	echo "disconnect: $2" >&2
	exit "$1"
}

# remove_lines drops the lines matching an extended regex from a file in
# the database container, keeping the file (inode, owner, mode), and prints
# the removed lines once the file is written back. It fails, and leaves the
# file as it was, when the copy beside the file cannot be written or does
# not have exactly the other lines.
remove_lines() { # file regex
	# shellcheck disable=SC2016 # expanded inside the container
	docker exec "$HC_DB_CONTAINER" sh -c '
set -eu
f=$1
re=$2
rc=0
grep -qE "$re" "$f" || rc=$?
[ "$rc" -eq 1 ] && exit 0
[ "$rc" -eq 0 ] || exit 2
t="$f.hopcharlie.$$"
trap "rm -f \"$t\"" EXIT
rc=0
grep -vE "$re" "$f" >"$t" || rc=$?
[ "$rc" -le 1 ] || exit 2
total=$(grep -c "" "$f")
removed=$(grep -cE "$re" "$f")
kept=$(grep -c "" "$t" || true)
[ "$kept" -eq $((total - removed)) ] || exit 3
lines=$(grep -E "$re" "$f")
cat "$t" >"$f"
printf "%s\n" "$lines"' sh "$1" "$2"
}

# Sourced by the tests: the functions only.
if [ "${BASH_SOURCE[0]}" != "$0" ]; then
	return 0
fi

printf '%s' "$HC_DB_CONTAINER" | grep -Eq '^[A-Za-z0-9][A-Za-z0-9_.-]{0,127}$' || die 2 "dbContainer is not valid"
printf '%s' "$HC_APP" | grep -Eq '^[a-z0-9]([a-z0-9-]{0,38}[a-z0-9])?$' || die 2 "appLabel is not valid"

command -v docker >/dev/null 2>&1 || die 3 "docker is not installed"
docker info >/dev/null 2>&1 || die 3 "docker does not answer"

ts_container=hopcharlie-ts-$HC_APP
ts_volume=$ts_container-state

undone() { # kind target
	local t=${2//\\/\\\\}
	t=${t//\"/\\\"}
	printf '{"kind":"%s","target":"%s","undoneAt":"%s"}\n' "$1" "$t" "$(date -u +%Y-%m-%dT%H:%M:%SZ)"
}

# --- the Tailscale container (reverse of connect.sh step 3) ---

if docker container inspect "$ts_container" >/dev/null 2>&1; then
	hostname=$(docker container inspect --format '{{range .Config.Env}}{{println .}}{{end}}' "$ts_container" | sed -n 's/^TS_HOSTNAME=//p')
	docker exec "$ts_container" tailscale logout >/dev/null 2>&1 || true
	[ -n "$hostname" ] && undone tailscale_device_joined "$hostname"
	docker rm -f "$ts_container" >/dev/null
	undone tailscale_container_started "$ts_container"
fi
if docker volume inspect "$ts_volume" >/dev/null 2>&1; then
	docker volume rm "$ts_volume" >/dev/null
	undone tailscale_volume_created "$ts_volume"
fi
rm -rf "${HC_STATE_DIR:?}/$HC_APP"

# --- the database (reverse of steps 2 and 1) ---

if [ "$(docker container inspect --format '{{.State.Status}}' "$HC_DB_CONTAINER" 2>/dev/null)" != running ]; then
	# Not done: the database changes are still there for the next run.
	echo '{"result":"incomplete","database":"not running"}'
	exit 4
fi

psql_db() {
	# shellcheck disable=SC2016 # expanded inside the container
	docker exec -i "$HC_DB_CONTAINER" sh -c 'exec psql -X -A -t -q -v ON_ERROR_STOP=1 -U "${POSTGRES_USER:-postgres}" -d "${POSTGRES_DB:-${POSTGRES_USER:-postgres}}"'
}

files=$(printf 'show hba_file;\nshow config_file;\n' | psql_db) || die 5 "reading the file locations failed"
hba_file=$(printf '%s\n' "$files" | sed -n 1p)
config_file=$(printf '%s\n' "$files" | sed -n 2p)

[ -n "$hba_file" ] && [ -n "$config_file" ] || die 5 "the server did not name its hba and config files"
wal_re="^wal_keep_size = '1GB'  # $MARKER\$"
removed=$(remove_lines "$config_file" "$wal_re") || die 5 "removing the wal_keep_size line from $config_file failed; the file is unchanged"
while IFS= read -r line; do
	[ -n "$line" ] && undone wal_keep_size_added "$line"
done <<<"$removed"
removed=$(remove_lines "$hba_file" "$HBA_RE") || die 5 "removing the hba line from $hba_file failed; the file is unchanged"
while IFS= read -r line; do
	[ -n "$line" ] && undone hba_line_added "$line"
done <<<"$removed"
printf 'select pg_reload_conf();\n' | psql_db >/dev/null || die 5 "reloading the configuration failed"

# Each session turns default_transaction_read_only off for itself: DELETE of
# an onboarding may run while the source is still fenced read-only by
# ALTER SYSTEM (platform ADR 0042, 5.), and DROP ROLE needs a writable
# transaction. The fence itself stays as it is.
roles=$(
	psql_db <<SQL
SET default_transaction_read_only = off;
select pg_terminate_backend(pid) from pg_stat_activity where usename = '$REPLICATION_USER' \g /dev/null
select 'replicator' where exists (select 1 from pg_roles where rolname = '$REPLICATION_USER');
select 'postgres' from pg_roles where rolname = 'postgres' and shobj_description(oid, 'pg_authid') = '$MARKER';
SQL
) || die 5 "removing the roles failed"
case "$roles" in
*replicator*)
	printf 'SET default_transaction_read_only = off;\ndrop role if exists %s;\n' "$REPLICATION_USER" | psql_db >/dev/null || die 5 "dropping $REPLICATION_USER failed"
	undone replication_role_created "$REPLICATION_USER"
	;;
esac
case "$roles" in
*postgres*)
	printf 'SET default_transaction_read_only = off;\ndrop role postgres;\n' | psql_db >/dev/null || die 5 "dropping role postgres failed"
	undone postgres_role_created postgres
	;;
esac

echo '{"result":"ok"}'
