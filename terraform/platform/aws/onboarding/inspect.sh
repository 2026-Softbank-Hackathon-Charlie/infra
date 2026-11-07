#!/usr/bin/env bash
# HopCharlie-Inspect: read-only inspection of a VM that runs an app with
# docker compose (platform ADR 0042, "1. 조사"). SSM Run Command runs it as
# root; it changes nothing on the VM but its own output file under
# HC_WORK_DIR, which holds no clear secret value, and pulls the pinned
# openssl image when docker does not have it.
#
# Parameters (environment, set by the SSM document from its parameters):
#
#   HC_MODE        run (default) | chunk | clean
#   HC_PUBLIC_KEY  the inspection's one-time RSA public key, base64 of the
#                  DER SubjectPublicKeyInfo (document parameter publicKey)
#   HC_MAX_CHUNK   characters per chunk, 1000..24000 (parameter maxChunk,
#                  default 20000)
#   HC_CHUNK       chunk mode: the chunk index to print (1..HC_MAX_CHUNKS-1)
#   HC_MAX_CHUNKS  how many chunk steps the document has (default 10)
#
# The SSM document runs this script once per step: step "inspect" with
# HC_MODE=run, steps "chunk1".."chunk9" with HC_MODE=chunk HC_CHUNK=<i>,
# and step "cleanup" with HC_MODE=clean. Every step prints at most
# HC_MAX_CHUNK characters (GetCommandInvocation returns the first 24,000
# characters of each step), so the platform reads the steps it needs.
#
# Output of every step: one header line, then (unless empty) one body line:
#
#   HOPCHARLIE-INSPECT v1 chunk=<i> chunks=<n> sha256=<hex>
#   <part i of the payload>
#
# The payload is base64(gzip(document JSON)), cut into n parts; sha256 is of
# the whole payload text. A chunk step with i >= n prints only the header.
#
# The document (version 1) is:
#
#   {"version":1, "at":"<UTC>", "docker":"<server version>",
#    "compose":"<compose version>", "dockerRoot":"/var/lib/docker",
#    "freeDiskBytes":<n>,
#    "containers":[{"id","name","image","imageId","repoDigests":[...],
#      "project","service","workingDir","configFiles","revision","state",
#      "health","startedAt","restart","memory","ports":<docker Ports>,
#      "exposed":<docker ExposedPorts>,"networks":<docker Networks>,
#      "mounts":<docker Mounts>,"env":[{"name","image":<bool>}]}],
#    "networks":[{"name","driver","ipam":<docker IPAM.Config>,"internal"}],
#    "volumes":[{"name","sizeBytes"}],
#    "db":[{"container", "facts":{...} | "error":"..."}],
#    "secrets":"<envelope>"}
#
# Only compose containers (label com.docker.compose.project) are listed.
# "env" are the names of each container's variables, "image" true when the
# variable is exactly the image's own (a default such as PATH). No value
# is in the document in clear: "secrets" is the envelope of
# {"<container name>":["NAME=value", ...]} (docker's Config.Env of every
# listed container), made in a pinned openssl container:
#
#   pass   = 64 random hex characters
#   body   = openssl enc -aes-256-cbc -pbkdf2 -iter 10000 -md sha256 -salt
#            ("Salted__" || salt(8) || AES-256-CBC ciphertext; key || iv =
#            PBKDF2-HMAC-SHA256(pass, salt, 10000, 48))
#   key    = RSA-OAEP(SHA-256, MGF1 SHA-256)(pass || SHA-256(plaintext))
#   envelope = "hcenc1." || base64(key) || "." || base64(body)
#
# "db" holds what psql read inside every running postgres container,
# connected over its local socket as the container's own POSTGRES_USER to
# its POSTGRES_DB (expanded inside the container: this script never sees
# them). See db_sql below for the fields.
#
# Exit codes: 0 done; 2 bad parameters; 3 docker unavailable; 4 encryption
# failed; 5 the payload needs more chunks than the document has; 6 chunk
# mode found no output of this key (not run, or cleaned); 7 the openssl
# image cannot be pulled (is Docker Hub reachable from the VM?). Exit 3 is
# also bash older than 4.4.
set -euo pipefail
umask 077

