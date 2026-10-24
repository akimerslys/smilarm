#!/usr/bin/env bash
# Install the alarm-plus skill into an OpenClaw workspace (default ~/.openclaw/workspace).
set -euo pipefail
WS="${OPENCLAW_WORKSPACE:-$HOME/.openclaw/workspace}"
HERE="$(cd "$(dirname "$0")" && pwd)"
mkdir -p "$WS/skills"
cp -r "$HERE/skills/alarm-plus" "$WS/skills/"
echo "Installed to $WS/skills/alarm-plus. Edit BASE URL in SKILL.md if the Pi isn't rpi5.local."
