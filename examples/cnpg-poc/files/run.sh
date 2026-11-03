#!/bin/sh
# No shell tracing, CLI passwords, credential output, or Kubernetes API access.
set -eu
case "$POC_SERVICE" in a) other=b;; b) other=a;; *) exit 1;; esac
case "$POC_MODE" in migrate|recover-a) role=owner;; verify|network-denied|marker|fingerprint) role=runtime;; *) exit 1;; esac
credentials_directory="${POC_CREDENTIALS_DIR:-/credentials}"
export PGUSER="$(cat "$credentials_directory/username")"
export PGPASSWORD="$(cat "$credentials_directory/password")"
expected="svc_${POC_SERVICE}_${role}"
[ "$PGUSER" = "$expected" ] || { echo 'Credential identity mismatch' >&2; exit 1; }
[ "$PGSSLMODE" = verify-full ] || exit 1
psql_check() { psql -X --no-password --set=ON_ERROR_STOP=1 "$@"; }
error_file="${POC_TMP_DIR:-/tmp}/negative-check.stderr"
dump_file="${POC_TMP_DIR:-/tmp}/db-a-recovery.sql"
trap 'rm -f "$error_file" "$dump_file"' EXIT

if [ "$POC_MODE" = network-denied ]; then
    if psql_check --command='SELECT 1' >/dev/null 2>"$error_file"; then
        echo 'Unlabelled Pod unexpectedly reached the database' >&2; exit 1
    fi
    grep -Eiq 'timeout expired|Connection timed out' "$error_file" || {
        echo 'Expected a network timeout; another error is not proof of isolation' >&2; exit 1;
    }
    echo 'unlabelled-pod-network-denial-passed'
elif [ "$POC_MODE" = migrate ]; then
    psql_check --set="expected_user=$expected" --set="runtime_role=svc_${POC_SERVICE}_runtime" \
        --file=/checks/migrate.sql
elif [ "$POC_MODE" = marker ]; then
    psql_check --set="marker_label=$POC_MARKER_LABEL" --file=/checks/marker.sql
elif [ "$POC_MODE" = fingerprint ]; then
    psql_check --file=/checks/fingerprint.sql
elif [ "$POC_MODE" = recover-a ]; then
    [ "$POC_SERVICE" = a ] || exit 1
    [ "$PGHOST" = postgres-poc-rw.charlie-db.svc.cluster.local ] || exit 1
    [ "$POC_RESTORE_HOST" = postgres-poc-restore-rw.charlie-db.svc.cluster.local ] || exit 1
    # Only the synthetic fixture schema is supported. pg_dump completes before
    # source mutation; no pipelines can hide a dump error. Writes must be fenced.
    umask 077
    (PGHOST="$POC_RESTORE_HOST" PGSSLROOTCERT="$POC_RESTORE_CA" \
        pg_dump --no-password --schema=app --clean --if-exists --no-owner \
        --no-privileges --file="$dump_file")
    psql_check --single-transaction --file="$dump_file"
    psql_check --set="expected_user=$expected" --set="runtime_role=svc_a_runtime" \
        --file=/checks/grants.sql
    echo 'synthetic-schema-a-recovery-complete-verify-before-reopening-writes'
else
    psql_check --set="expected_user=$expected" --set="owner_role=svc_${POC_SERVICE}_owner" \
        --set="other_db=db_$other" --file=/checks/verify.sql
    if (PGDATABASE="db_$other" psql_check --command='SELECT 1') >/dev/null 2>"$error_file"; then
        echo 'Cross-service database connection unexpectedly succeeded' >&2; exit 1
    fi
    grep -Eiq 'permission denied for database|pg_hba.conf rejects connection|no pg_hba.conf entry' "$error_file" || {
        echo 'Cross-service check failed for an unrelated reason' >&2; exit 1;
    }
    # Retain the correct socket destination but deliberately mismatch TLS hostname.
    destination="$(getent ahostsv4 "$PGHOST" | awk 'NR==1 {print $1}')"
    [ -n "$destination" ] || { echo 'Cannot resolve validated endpoint for TLS test' >&2; exit 1; }
    if (PGHOSTADDR="$destination" PGHOST=wrong-host.example.invalid \
        psql_check --command='SELECT 1') >/dev/null 2>"$error_file"; then
        echo 'Wrong TLS hostname unexpectedly succeeded' >&2; exit 1
    fi
    grep -Fq 'does not match host name' "$error_file" || {
        echo 'Expected TLS hostname rejection; another error is not proof' >&2; exit 1;
    }
    echo 'cross-service-connect-denial-and-tls-hostname-verification-passed'
fi