# The images it runs, pinned by digest (multi-arch index).
OPENSSL_IMAGE='alpine/openssl:3.5.9@sha256:6821afa2476e11be343f29275f2ca24291720ff0a7148bd131fd8e314ff26d9e'

HC_MODE=${HC_MODE:-run}
HC_PUBLIC_KEY=${HC_PUBLIC_KEY:-}
HC_MAX_CHUNK=${HC_MAX_CHUNK:-20000}
HC_MAX_CHUNKS=${HC_MAX_CHUNKS:-10}
HC_CHUNK=${HC_CHUNK:-0}
HC_WORK_DIR=${HC_WORK_DIR:-/run/hopcharlie-inspect}
# Only this compose project (tests); empty lists every project.
HC_PROJECT=${HC_PROJECT:-}
# Seconds between the two WAL position reads (the WAL rate).
HC_WAL_SAMPLE=${HC_WAL_SAMPLE:-3}

die() {
	echo "inspect: $2" >&2
	exit "$1"
}

# 4.4 for empty arrays under set -u.
if [ "${BASH_VERSINFO[0]}" -lt 4 ] || { [ "${BASH_VERSINFO[0]}" -eq 4 ] && [ "${BASH_VERSINFO[1]}" -lt 4 ]; }; then
	die 3 "bash 4.4 or later is needed (this is $BASH_VERSION)"
fi

case "$HC_MAX_CHUNK" in
'' | *[!0-9]*) die 2 "maxChunk must be a number" ;;
esac
if [ "$HC_MAX_CHUNK" -lt 1000 ] || [ "$HC_MAX_CHUNK" -gt 24000 ]; then
	die 2 "maxChunk must be 1000..24000"
fi
case "$HC_MAX_CHUNKS$HC_CHUNK$HC_WAL_SAMPLE" in
*[!0-9]*) die 2 "HC_MAX_CHUNKS, HC_CHUNK and HC_WAL_SAMPLE must be numbers" ;;
esac
# base64 of a DER RSA key of 2048..4096 bits.
if [ ${#HC_PUBLIC_KEY} -lt 200 ] || [ ${#HC_PUBLIC_KEY} -gt 1200 ] ||
	! printf '%s' "$HC_PUBLIC_KEY" | grep -Eq '^[A-Za-z0-9+/]+={0,2}$'; then
	die 2 "publicKey must be base64 of a DER public key"
fi

# The output file of this inspection: one per key, as every inspection
# makes its own key pair.
key_id=$(printf '%s' "$HC_PUBLIC_KEY" | sha256sum | cut -c1-16)
out_file="$HC_WORK_DIR/$key_id.b64"

header() { # chunk chunks sha
	printf 'HOPCHARLIE-INSPECT v1 chunk=%s chunks=%s sha256=%s\n' "$1" "$2" "$3"
}

print_chunk() { # index
	local total n sha
	total=$(wc -c <"$out_file" | tr -d ' ')
	n=$(((total + HC_MAX_CHUNK - 1) / HC_MAX_CHUNK))
	sha=$(sha256sum <"$out_file" | cut -c1-64)
	header "$1" "$n" "$sha"
	if [ "$1" -lt "$n" ]; then
		tail -c +"$(($1 * HC_MAX_CHUNK + 1))" "$out_file" | head -c "$HC_MAX_CHUNK"
		echo
	fi
}

case "$HC_MODE" in
chunk)
	[ -f "$out_file" ] || die 6 "no inspection output for this key"
	print_chunk "$HC_CHUNK"
	exit 0
	;;
clean)
	rm -f "$out_file"
	exit 0
	;;
run) ;;
*) die 2 "HC_MODE must be run, chunk or clean" ;;
esac

