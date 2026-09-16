#!/usr/bin/env bash
# Open TCP 8080 on the Oracle Ubuntu host firewall (iptables).
# The VCN / NSG ingress rule is separate and must still be added in the
# Oracle console — this script cannot do that.
set -euo pipefail

PORT="${HEALTH_HTTP_PORT:-8080}"

if [[ "$(id -u)" -ne 0 ]]; then
  echo "re-run with sudo: sudo $0"
  exit 1
fi

ensure_accept() {
  local chain_cmd=$1
  if $chain_cmd -C INPUT -p tcp --dport "${PORT}" -j ACCEPT 2>/dev/null; then
    return 0
  fi
  # Insert at the top so Oracle's trailing REJECT does not swallow the packet.
  $chain_cmd -I INPUT 1 -p tcp --dport "${PORT}" -j ACCEPT
}

ensure_accept iptables
if command -v ip6tables >/dev/null 2>&1; then
  ensure_accept ip6tables || true
fi

mkdir -p /etc/iptables
iptables-save >/etc/iptables/rules.v4
if command -v ip6tables-save >/dev/null 2>&1; then
  ip6tables-save >/etc/iptables/rules.v6 2>/dev/null || true
fi
if command -v netfilter-persistent >/dev/null 2>&1; then
  netfilter-persistent save >/dev/null 2>&1 || true
fi

echo "host firewall: TCP ${PORT} ACCEPT (persisted)"
echo "still required in Oracle Cloud: Security List AND any NSG on the VNIC"
echo "  source 0.0.0.0/0  protocol TCP  destination port ${PORT}"
echo "then: curl -sS http://127.0.0.1:${PORT}/status"
