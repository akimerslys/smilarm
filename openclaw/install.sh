#!/usr/bin/env bash
# Install the alarm-plus skill into an OpenClaw workspace (default ~/.openclaw/workspace).
set -euo pipefail
WS="${OPENCLAW_WORKSPACE:-$HOME/.openclaw/workspace}"
HERE="$(cd "$(dirname "$0")" && pwd)"
mkdir -p "$WS/skills"
cp -r "$HERE/skills/alarm-plus" "$WS/skills/"
echo "Installed to $WS/skills/alarm-plus. Set ALARM_URL (e.g. http://<pi>:8080) and, if the server has a token, ALARM_TOKEN in the OpenClaw environment."
