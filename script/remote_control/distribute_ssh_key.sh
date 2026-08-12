#!/usr/bin/env bash
set -euo pipefail

LOCAL_KEY="$HOME/.ssh/cloudlab_key"
REMOTE_KEY_NAME="cloudlab_key"

NODES=(
  "10.10.1.1"
  "10.10.1.2"
  "10.10.1.3"
  "10.10.1.4"
  "10.10.1.5"
  "10.10.1.6"
  "10.10.1.7"
  "10.10.1.8"
  "10.10.1.9"
  "10.10.1.10"
)

USER="wucy"

if [ ! -f "$LOCAL_KEY" ]; then
  echo "[ERROR] Local SSH key not found: $LOCAL_KEY"
  exit 1
fi

echo "[INFO] Distributing SSH key $LOCAL_KEY to all nodes..."

for HOST in "${NODES[@]}"; do
  echo "[INFO] -> $HOST"

  ssh -i "$LOCAL_KEY" -o StrictHostKeyChecking=accept-new "${USER}@${HOST}" "mkdir -p ~/.ssh" >/dev/null 2>&1

  scp -i "$LOCAL_KEY" -o StrictHostKeyChecking=accept-new \
    "$LOCAL_KEY" "${USER}@${HOST}:~/.ssh/${REMOTE_KEY_NAME}"

  ssh -i "$LOCAL_KEY" -o StrictHostKeyChecking=accept-new "${USER}@${HOST}" \
    "chmod 600 ~/.ssh/${REMOTE_KEY_NAME}"
done

echo "[INFO] SSH key distribution finished."


