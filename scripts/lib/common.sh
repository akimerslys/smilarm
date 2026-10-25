#!/usr/bin/env bash
# Shared settings and helpers of the Smilarm scripts. Sourced by install.sh, uninstall.sh and the `smilarm` command.
# It only defines things; nothing runs when it is sourced.

# ---- Publishing ------------------------------------------------------------------------------------------------------------
# Replace OWNER with the GitHub account that hosts this repository (or export SMILARM_REPO). Until then Smilarm builds its Docker image
# from the cloned sources and `smilarm update` asks you to pull a new clone; once published, the prebuilt image is pulled from
# ghcr.io (built by .github/workflows/docker.yml) and the repository tarball is used for updates and for `curl | bash` installs.
SMILARM_REPO=${SMILARM_REPO:-OWNER/smilarm}
SMILARM_IMAGE_REPO=${SMILARM_IMAGE_REPO:-ghcr.io/$(printf '%s' "$SMILARM_REPO" | tr 'A-Z' 'a-z')}
SMILARM_TAG=${SMILARM_TAG:-latest}
SMILARM_SOURCE_URL=${SMILARM_SOURCE_URL:-https://github.com/$SMILARM_REPO/archive/refs/heads/main.tar.gz}

# ---- Where things live (nothing depends on the folder you cloned) ------------------------------------------------------------
SMILARM_HOME=${SMILARM_HOME:-$HOME/.smilarm}      # compose files, the `smilarm` command, helper scripts; native: app, venv, config, data
SMILARM_BIN_DIR=${SMILARM_BIN_DIR:-$HOME/.local/bin}
SMILARM_UNIT=${SMILARM_UNIT:-/etc/systemd/system/smilarm.service}
LOCAL_IMAGE=smilarm:local
CONTAINER=smilarm
VOLUME=smilarm-state
# names used before the project was called Smilarm (its data is moved over automatically)
LEGACY_CONTAINER=${SMILARM_LEGACY_CONTAINER:-alarmplus}
LEGACY_VOLUME=${SMILARM_LEGACY_VOLUME:-alarmplus-state}
LEGACY_UNIT=${SMILARM_LEGACY_UNIT:-/etc/systemd/system/alarmplus.service}

is_published() { case "$SMILARM_REPO" in OWNER/*) return 1 ;; *) return 0 ;; esac; }

port_busy() { (exec 3<>"/dev/tcp/127.0.0.1/$1") 2>/dev/null; }

# detect_dc: how to call docker (plain, or through sudo when the user is not in the docker group). Run it in the MAIN shell: steps run
# in background subshells, whose variable changes would be lost.
DC=${DC:-docker}
detect_dc() {
  if docker info >/dev/null 2>&1; then DC=docker
  elif command -v docker >/dev/null 2>&1 && { sudo -n docker info >/dev/null 2>&1 || { [ -t 0 ] && sudo docker info >/dev/null 2>&1; }; }; then DC="sudo docker"
  fi
}

# fetch_source DIR: download the repository tarball and unpack it into DIR (for updates and curl|bash installs)
fetch_source() {
  mkdir -p "$1" && curl -fsSL "$SMILARM_SOURCE_URL" | tar -xz -C "$1" --strip-components=1
}

# install_home SRC_ROOT MODE: copy what the commands need so that the source folder can be deleted afterwards.
install_home() {
  local src=$1 mode=$2
  mkdir -p "$SMILARM_HOME"/bin "$SMILARM_HOME"/lib "$SMILARM_HOME"/compose "$SMILARM_HOME"/share "$SMILARM_BIN_DIR"
  cp "$src"/scripts/lib/ui.sh "$src"/scripts/lib/common.sh "$SMILARM_HOME/lib/"
  cp "$src"/scripts/uninstall.sh "$SMILARM_HOME/lib/"
  cp "$src"/scripts/smilarm "$SMILARM_HOME/bin/smilarm" && chmod +x "$SMILARM_HOME/bin/smilarm"
  cp "$src"/docker-compose.yml "$src"/docker-compose.sound.yml "$SMILARM_HOME/compose/"
  cp "$src"/openclaw/skills/smilarm/SKILL.md "$src"/config.example.toml "$SMILARM_HOME/share/"
  printf '%s\n' "$mode" >"$SMILARM_HOME/mode"
  { echo "$(cat "$src/VERSION" 2>/dev/null || echo dev)$(git -C "$src" rev-parse --short HEAD 2>/dev/null | sed "s/^/+/")"; } >"$SMILARM_HOME/version"
  ln -sf "$SMILARM_HOME/bin/smilarm" "$SMILARM_BIN_DIR/smilarm"
}

# set_env_var FILE KEY VALUE: add or replace KEY=VALUE, keeping every other line
set_env_var() {
  { grep -v "^$2=" "$1" 2>/dev/null; echo "$2=$3"; } >"$1.tmp" && mv "$1.tmp" "$1"
}

in_path() { case ":$PATH:" in *":$SMILARM_BIN_DIR:"*) return 0 ;; *) return 1 ;; esac; }