command -v docker >/dev/null 2>&1 || die 3 "docker is not installed"
docker info >/dev/null 2>&1 || die 3 "docker does not answer"
if [ "$HC_MODE" = run ] && ! docker image inspect "$OPENSSL_IMAGE" >/dev/null 2>&1; then
	docker pull -q "$OPENSSL_IMAGE" >/dev/null 2>&1 || die 7 "cannot pull $OPENSSL_IMAGE (is Docker Hub reachable?)"
fi

# json_str prints its argument as a JSON string.
json_str() {
	local s=$1
	s=${s//\\/\\\\}
	s=${s//\"/\\\"}
	s=${s//$'\n'/\\n}
	s=${s//$'\r'/\\r}
	s=${s//$'\t'/\\t}
	# Other control characters are dropped.
	s=$(printf '%s' "$s" | LC_ALL=C tr -d '\000-\010\013\014\016-\037')
	printf '"%s"' "$s"
}

# join prints its arguments separated by commas.
join() {
	local IFS=,
	printf '%s' "$*"
}

# The SQL of the database facts, in the app's database. It prints one JSON
# object. :'l1' and :t1 are the first WAL position read and its time.
db_sql() {
	cat <<'SQL'
\set ON_ERROR_STOP on
set application_name = 'hopcharlie-inspect';
set statement_timeout = '60s';
-- never wait behind a lock: every read below is of the catalogs
set lock_timeout = '1s';
select pg_current_wal_lsn() as l1, extract(epoch from clock_timestamp()) as t1 \gset
select pg_sleep(:sleep) \g /dev/null
with
db as (
  select d.*, to_jsonb(d) as j from pg_database d where d.datname = current_database()
),
dflt as (
  -- the database's own collation, what collation oid 100 (default) means
  select coalesce(db.j->>'datlocprovider', 'c') as provider,
         db.datcollate as dcollate,
         coalesce(db.j->>'datlocale', db.j->>'daticulocale') as locale
  from db
),
-- the effective collation of every collation oid used below
coll as (
  select c.oid,
         case when c.oid = 100 then (select provider from dflt) else c.collprovider::text end as provider,
         case when c.oid = 100 then
                case (select provider from dflt) when 'c' then (select dcollate from dflt) else (select locale from dflt) end
              when c.collprovider = 'c' then c.collcollate
              else coalesce(to_jsonb(c)->>'colllocale', to_jsonb(c)->>'colliculocale', c.collcollate) end as name
  from pg_collation c
),
clike as (
  select oid, provider, name,
         (provider = 'b' or name in ('C', 'POSIX', 'C.UTF-8', 'C.utf8', 'ucs_basic')) as c_like
  from coll
),
idx as (
  select distinct on (i.indexrelid)
         quote_ident(n.nspname) || '.' || quote_ident(ci.relname) as name,
         quote_ident(tn.nspname) || '.' || quote_ident(ct.relname) as tbl,
         cl.provider, cl.name as coll_name,
         ci.relpages::bigint * current_setting('block_size')::bigint as size
  from pg_index i
  join pg_class ci on ci.oid = i.indexrelid
  join pg_namespace n on n.oid = ci.relnamespace
  join pg_class ct on ct.oid = i.indrelid
  join pg_namespace tn on tn.oid = ct.relnamespace
  cross join lateral unnest(i.indcollation::oid[]) as u(colloid)
  join clike cl on cl.oid = u.colloid
  where not cl.c_like
    and n.nspname not in ('pg_catalog', 'information_schema') and n.nspname not like 'pg_toast%'
  order by i.indexrelid
),
partkeys as (
  -- text range partition keys of a collation that is not C-like
  select quote_ident(n.nspname) || '.' || quote_ident(c.relname) || '.partition_key' as name
  from pg_partitioned_table p
  join pg_class c on c.oid = p.partrelid
  join pg_namespace n on n.oid = c.relnamespace
  cross join lateral unnest(p.partcollation::oid[]) as u(colloid)
  join clike cl on cl.oid = u.colloid
  where p.partstrat = 'r' and not cl.c_like
),
checks as (
  -- CHECK constraints on a column of a collation that is not C-like that
  -- order or case-fold text
  select distinct quote_ident(n.nspname) || '.' || quote_ident(c.relname) || '.' || quote_ident(con.conname) as name
  from pg_constraint con
  join pg_class c on c.oid = con.conrelid
  join pg_namespace n on n.oid = c.relnamespace
  cross join lateral unnest(con.conkey) as k(attnum)
  join pg_attribute a on a.attrelid = con.conrelid and a.attnum = k.attnum
  join clike cl on cl.oid = a.attcollation
  where con.contype = 'c' and not cl.c_like
    and n.nspname not in ('pg_catalog', 'information_schema')
    and pg_get_constraintdef(con.oid) ~* '(<|>|between|lower\(|upper\(|initcap\(|~\*|ilike)'
),
tbls as (
  select quote_ident(n.nspname) || '.' || quote_ident(c.relname) as name,
         greatest(c.reltuples, 0)::bigint as nrows,
         (c.relpages::bigint + coalesce((select t.relpages from pg_class t where t.oid = c.reltoastrelid), 0))
           * current_setting('block_size')::bigint as size
  from pg_class c join pg_namespace n on n.oid = c.relnamespace
  where c.relkind in ('r', 'p', 'm')
    and n.nspname not in ('pg_catalog', 'information_schema') and n.nspname not like 'pg_toast%'
)
select json_build_object(
  'serverVersionNum', current_setting('server_version_num')::int,
  'version', version(),
  'user', current_user,
  'database', current_database(),
  'owner', (select pg_get_userbyid(datdba) from db),
  'databases', (select coalesce(json_agg(json_build_object(
      'name', d.datname, 'owner', pg_get_userbyid(d.datdba),
      'locProvider', coalesce(to_jsonb(d)->>'datlocprovider', 'c'),
      'collate', d.datcollate, 'ctype', d.datctype,
      'locale', coalesce(to_jsonb(d)->>'datlocale', to_jsonb(d)->>'daticulocale'),
      'collVersion', to_jsonb(d)->>'datcollversion',
      'sizeBytes', case when has_database_privilege(d.oid, 'connect') then pg_database_size(d.oid) end
    ) order by d.datname), '[]') from pg_database d where not d.datistemplate),
  'lcMonetary', current_setting('lc_monetary'),
  'lcNumeric', current_setting('lc_numeric'),
  'lcTime', current_setting('lc_time'),
  'lcMessages', current_setting('lc_messages'),
  'autoConf', (select coalesce(json_agg(distinct name), '[]') from (
      select name from pg_file_settings where sourcefile like '%postgresql.auto.conf'
      union select name from pg_settings where sourcefile like '%postgresql.auto.conf') a),
  'allowAlterSystem', current_setting('allow_alter_system', true),
  'walLevel', current_setting('wal_level'),
  'maxWalSenders', current_setting('max_wal_senders')::int,
  'passwordEncryption', current_setting('password_encryption'),
  'synchronousCommit', current_setting('synchronous_commit'),
  'readOnlyRoleSettings', (select coalesce(json_agg(coalesce(r.rolname, '') || '@' || coalesce(d.datname, '')), '[]')
      from pg_db_role_setting s
      left join pg_roles r on r.oid = s.setrole
      left join pg_database d on d.oid = s.setdatabase
      where exists (select 1 from unnest(s.setconfig) c where c like 'default_transaction_read_only=%')),
  'walKeepSize', current_setting('wal_keep_size', true),
  'walKeepSizeSource', (select source from pg_settings where name = 'wal_keep_size'),
  'superusers', (select coalesce(json_agg(rolname order by rolname), '[]') from pg_roles where rolsuper and rolcanlogin),
  'postgresRole', exists (select 1 from pg_roles where rolname = 'postgres'),
  'hbaFile', current_setting('hba_file'),
  'configFile', current_setting('config_file'),
  'dataDirectory', current_setting('data_directory'),
  'port', current_setting('port')::int,
  'walBytesPerSecond', (pg_wal_lsn_diff(pg_current_wal_lsn(), :'l1') /
      greatest(extract(epoch from clock_timestamp()) - :t1, 0.001))::bigint,
  'connections', (select count(*) from pg_stat_activity where backend_type = 'client backend'),
  'maxConnections', current_setting('max_connections')::int,
  'maxWorkerProcesses', current_setting('max_worker_processes')::int,
  'maxPreparedTransactions', current_setting('max_prepared_transactions')::int,
  'maxLocksPerTransaction', current_setting('max_locks_per_transaction')::int,
  'walSendersUsed', (select count(*) from pg_stat_replication),
  'pendingConfig', (select coalesce(json_agg(distinct name), '[]') from (
      select name from pg_settings where pending_restart
      union select coalesce(name, sourcefile || ':' || sourceline) from pg_file_settings where error is not null) p),
  'indexes', (select coalesce(json_agg(json_build_object('name', name, 'table', tbl, 'provider', provider,
      'collation', coll_name, 'sizeBytes', size) order by name), '[]') from idx),
  'collationDependent', (select coalesce(json_agg(name order by name), '[]') from (
      select name from partkeys union select name from checks) x),
  'tables', (select coalesce(json_agg(json_build_object('name', name, 'rows', nrows, 'sizeBytes', size) order by name), '[]') from tbls)
);
SQL
}

# psql_in runs SQL from stdin in one database of a container (the
# container's POSTGRES_DB when empty) as its POSTGRES_USER.
psql_in() { # container database sleep
	# shellcheck disable=SC2016 # expanded inside the container
	docker exec -i "$1" sh -c 'exec psql -X -A -t -q -v sleep="$2" -U "${POSTGRES_USER:-postgres}" -d "${1:-${POSTGRES_DB:-${POSTGRES_USER:-postgres}}}"' sh "$2" "$3"
}

# db_facts prints the facts object of one postgres container, or an error:
# {"container", "facts", "others":[the same facts of each other database],
# "othersError":[...], "configWritable"}. Of "others" only indexes and
# collationDependent matter.
db_facts() { # container
	local out o err rc=0 files writable other others=() errs=()
	err=$(mktemp)
	out=$(db_sql | psql_in "$1" "" "$HC_WAL_SAMPLE" 2>"$err") || rc=$?
	if [ "$rc" -ne 0 ] || [ -z "$out" ]; then
		printf '{"container":%s,"error":%s}' "$(json_str "$1")" "$(json_str "psql exited $rc: $(head -c 300 "$err")")"
		rm -f "$err"
		return
	fi
	files=$(printf 'show hba_file;\nshow config_file;\n' | psql_in "$1" "" 0 2>/dev/null || true)
	# shellcheck disable=SC2016 # expanded inside the container
	writable=$(docker exec "$1" sh -c 'test -w "$1" && test -w "$2" && echo true || echo false' sh \
		"$(printf '%s\n' "$files" | sed -n 1p)" "$(printf '%s\n' "$files" | sed -n 2p)" 2>/dev/null || echo false)
	while IFS= read -r other; do
		[ -n "$other" ] || continue
		rc=0
		o=$(db_sql | psql_in "$1" "$other" 0 2>"$err") || rc=$?
		if [ "$rc" -eq 0 ] && [ -n "$o" ]; then
			others+=("$o")
		else
			errs+=("$(json_str "$other: psql exited $rc: $(head -c 200 "$err")")")
		fi
	done < <(printf "select datname from pg_database where not datistemplate and datallowconn and datname <> current_database() and datname !~ '[[:cntrl:]]';\n" | psql_in "$1" "" 0 2>/dev/null || true)
	printf '{"container":%s,"facts":%s,"others":[%s],"othersError":[%s],"configWritable":%s}' "$(json_str "$1")" "$out" \
		"$(join ${others[@]+"${others[@]}"})" "$(join ${errs[@]+"${errs[@]}"})" "${writable:-false}"
	rm -f "$err"
}

# encrypt reads a plaintext on stdin and prints its envelope (see the top).
encrypt() {
	# shellcheck disable=SC2016 # expanded inside the container
	docker run --rm -i --pull missing --network none --read-only --cap-drop ALL \
		--security-opt no-new-privileges --tmpfs /w:rw,mode=700,size=64m \
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

# --- what docker knows ---

docker_version=$(docker version --format '{{.Server.Version}}' 2>/dev/null || true)
compose_version=$(docker compose version --short 2>/dev/null || docker-compose version --short 2>/dev/null || true)
docker_root=$(docker info --format '{{.DockerRootDir}}' 2>/dev/null || echo /var/lib/docker)
free_kb=$(df -Pk "$docker_root" 2>/dev/null | awk 'NR==2 {print $4}')
free_bytes=$((${free_kb:-0} * 1024))

project_filter=label=com.docker.compose.project
[ -n "$HC_PROJECT" ] && project_filter=label=com.docker.compose.project=$HC_PROJECT
mapfile -t ids < <(docker ps -aq --filter "$project_filter")

container_tmpl='{"id":{{json .Id}},"name":{{json .Name}},"image":{{json .Config.Image}},"imageId":{{json .Image}},'
container_tmpl+='"project":{{json (index .Config.Labels "com.docker.compose.project")}},'
container_tmpl+='"service":{{json (index .Config.Labels "com.docker.compose.service")}},'
container_tmpl+='"workingDir":{{json (index .Config.Labels "com.docker.compose.project.working_dir")}},'
container_tmpl+='"configFiles":{{json (index .Config.Labels "com.docker.compose.project.config_files")}},'
container_tmpl+='"revision":{{json (index .Config.Labels "org.opencontainers.image.revision")}},'
container_tmpl+='"state":{{json .State.Status}},'
container_tmpl+='"startedAt":{{json .State.StartedAt}},"restart":{{json .HostConfig.RestartPolicy.Name}},'
container_tmpl+='"memory":{{.HostConfig.Memory}},"ports":{{json .NetworkSettings.Ports}},'
container_tmpl+='"networks":{{json .NetworkSettings.Networks}},'
container_tmpl+='"mounts":{{json .Mounts}}'

containers=()
pg=()
declare -A networks_seen=()
declare -A volumes_seen=()
for id in "${ids[@]}"; do
	base=$(docker container inspect --format "$container_tmpl" "$id")
	name=$(docker container inspect --format '{{.Name}}' "$id")
	name=${name#/}
	image=$(docker container inspect --format '{{.Config.Image}}' "$id")
	image_id=$(docker container inspect --format '{{.Image}}' "$id")
	state=$(docker container inspect --format '{{.State.Status}}' "$id")
	# Fields a docker CLI may leave out of its map when empty: a missing
	# one is a template error there and a nil pointer elsewhere.
	health=$(docker container inspect --format '{{json .State.Health.Status}}' "$id" 2>/dev/null) || health='""'
	exposed=$(docker container inspect --format '{{json .Config.ExposedPorts}}' "$id" 2>/dev/null) || exposed=null
	digests=$(docker image inspect --format '{{json .RepoDigests}}' "$image_id" 2>/dev/null || echo '[]')
	# Variable names, each line a JSON string "NAME=value" that is only
	# compared and cut, never printed.
	declare -A image_env=()
	while IFS= read -r line; do
		[ -n "$line" ] && image_env["$line"]=1
	done < <(docker image inspect --format '{{range .Config.Env}}{{json .}}{{println}}{{end}}' "$image_id" 2>/dev/null || true)
	envs=()
	while IFS= read -r line; do
		[ -n "$line" ] || continue
		var=${line#\"}
		case $var in
		*=*) var=${var%%=*} ;;
		*) var=${var%\"} ;; # a variable without a value
		esac
		from_image=false
		[ -n "${image_env[$line]:-}" ] && from_image=true
		envs+=("{\"name\":\"$var\",\"image\":$from_image}")
	done < <(docker container inspect --format '{{range .Config.Env}}{{json .}}{{println}}{{end}}' "$id")
	unset image_env
	containers+=("$base,\"health\":${health:-\"\"},\"exposed\":${exposed:-null},\"repoDigests\":$digests,\"env\":[$(join "${envs[@]}")]}")
	while IFS= read -r net; do
		[ -n "$net" ] && networks_seen["$net"]=1
	done < <(docker container inspect --format '{{range $k, $v := .NetworkSettings.Networks}}{{$k}}{{println}}{{end}}' "$id")
	while IFS= read -r vol; do
		[ -n "$vol" ] && volumes_seen["$vol"]=1
	done < <(docker container inspect --format '{{range .Mounts}}{{if eq .Type "volume"}}{{.Name}}{{println}}{{end}}{{end}}' "$id")
	if [ "$state" = running ] && printf '%s' "$image" | grep -Eiq 'postgres|postgis'; then
		pg+=("$name")
	fi
done

networks=()
for net in "${!networks_seen[@]}"; do
	networks+=("$(docker network inspect --format '{"name":{{json .Name}},"driver":{{json .Driver}},"ipam":{{json .IPAM.Config}},"internal":{{.Internal}}}' "$net")")
done

volumes=()
for vol in "${!volumes_seen[@]}"; do
	mp=$(docker volume inspect --format '{{.Mountpoint}}' "$vol" 2>/dev/null || true)
	size=0
	if [ -n "$mp" ] && [ -d "$mp" ]; then
		size=$(du -sk "$mp" 2>/dev/null | awk '{print $1 * 1024}')
	fi
	volumes+=("{\"name\":$(json_str "$vol"),\"sizeBytes\":${size:-0}}")
done

dbs=()
for c in "${pg[@]}"; do
	dbs+=("$(db_facts "$c")")
	# The database's disk may not be docker's: the least free of both.
	while IFS= read -r src; do
		[ -n "$src" ] && [ -e "$src" ] || continue
		kb=$(df -Pk "$src" 2>/dev/null | awk 'NR==2 {print $4}')
		if [ -n "$kb" ] && [ $((kb * 1024)) -lt "$free_bytes" ]; then
			free_bytes=$((kb * 1024))
		fi
	done < <(docker container inspect --format '{{range .Mounts}}{{.Source}}{{println}}{{end}}' "$c")
done

# The environment values, encrypted: {"<name>":["NAME=value",...]} goes
# straight from docker into the openssl container's stdin.
secrets_plain() {
	local i=0 id
	printf '{'
	for id in "${ids[@]}"; do
		[ "$i" -gt 0 ] && printf ','
		docker container inspect --format '{{json (slice .Name 1)}}:{{json .Config.Env}}' "$id"
		i=$((i + 1))
	done
	printf '}'
}
secrets=$(secrets_plain | encrypt) || die 4 "encrypting the environment values failed"
case "$secrets" in
hcenc1.*) ;;
*) die 4 "encrypting the environment values printed no envelope" ;;
esac

doc=$(printf '{"version":1,"at":"%s","docker":%s,"compose":%s,"dockerRoot":%s,"freeDiskBytes":%s,"containers":[%s],"networks":[%s],"volumes":[%s],"db":[%s],"secrets":"%s"}' \
	"$(date -u +%Y-%m-%dT%H:%M:%SZ)" "$(json_str "$docker_version")" "$(json_str "$compose_version")" \
	"$(json_str "$docker_root")" "$free_bytes" "$(join "${containers[@]}")" "$(join "${networks[@]}")" \
	"$(join "${volumes[@]}")" "$(join "${dbs[@]}")" "$secrets")

mkdir -p "$HC_WORK_DIR"
chmod 700 "$HC_WORK_DIR"
# Outputs of inspections older than an hour were never cleaned up.
find "$HC_WORK_DIR" -name '*.b64' -mmin +60 -delete 2>/dev/null || true
printf '%s' "$doc" | gzip -9 | base64 | tr -d '\n' >"$out_file"

total=$(wc -c <"$out_file" | tr -d ' ')
if [ $(((total + HC_MAX_CHUNK - 1) / HC_MAX_CHUNK)) -gt "$HC_MAX_CHUNKS" ]; then
	rm -f "$out_file"
	die 5 "the output ($total characters) needs more than $HC_MAX_CHUNKS chunks of $HC_MAX_CHUNK"
fi
print_chunk 0
