#!/bin/bash
set -euo pipefail
export DEBIAN_FRONTEND=noninteractive

# Prevent package post-install scripts from starting an unguarded daemon.
if ! command -v docker >/dev/null 2>&1; then
  systemctl mask docker.service docker.socket
  apt-get update
  apt-get install -y --no-install-recommends docker.io docker-compose-v2 iptables
fi
install -d -m 0755 /usr/local/sbin /etc/systemd/system/docker.service.d
cat > /usr/local/sbin/rin-metadata-guard <<'GUARD'
#!/bin/bash
set -euo pipefail
# Keep metadata DNS (UDP/TCP 53) available. Block HTTP and HTTPS from bridges.
iptables -w -N DOCKER-USER 2>/dev/null || iptables -w -L DOCKER-USER -n >/dev/null
iptables -w -C DOCKER-USER -d 169.254.169.254/32 -p tcp -m multiport --dports 80,443 -j REJECT 2>/dev/null ||
  iptables -w -I DOCKER-USER 1 -d 169.254.169.254/32 -p tcp -m multiport --dports 80,443 -j REJECT
iptables -w -C FORWARD -j DOCKER-USER 2>/dev/null || iptables -w -I FORWARD 1 -j DOCKER-USER
GUARD
chmod 0755 /usr/local/sbin/rin-metadata-guard
cat > /etc/systemd/system/rin-metadata-guard.service <<'UNIT'
[Unit]
Description=Block container access to GCE metadata HTTP before Docker starts
Before=docker.service
[Service]
Type=oneshot
ExecStart=/usr/local/sbin/rin-metadata-guard
RemainAfterExit=yes
[Install]
WantedBy=multi-user.target
UNIT
cat > /etc/systemd/system/docker.service.d/rin-metadata-guard.conf <<'UNIT'
[Unit]
Requires=rin-metadata-guard.service
After=rin-metadata-guard.service
UNIT
systemctl daemon-reload
systemctl enable rin-metadata-guard.service
systemctl start rin-metadata-guard.service
systemctl unmask docker.service docker.socket
systemctl enable --now docker.service
# Data restoration and application creation are an explicitly approved step.
