#!/bin/bash
# Tailscale subnet router for the home LAN (2026-11-22).
# Runs on nuc8-2, on the host (not in swarm: swarm can't pass /dev/net/tun, and this must
# survive Docker failing to start). Idempotent; safe to re-run.
#
# Usage (on the node, as root):  sudo ./setup.sh
#   or from a workstation:       ssh nuc8-2 'sudo bash -s' < tailscale/setup.sh
#
# Forwarding is default-deny through ufw (routed: deny); only Tailscale and Docker traffic gets ACCEPTed.
#
# It does NOT sign in. Afterwards run (opens/prints the login link):
#   sudo tailscale up --hostname=nuc8-2 --advertise-routes=192.168.0.0/24
# then approve the route + disable key expiry in the admin console (see README.md).

set -euo pipefail

ROUTES="192.168.0.0/24"
LAN_IF="eno1"

if [[ $EUID -ne 0 ]]; then
    echo "Error: run as root (sudo)"; exit 1
fi
. /etc/os-release

echo "=== 1. Tailscale package (official apt repo, ${VERSION_CODENAME}) ==="
if ! command -v tailscale >/dev/null; then
    curl -fsSL "https://pkgs.tailscale.com/stable/ubuntu/${VERSION_CODENAME}.noarmor.gpg" \
        -o /usr/share/keyrings/tailscale-archive-keyring.gpg
    curl -fsSL "https://pkgs.tailscale.com/stable/ubuntu/${VERSION_CODENAME}.tailscale-keyring.list" \
        -o /etc/apt/sources.list.d/tailscale.list
    apt-get update -q
    apt-get install -y -q tailscale
fi
systemctl enable --now tailscaled
tailscale version | head -1

echo "=== 2. IPv4 forwarding, persistent (Docker only sets it at runtime) ==="
cat > /etc/sysctl.d/99-tailscale.conf <<'EOF'
# Tailscale subnet router (see smarthomeserver/tailscale/README.md).
# Docker also enables this when it starts; this keeps routing working if Docker doesn't.
# IPv6 forwarding deliberately left off: the LAN has no IPv6 and only an IPv4 route is advertised.
net.ipv4.ip_forward = 1
EOF
sysctl -p /etc/sysctl.d/99-tailscale.conf

echo "=== 3. UDP GRO forwarding on ${LAN_IF} (Tailscale's subnet-router throughput tuning) ==="
cat > /etc/systemd/system/tailscale-gro.service <<EOF
[Unit]
Description=UDP GRO forwarding on ${LAN_IF} for the Tailscale subnet router
After=network-online.target
Wants=network-online.target

[Service]
Type=oneshot
ExecStart=/usr/sbin/ethtool -K ${LAN_IF} rx-udp-gro-forwarding on rx-gro-list off
RemainAfterExit=yes

[Install]
WantedBy=multi-user.target
EOF
systemctl daemon-reload
systemctl enable --now tailscale-gro.service
systemctl restart tailscale-gro.service
ethtool -k "${LAN_IF}" | grep -E "rx-udp-gro-forwarding|rx-gro-list"

echo "=== 4. Default-deny forwarding via ufw (Tailscale KB 1019 / Docker packet-filtering docs) ==="
# Docker (iptables backend) only sets FORWARD to DROP when it turns ip_forward on itself. Step 2
# turns it on earlier in boot, so the host firewall has to deny routed traffic. Tailscale's
# ts-forward and Docker's DOCKER-FORWARD accept their own traffic ahead of ufw's default.
# Incoming/outgoing stay allow (unchanged behaviour; nuc8-2 never filtered INPUT).
apt-get install -y -q ufw
ufw default allow incoming
ufw default allow outgoing
ufw default deny routed
ufw --force enable
ufw status verbose | sed -n 1,4p
iptables -S FORWARD | head -1

echo "=== Done. Not signed in yet. Next: ==="
echo "  sudo tailscale up --hostname=nuc8-2 --advertise-routes=${ROUTES}"
