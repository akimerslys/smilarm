#!/usr/bin/env bash
# Smilarm uninstaller. Asks before it deletes anything you may want to keep (your config and alarms).
#
#   bash uninstall.sh             interactive
#   bash uninstall.sh --dry-run   show what would happen, change nothing
#
# More options (--yes, --purge, --no-backup, --docker, --native, --openclaw, --packages, --plain): see docs/install.md.
# This works without the folder you cloned too: `smilarm uninstall` runs the same script from ~/.smilarm.
set -uo pipefail
SELF="${BASH_SOURCE[0]:-}"
HERE=""
[ -n "$SELF" ] && [ -f "$SELF" ] && HERE="$(cd "$(dirname "$SELF")" && pwd)"

if [ -n "$HERE" ] && [ -f "$HERE/scripts/uninstall.sh" ]; then exec bash "$HERE/scripts/uninstall.sh" "$@"; fi
INSTALLED="${SMILARM_HOME:-$HOME/.smilarm}/lib/uninstall.sh"
if [ -f "$INSTALLED" ]; then exec bash "$INSTALLED" "$@"; fi
echo "Nothing to uninstall: ${SMILARM_HOME:-$HOME/.smilarm} does not exist and this is not a Smilarm checkout."
echo "If Smilarm runs in Docker: docker rm -f smilarm; docker image rm smilarm:local; docker volume rm smilarm-state"
exit 0
