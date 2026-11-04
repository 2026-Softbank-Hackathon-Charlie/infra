#!/usr/bin/env bash
# Runs as root on the runner host (Ubuntu 24.04) through SSM Run Command; up.py
# sends it with the inputs below (ADR 0042). Safe to run again: it installs what
# is missing and leaves runners that are already configured alone.
#
#   RUNNER_TOKEN    org registration token (never printed)
#   RUNNER_VERSION  actions/runner version, e.g. 2.338.2
#   RUNNER_SHA256   sha256 of actions-runner-linux-x64-<version>.tar.gz
#   RUNNER_ORG_URL  https://github.com/<org>
#   RUNNER_LABEL    extra label, default hopcharlie
#   RUNNER_COUNT    runners on this host, default 3
#
# Detailed output goes to /var/log/github-runner-bootstrap.log; stdout only gets
# the steps, a summary and, on failure, the end of that log.
set -euo pipefail
: "${RUNNER_TOKEN:?}" "${RUNNER_VERSION:?}" "${RUNNER_SHA256:?}" "${RUNNER_ORG_URL:?}"
label="${RUNNER_LABEL:-hopcharlie}"
count="${RUNNER_COUNT:-3}"
base=/opt/actions-runner
export DEBIAN_FRONTEND=noninteractive HOME=/root
export PATH=/usr/local/sbin:/usr/local/bin:/usr/sbin:/usr/bin:/sbin:/bin:/snap/bin

log=/var/log/github-runner-bootstrap.log
exec 3>&1 >>"$log" 2>&1
on_exit() {
  local rc=$?
  [ "$rc" -eq 0 ] || { echo "bootstrap failed ($rc); end of $log:" >&3; tail -n 40 "$log" >&3; }
}
trap on_exit EXIT
step() { echo "== $*" >&3; echo "== $(date -Is) $*"; }
# --no-upgrade: a re-run while jobs run must not upgrade docker.io and restart dockerd.
apt_install() { apt-get install -y -q --no-upgrade "$@"; }

step 'wait for cloud-init'
cloud-init status --wait || true

# apt (also in the runner's installdependencies.sh) waits for the lock instead of failing,
# and no unattended upgrade restarts dockerd under a running job.
echo 'DPkg::Lock::Timeout "600";' > /etc/apt/apt.conf.d/90github-runner
systemctl disable --now apt-daily.timer apt-daily-upgrade.timer

# 16 GiB for three parallel jobs (go test -race, PostgreSQL, terraform): swap instead of the OOM killer.
if [ -z "$(swapon --show=NAME --noheadings)" ]; then
  step 'swap 8 GiB'
  if [ ! -f /swapfile ]; then
    fallocate -l 8G /swapfile
    chmod 600 /swapfile
    mkswap /swapfile
  fi
  swapon /swapfile
  grep -q '^/swapfile ' /etc/fstab || echo '/swapfile none swap sw 0 0' >> /etc/fstab
fi

# GitHub-hosted runners push plain image manifests (classic image store). Docker 29
# would use the containerd store on a fresh install, so set this before it starts.
if [ ! -f /etc/docker/daemon.json ]; then
  mkdir -p /etc/docker
  echo '{"features": {"containerd-snapshotter": false}}' > /etc/docker/daemon.json
fi

# Tools the workflows take from the GitHub-hosted image; setup-* actions do not install them.
step 'apt packages'
apt-get update -q
apt_install git curl ca-certificates jq unzip zip zstd xz-utils python3 python3-venv python3-pip \
  build-essential iptables docker.io docker-buildx

if [ ! -f /etc/apt/sources.list.d/github-cli.list ]; then
  step 'gh apt repository'
  install -d -m 755 /etc/apt/keyrings
  curl -fsSL https://cli.github.com/packages/githubcli-archive-keyring.gpg -o /etc/apt/keyrings/githubcli-archive-keyring.gpg
  chmod go+r /etc/apt/keyrings/githubcli-archive-keyring.gpg
  echo "deb [arch=$(dpkg --print-architecture) signed-by=/etc/apt/keyrings/githubcli-archive-keyring.gpg] https://cli.github.com/packages stable main" \
    > /etc/apt/sources.list.d/github-cli.list
  apt-get update -q
fi
apt_install gh

