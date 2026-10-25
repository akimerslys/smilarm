#!/usr/bin/env bash
# Alarm+ installer.
#   bash install.sh              Docker install (recommended): build, start, wait until healthy. Also how you update.
#   bash install.sh --native     optional install WITHOUT Docker (systemd service + Python venv)
# Other options are passed on; see `bash install.sh --help` or `bash install.sh --native --help`.
ROOT="$(cd "$(dirname "$0")" && pwd)"
args=() native=0
for a in "$@"; do
  if [ "$a" = "--native" ]; then native=1; else args+=("$a"); fi
done
if [ "$native" = 1 ]; then exec bash "$ROOT/deploy/install.sh" "${args[@]}"; fi
exec bash "$ROOT/deploy/docker-install.sh" "${args[@]}"
