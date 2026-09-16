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
ExecStart=${PYTHON} ${ROOT}/collector/tfl_collector.py
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
systemctl enable --now tfl-collector.service
systemctl enable --now tfl-collector-health.timer

echo
echo "installed. Watch logs with:"
echo "  journalctl -u tfl-collector.service -f"
echo "After a minute:"
echo "  sudo -u ${RUN_USER} ${PYTHON} ${ROOT}/collector/healthcheck.py"
echo "Raw JSONL lands in ${DATA_DIR}/raw/ — never delete it."
