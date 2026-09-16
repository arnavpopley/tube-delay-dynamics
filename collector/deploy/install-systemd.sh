#!/usr/bin/env bash
# Install the collector as a systemd service with Restart=always.
# Run on the always-on VM (Oracle Always Free or a VPS), not on Vercel.
set -euo pipefail

if [[ "$(id -u)" -ne 0 ]]; then
  echo "re-run with sudo: sudo $0"
  exit 1
fi

ROOT="$(cd "$(dirname "$0")/../.." && pwd)"
RUN_USER="${SUDO_USER:-${USER}}"
RUN_GROUP="$(id -gn "$RUN_USER")"
PYTHON="$(command -v python3)"
ENV_FILE="${ROOT}/.env"
DATA_DIR="${ROOT}/data"

if [[ ! -f "${ROOT}/collector/tfl_collector.py" ]]; then
  echo "cannot find collector/tfl_collector.py under ${ROOT}"
  exit 1
fi

if [[ ! -f "${ENV_FILE}" ]]; then
  echo "missing ${ENV_FILE}"
  echo "cp ${ROOT}/.env.example ${ENV_FILE} && nano ${ENV_FILE}"
  exit 1
fi

if ! grep -q '^TFL_APP_KEY=.\+' "${ENV_FILE}"; then
  echo "TFL_APP_KEY is empty in ${ENV_FILE}. The collector will run, but TfL may throttle unsigned requests."
fi

mkdir -p "${DATA_DIR}"
chown -R "${RUN_USER}:${RUN_GROUP}" "${DATA_DIR}"

cat >/etc/systemd/system/tfl-collector.service <<EOF
[Unit]
Description=TfL Tube Delay Dynamics collector
After=network-online.target
Wants=network-online.target

[Service]
Type=simple
User=${RUN_USER}
Group=${RUN_GROUP}
WorkingDirectory=${ROOT}
EnvironmentFile=-${ENV_FILE}
Environment=PYTHONUNBUFFERED=1
Environment=HEALTH_HTTP_PORT=8080
ExecStart=/usr/bin/env HEALTH_HTTP_PORT=8080 HEARTBEAT_PUSH=1 ${PYTHON} ${ROOT}/collector/tfl_collector.py
Restart=always
RestartSec=5
TimeoutStopSec=40
Nice=10

[Install]
WantedBy=multi-user.target
EOF

cat >/etc/systemd/system/tfl-collector-health.service <<EOF
[Unit]
Description=TfL collector health check
After=tfl-collector.service

[Service]
Type=oneshot
User=${RUN_USER}
Group=${RUN_GROUP}
WorkingDirectory=${ROOT}
EnvironmentFile=-${ENV_FILE}
ExecStart=${PYTHON} ${ROOT}/collector/healthcheck.py
EOF

cp "${ROOT}/collector/deploy/tfl-collector-health.timer" /etc/systemd/system/

systemctl daemon-reload
systemctl enable tfl-collector.service
# Restart so a git pull actually runs the new collector (enable --now will
# not replace an already-running process).
systemctl restart tfl-collector.service
systemctl enable --now tfl-collector-health.timer

if [[ -f "${ROOT}/collector/deploy/heartbeat_key.a" && -f "${ROOT}/collector/deploy/heartbeat_key.b" ]]; then
  ${PYTHON} - <<PY
from pathlib import Path
import base64
root = Path("${ROOT}")
blob = base64.b64decode(
    (root / "collector/deploy/heartbeat_key.a").read_text().strip()
    + (root / "collector/deploy/heartbeat_key.b").read_text().strip()
)
path = root / "collector/deploy/heartbeat_deploy_key"
path.write_bytes(blob)
path.chmod(0o600)
PY
  chown "${RUN_USER}:${RUN_GROUP}" "${ROOT}/collector/deploy/heartbeat_deploy_key"
fi
if [[ -f "${ROOT}/collector/deploy/heartbeat-ssh.sh" ]]; then
  chmod 755 "${ROOT}/collector/deploy/heartbeat-ssh.sh"
fi

# Optional: local :8080 for curl on the box. The public site no longer
# needs inbound TCP 8080; it reads the outbound GitHub heartbeat.
if [[ -x "${ROOT}/collector/deploy/open-status-port.sh" ]]; then
  bash "${ROOT}/collector/deploy/open-status-port.sh" || true
fi

echo
echo "installed. Watch logs with:"
echo "  journalctl -u tfl-collector.service -f"
echo "After a minute:"
echo "  sudo -u ${RUN_USER} ${PYTHON} ${ROOT}/collector/healthcheck.py"
echo "The Vercel card reads the outbound heartbeat, not port 8080."
echo "Raw JSONL lands in ${DATA_DIR}/raw/ — never delete it."
sleep 2
if curl -fsS --max-time 2 http://127.0.0.1:8080/status; then
  echo
else
  echo "local :8080 not up yet. Collection can still be running. Check:"
  echo "  journalctl -u tfl-collector.service -n 40 --no-pager"
fi