if ! command -v aws >/dev/null; then
  step 'AWS CLI v2'
  tmp="$(mktemp -d)"
  curl -fsSL https://awscli.amazonaws.com/awscli-exe-linux-x86_64.zip -o "$tmp/awscliv2.zip"
  unzip -q "$tmp/awscliv2.zip" -d "$tmp"
  "$tmp/aws/install"
  rm -rf "$tmp"
fi

# Jobs run as runner and need docker. No workflow uses sudo, so runner gets none.
step 'user runner'
id runner >/dev/null 2>&1 || useradd --create-home --shell /bin/bash runner
usermod -aG docker runner

# IMDS hop limit 1 keeps containers away from the instance role, but steps run on
# the host as runner. Reject their metadata requests too; root (SSM agent) keeps it.
step 'block instance metadata for runner'
cat > /etc/systemd/system/github-runner-imds-block.service <<'UNIT'
[Unit]
Description=Reject instance metadata requests from the runner user
After=network-pre.target
Before=network.target

[Service]
Type=oneshot
RemainAfterExit=yes
ExecStart=/bin/sh -c 'iptables -C OUTPUT -d 169.254.169.254/32 -m owner --uid-owner runner -j REJECT 2>/dev/null || iptables -I OUTPUT -d 169.254.169.254/32 -m owner --uid-owner runner -j REJECT'

[Install]
WantedBy=multi-user.target
UNIT

# Jobs leave images, build cache and volumes behind; prune them daily at 04:00 KST.
# docker system prune cannot combine --volumes with an until filter.
step 'daily docker prune'
cat > /etc/systemd/system/github-runner-docker-prune.service <<'UNIT'
[Unit]
Description=Prune Docker data that jobs left behind
Requires=docker.service
After=docker.service

[Service]
Type=oneshot
ExecStart=/usr/bin/docker system prune -af --filter until=24h
ExecStart=/usr/bin/docker volume prune -af
UNIT
cat > /etc/systemd/system/github-runner-docker-prune.timer <<'UNIT'
[Unit]
Description=Daily Docker prune on the GitHub runner host

[Timer]
OnCalendar=*-*-* 19:00:00 UTC
Persistent=true

[Install]
WantedBy=timers.target
UNIT
systemctl daemon-reload
systemctl enable --now github-runner-imds-block.service github-runner-docker-prune.timer

tarball="$base/actions-runner-linux-x64-$RUNNER_VERSION.tar.gz"
deps_installed=no
for n in $(seq 1 "$count"); do
  dir="$base/$n"
  name="hopcharlie-runner-$n"
  if [ -f "$dir/.runner" ]; then
    step "$name: already configured"
  else
    step "$name: install actions/runner $RUNNER_VERSION and register"
    if [ ! -f "$tarball" ]; then
      mkdir -p "$base"
      curl -fsSL -o "$tarball.part" \
        "https://github.com/actions/runner/releases/download/v$RUNNER_VERSION/actions-runner-linux-x64-$RUNNER_VERSION.tar.gz"
      echo "$RUNNER_SHA256  $tarball.part" | sha256sum --check --quiet - || { rm -f "$tarball.part"; echo "sha256 mismatch" >&3; exit 1; }
      mv "$tarball.part" "$tarball"
    fi
    mkdir -p "$dir"
    tar -xzf "$tarball" -C "$dir"
    chown -R runner:runner "$dir"
    if [ "$deps_installed" = no ]; then
      "$dir/bin/installdependencies.sh"
      deps_installed=yes
    fi
    # The runner reads the token from ACTIONS_RUNNER_INPUT_TOKEN: it stays off the process list.
    (cd "$dir" && ACTIONS_RUNNER_INPUT_TOKEN="$RUNNER_TOKEN" runuser -u runner -- ./config.sh --unattended \
      --url "$RUNNER_ORG_URL" --name "$name" --labels "$label" --runnergroup Default --work _work --replace)
  fi
  [ -f "$dir/.service" ] || (cd "$dir" && ./svc.sh install runner)
  (cd "$dir" && ./svc.sh start)
done

step 'summary'
{
  docker --version
  docker buildx version
  aws --version
  gh --version | head -n 1
  git --version
  python3 --version
  jq --version
  df -h / | tail -n 1
  for n in $(seq 1 "$count"); do
    printf 'hopcharlie-runner-%s: %s\n' "$n" "$(systemctl is-active "$(cat "$base/$n/.service")")"
  done
} >&3
