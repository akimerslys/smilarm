#!/usr/bin/env bash
# Smilarm installer: the smile-to-dismiss alarm clock.
#
#   bash install.sh             recommended: Docker (prebuilt image, or built from this clone)
#   bash install.sh --native    optional: without Docker (systemd service + Python venv)
#
# More options (they go to the installer you chose; see docs/install.md):
#   --plain --demo --no-start --build --clean-cache --clean-only --install-docker --no-migrate   (Docker)
#   --plain --demo --no-edit --clean-cache                                                       (native)
#
# From the web, once the project is published (see SMILARM_REPO in scripts/lib/common.sh):
#   curl -fsSL https://raw.githubusercontent.com/OWNER/smilarm/main/install.sh | bash
set -uo pipefail

SELF="${BASH_SOURCE[0]:-}"
HERE=""
[ -n "$SELF" ] && [ -f "$SELF" ] && HERE="$(cd "$(dirname "$SELF")" && pwd)"

# Bootstrap: started without the repository next to it (`curl | bash`, or this file copied alone) -> download the sources first.
if [ -z "$HERE" ] || [ ! -f "$HERE/scripts/lib/common.sh" ]; then
  REPO=${SMILARM_REPO:-OWNER/smilarm}
  case "$REPO" in
    OWNER/*)
      echo "This installer needs the Smilarm sources next to it. Clone the repository and run it from there:"
      echo "  git clone <repository-url> smilarm && cd smilarm && bash install.sh"
      exit 1 ;;
  esac
  for t in curl tar; do command -v "$t" >/dev/null 2>&1 || { echo "$t is required"; exit 1; }; done
  tmp=$(mktemp -d) || exit 1
  echo "Downloading Smilarm from github.com/$REPO ..."
  curl -fsSL "${SMILARM_SOURCE_URL:-https://github.com/$REPO/archive/refs/heads/main.tar.gz}" | tar -xz -C "$tmp" --strip-components=1 || { echo "download failed"; rm -rf "$tmp"; exit 1; }
  SMILARM_REPO="$REPO" bash "$tmp/install.sh" "$@"
  rc=$?
  rm -rf "$tmp"
  exit $rc
fi

args=() native=0
for a in "$@"; do
  if [ "$a" = "--native" ]; then native=1; else args+=("$a"); fi
done
if [ "$native" = 1 ]; then exec bash "$HERE/scripts/install-native.sh" "${args[@]}"; fi
exec bash "$HERE/scripts/install-docker.sh" "${args[@]}"
