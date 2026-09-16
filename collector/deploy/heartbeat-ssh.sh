#!/bin/sh
# SSH wrapper for publishing status.json to the heartbeat repo.
# Uses the write-only deploy key. Does not touch data/raw/.
DIR=$(CDPATH= cd -- "$(dirname -- "$0")" && pwd)
exec ssh -i "$DIR/heartbeat_deploy_key" \
  -o IdentitiesOnly=yes \
  -o UserKnownHostsFile="$DIR/github_known_hosts" \
  -o StrictHostKeyChecking=yes \
  -o BatchMode=yes \
  "$@"
