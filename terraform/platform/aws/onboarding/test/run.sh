#!/usr/bin/env bash
# Runs inspect.sh, connect.sh and disconnect.sh against a compose stand-in
# (compose.yaml) on the local docker, without SSM: the scripts run in a
# container that has the docker socket, as SSM would run them on the VM.
# Tailscale is skipped (HC_SKIP_TAILSCALE=1); its serve config is checked.
#
#   terraform/platform/aws/onboarding/test/run.sh
set -euo pipefail
here=$(cd "$(dirname "$0")" && pwd)
# Docker Desktop on Windows wants a Windows path for bind mounts.
dir=$(cd "$here/.." && { pwd -W 2>/dev/null || pwd; })
cd "$here"

docker compose -f compose.yaml up -d --wait --quiet-pull
trap 'docker compose -f compose.yaml down -v --remove-orphans >/dev/null 2>&1' EXIT

docker compose -f compose.yaml exec -T db psql -q -U app -d app <<'SQL'
create table users (id bigserial primary key, email text not null, name text check (name > 'a'));
create index users_email_idx on users (email);
create index users_email_c_idx on users (email collate "C");
create table events (at text not null) partition by range (at);
insert into users (email, name) select 'u' || g || '@example.com', 'n' || g from generate_series(1, 1000) g;
SQL

MSYS_NO_PATHCONV=1 docker run --rm -v /var/run/docker.sock:/var/run/docker.sock -v /var/lib/docker:/var/lib/docker:ro \
	-v "$dir:/scripts:ro" -e HC_TEST_PROJECT=hcinspecttest docker:29-cli \
	sh -c 'apk add -q --no-cache bash coreutils jq openssl >/dev/null && exec bash /scripts/test/checks.sh'
