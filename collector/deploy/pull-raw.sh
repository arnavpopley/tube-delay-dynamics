#!/usr/bin/env bash
# Pull append-only raw JSONL off the always-on host onto this machine.
# Never deletes remote files. Safe to re-run; rsync only copies what's new.
set -euo pipefail

ROOT="$(cd "$(dirname "$0")/../.." && pwd)"
DEST="${ROOT}/data/raw"
mkdir -p "${DEST}"

usage() {
  cat <<'EOF'
Pull data/raw from the 24/7 collector host. Remote files are not deleted.

  collector/deploy/pull-raw.sh vps user@host:/opt/tube-delay-dynamics/data/raw/ [ssh-key]
  SSH_IDENTITY=~/.ssh/tfl-collector.key collector/deploy/pull-raw.sh vps user@host:/opt/tube-delay-dynamics/data/raw/
  collector/deploy/pull-raw.sh fly  [app-name]

Then: python src/ingest.py all
EOF
}

cmd="${1:-}"
case "${cmd}" in
  vps)
    src="${2:-}"
    if [[ -z "${src}" ]]; then
      usage
      exit 2
    fi
    identity="${3:-${SSH_IDENTITY:-}}"
    if [[ -n "${identity}" ]]; then
      rsync -avz --progress -e "ssh -i ${identity} -o IdentitiesOnly=yes" \
        "${src%/}/" "${DEST}/"
    else
      rsync -avz --progress "${src%/}/" "${DEST}/"
    fi
    ;;
  fly)
    app="${2:-tube-delay-collector}"
    # Stream a tarball of /app/data/raw from the Machine. fly ssh is interactive
    # on some setups; this uses a one-shot console command.
    tmp="$(mktemp -t tfl-raw.XXXXXX.tar)"
    fly ssh console --app "${app}" -C "tar -C /app/data -cf - raw" > "${tmp}"
    tar -xf "${tmp}" -C "${ROOT}/data"
    rm -f "${tmp}"
    ;;
  *)
    usage
    exit 2
    ;;
esac

echo "raw is at ${DEST}"
