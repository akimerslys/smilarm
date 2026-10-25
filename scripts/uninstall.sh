#!/usr/bin/env bash
# Remove Smilarm. Safe by default: nothing you may want to keep (config, alarms) is deleted unless you say so. Normally run through
# `bash uninstall.sh` or `smilarm uninstall`; it works even after the folder you cloned has been deleted.
#
#   bash uninstall.sh                 interactive: finds the Docker and/or native install and removes it
#   bash uninstall.sh --yes           no questions: remove the program, KEEP your data (config, alarms)
#   bash uninstall.sh --purge         also delete your data; a backup tarball is written to your home folder first
#   bash uninstall.sh --no-backup     with --purge: skip the backup
#   bash uninstall.sh --docker        only the Docker install          (--native: only the native one)
#   bash uninstall.sh --openclaw      also remove the OpenClaw skill   (--packages: native only, also apt-remove mpv & co)
#   bash uninstall.sh --dry-run       show what would happen, change nothing
#   bash uninstall.sh --plain         no animation
set -uo pipefail
HERE="$(cd "$(dirname "${BASH_SOURCE[0]:-$0}")" && pwd)"
LIB="$HERE"; [ -f "$HERE/lib/ui.sh" ] && LIB="$HERE/lib"
# shellcheck source=scripts/lib/common.sh
. "$LIB/common.sh"

# This file may live inside the directory it is about to delete: run a copy from a temp dir instead.
case "$HERE" in
  "$SMILARM_HOME"/*)
    if [ -z "${SMILARM_UNINSTALL_COPY:-}" ]; then
      tmp=$(mktemp -d) && cp "$HERE"/*.sh "$tmp"/ && SMILARM_UNINSTALL_COPY=1 exec bash "$tmp/uninstall.sh" "$@"
    fi ;;
esac
# shellcheck source=scripts/lib/ui.sh
. "$LIB/ui.sh"

YES=0 PURGE=0 BACKUP=1 DRY=0 PLAIN=0 ONLY="" OPENCLAW=0 PACKAGES=0
for a in "$@"; do
  case "$a" in
    --yes | -y) YES=1 ;;
    --purge) PURGE=1 ;;
    --no-backup) BACKUP=0 ;;
    --dry-run) DRY=1 ;;
    --plain) PLAIN=1 ;;
    --docker) ONLY=docker ;;
    --native) ONLY=native ;;
    --openclaw) OPENCLAW=1 ;;
    --packages) PACKAGES=1 ;;
    -h | --help) sed -n '2,13p' "$0"; exit 0 ;;
    *) echo "unknown option: $a (see --help)"; exit 2 ;;
  esac
done

ui_init "${TMPDIR:-/tmp}/smilarm-uninstall.log" "$PLAIN" # not inside the home: it may be deleted
interactive=0
{ [ "$YES" = 0 ] && [ "$DRY" = 0 ] && [ -t 0 ] && [ -t 1 ]; } && interactive=1
ask() { # ask QUESTION DEFAULT(y|n): returns 0 for yes. Non-interactive runs take the default.
  [ "$interactive" = 1 ] || { [ "$2" = y ]; return; }
  local ans hint="[y/N]"; [ "$2" = y ] && hint="[Y/n]"
  read -r -p "  $1 $hint " ans; ans=${ans:-$2}
  case "$ans" in [yY]*) return 0 ;; *) return 1 ;; esac
}
act() { if [ "$DRY" = 1 ]; then echo "[dry-run] $*"; else "$@"; fi; }
DRYTAG=""; [ "$DRY" = 1 ] && DRYTAG="(dry run, not done) "

# ---------------------------------------------------------------- what is installed
detect_dc
docker_up=0; $DC info >/dev/null 2>&1 && docker_up=1
ENVF="$SMILARM_HOME/compose/.env"
IMAGES=("$LOCAL_IMAGE")
if [ -f "$ENVF" ]; then ref=$(grep '^SMILARM_IMAGE=' "$ENVF" | cut -d= -f2-); [ -n "$ref" ] && [ "$ref" != "$LOCAL_IMAGE" ] && IMAGES+=("$ref"); fi

has_container=0 has_volume=0 has_image=0 legacy_volume=0 legacy_container=0
if [ "$docker_up" = 1 ]; then
  $DC ps -a --format '{{.Names}}' 2>/dev/null | grep -qx "$CONTAINER" && has_container=1
  $DC volume ls -q 2>/dev/null | grep -qx "$VOLUME" && has_volume=1
  for i in "${IMAGES[@]}"; do $DC image inspect "$i" >/dev/null 2>&1 && has_image=1; done
  $DC volume ls -q 2>/dev/null | grep -qx "$LEGACY_VOLUME" && legacy_volume=1
  $DC ps -a --format '{{.Names}}' 2>/dev/null | grep -qx "$LEGACY_CONTAINER" && legacy_container=1
fi
found_docker=0; { [ $has_container = 1 ] || [ $has_volume = 1 ] || [ $has_image = 1 ]; } && found_docker=1
found_native=0; { [ -f "$SMILARM_UNIT" ] || [ -d "$SMILARM_HOME/venv" ]; } && found_native=1
[ "$ONLY" = docker ] && found_native=0 && found_docker=$([ "$docker_up" = 1 ] && echo 1 || echo 0)
[ "$ONLY" = native ] && found_docker=0 && found_native=1

ui_title "Smilarm uninstaller"
[ "$DRY" = 1 ] && printf '%s(dry run: nothing will be changed)%s\n' "$C_DIM" "$C_OFF"
if [ "$found_docker" = 0 ] && [ "$found_native" = 0 ]; then
  printf '\nNo Smilarm installation found (no container, volume or image; no service or Python environment).\n'
  [ "$legacy_volume" = 1 ] && printf 'An old "Alarm+" volume exists: %s. Remove it with: docker volume rm %s\n' "$LEGACY_VOLUME" "$LEGACY_VOLUME"
  [ -d "$SMILARM_HOME" ] && printf 'Leftover files in %s can be deleted by hand.\n' "$SMILARM_HOME"
  exit 0
fi
if [ "$found_docker" = 1 ] && [ "$found_native" = 1 ] && [ -z "$ONLY" ]; then
  # Both exist (e.g. a native install you are replacing with Docker). Never remove one because the other was asked for.
  echo "Found BOTH a Docker install and a native install of Smilarm."
  if [ "$DRY" = 0 ]; then
    if [ "$interactive" = 1 ]; then
      read -r -p "  Remove both? [y/N] " ans
      case "${ans:-N}" in [yY]*) ;; *) echo "Choose one: bash uninstall.sh --docker   or   bash uninstall.sh --native"; exit 2 ;; esac
    else
      echo "Say which one to remove: bash uninstall.sh --docker   or   bash uninstall.sh --native   (nothing was changed)"; exit 2
    fi
  fi
fi

printf '\nFound:\n'
if [ "$found_docker" = 1 ]; then
  printf '  %-16s %s\n' "container" "$([ $has_container = 1 ] && echo "$CONTAINER" || echo "none")"
  printf '  %-16s %s\n' "image" "$([ $has_image = 1 ] && echo "${IMAGES[*]}" || echo "none")"
  printf '  %-16s %s\n' "data volume" "$([ $has_volume = 1 ] && echo "$VOLUME (config, alarms, logs)" || echo "none")"
fi
if [ "$found_native" = 1 ]; then
  n_alarms=0; [ -f "$SMILARM_HOME/data/alarms.json" ] && n_alarms=$(grep -o '"id"' "$SMILARM_HOME/data/alarms.json" 2>/dev/null | wc -l | tr -d ' ')
  printf '  %-16s %s\n' "systemd service" "$([ -f "$SMILARM_UNIT" ] && echo "$SMILARM_UNIT" || echo "not installed")"
  printf '  %-16s %s\n' "app + Python env" "$([ -d "$SMILARM_HOME/venv" ] && echo "$SMILARM_HOME ($(ui_hsize "$(du -sk "$SMILARM_HOME/venv" | awk '{printf "%d", $1*1000}')"))" || echo "none")"
  printf '  %-16s %s\n' "your data" "$([ -f "$SMILARM_HOME/config.toml" ] && echo "config.toml, $n_alarms alarm(s)" || echo "none")"
fi
SKILL_DIR="${OPENCLAW_WORKSPACE:-$HOME/.openclaw/workspace}/skills/smilarm"
printf '  %-16s %s\n' "OpenClaw skill" "$([ -d "$SKILL_DIR" ] && echo "$SKILL_DIR" || echo "not installed")"
printf '\n'

# ---------------------------------------------------------------- decisions (all questions up front)
have_data=0
[ "$has_volume" = 1 ] && [ "$found_docker" = 1 ] && have_data=1
[ "$found_native" = 1 ] && { [ -f "$SMILARM_HOME/config.toml" ] || [ -d "$SMILARM_HOME/data" ]; } && have_data=1
DO_DATA=$PURGE DO_SKILL=$OPENCLAW DO_PKGS=$PACKAGES
if [ "$interactive" = 1 ]; then
  [ "$PURGE" = 0 ] && [ "$have_data" = 1 ] && { ask "Also delete your config and alarms? (a backup is saved to your home folder first)" n && DO_DATA=1; }
  [ "$OPENCLAW" = 0 ] && [ -d "$SKILL_DIR" ] && { ask "Remove the OpenClaw skill ($SKILL_DIR)?" n && DO_SKILL=1; }
  [ "$PACKAGES" = 0 ] && [ "$found_native" = 1 ] && { ask "Also remove the system packages the installer added (mpv and a few libraries)? Other programs might use them." n && DO_PKGS=1; }
  echo
fi
[ "$have_data" = 1 ] || DO_DATA=0
[ -d "$SKILL_DIR" ] || DO_SKILL=0
[ "$found_native" = 1 ] || DO_PKGS=0
APT_PKGS=(mpv libgles2 libegl1 python3-venv) # nano and libgl1 are left alone: most systems already had them

steps=()
[ "$DO_DATA" = 1 ] && [ "$BACKUP" = 1 ] && steps+=("Back up your data")
[ "$found_docker" = 1 ] && { [ $has_container = 1 ] || [ $has_image = 1 ]; } && steps+=("Remove the container and image")
[ "$found_native" = 1 ] && [ -f "$SMILARM_UNIT" ] && steps+=("Stop and remove the service")
[ "$found_native" = 1 ] && [ -d "$SMILARM_HOME/venv" ] && steps+=("Remove the app and Python environment")
[ "$DO_DATA" = 1 ] && steps+=("Delete your data and config")
[ "$DO_SKILL" = 1 ] && steps+=("Remove the OpenClaw skill")
[ "$DO_PKGS" = 1 ] && steps+=("Remove system packages")
[ -d "$SMILARM_HOME" ] && steps+=("Remove the smilarm command")
printf 'Will do:\n'; printf '  - %s\n' "${steps[@]}"
[ "$DO_DATA" = 1 ] && [ "$BACKUP" = 1 ] && printf '    (backup first: ~/smilarm-backup-<date>.tar.gz)\n'
[ "$DO_DATA" = 0 ] && [ "$have_data" = 1 ] && printf '    (your config and alarms are kept)\n'
echo
[ "$interactive" = 1 ] && { ask "Continue?" y || { echo "Cancelled, nothing changed."; exit 0; }; }
[ "$DRY" = 0 ] && { [ -f "$SMILARM_UNIT" ] || [ "$DO_PKGS" = 1 ]; } && { sudo -v || { echo "sudo is needed for the service/packages"; exit 1; }; }

# ---------------------------------------------------------------- the work
backup_name() { echo "$HOME/smilarm-backup-$(date +%Y%m%d-%H%M%S).tar.gz"; }
do_backup() {
  local out; out=$(backup_name)
  if [ "$found_docker" = 1 ] && [ "$has_volume" = 1 ]; then # tar the volume with the app image itself (nothing extra to pull); run as the caller
    local img=""; for i in "${IMAGES[@]}"; do $DC image inspect "$i" >/dev/null 2>&1 && { img=$i; break; }; done
    [ -n "$img" ] || { echo "no image to run the backup with"; return 1; }
    act $DC run --rm --user "$(id -u):$(id -g)" -v "$VOLUME":/state:ro -v "$HOME":/backup --entrypoint tar "$img" czf "/backup/$(basename "$out")" -C /state . || return 1
  else
    local files=(); [ -d "$SMILARM_HOME/data" ] && files+=(data); [ -f "$SMILARM_HOME/config.toml" ] && files+=(config.toml)
    act tar -czf "$out" -C "$SMILARM_HOME" "${files[@]}" || return 1
  fi
  act chmod 600 "$out" # the config holds your access token
  echo "${DRYTAG}backup: $out"
}
do_container() {
  act $DC rm -f "$CONTAINER" >/dev/null 2>&1 || true
  for i in "${IMAGES[@]}"; do act $DC image rm -f "$i" >/dev/null 2>&1 || true; done
  echo "${DRYTAG}container and image removed"
}
do_service() {
  act sudo systemctl disable --now smilarm 2>&1 || true # also stops mpv: it lives in the service's cgroup
  act sudo rm -f "$SMILARM_UNIT"; act sudo systemctl daemon-reload; act sudo systemctl reset-failed smilarm 2>/dev/null || true
  echo "${DRYTAG}service removed"
}
do_app() { act rm -rf "$SMILARM_HOME/venv" "$SMILARM_HOME/app" && echo "${DRYTAG}removed the app and its Python environment"; }
do_data() {
  if [ "$found_docker" = 1 ] && [ "$has_volume" = 1 ]; then act $DC volume rm "$VOLUME" >/dev/null && echo "${DRYTAG}deleted volume $VOLUME"; fi
  if [ "$found_native" = 1 ]; then act rm -rf "$SMILARM_HOME/data" "$SMILARM_HOME/config.toml" "$SMILARM_HOME/config.toml.bak" && echo "${DRYTAG}deleted config.toml and data/"; fi
}
do_skill() { act rm -rf "$SKILL_DIR" && echo "${DRYTAG}removed $SKILL_DIR"; }
do_pkgs() { act sudo apt-get remove -y "${APT_PKGS[@]}" && echo "${DRYTAG}removed ${APT_PKGS[*]}"; }
do_command() { # everything of ours in the home, but never your own docker-compose.local.yml or (native) kept config/data
  act rm -rf "$SMILARM_HOME/bin" "$SMILARM_HOME/lib" "$SMILARM_HOME/share" "$SMILARM_HOME/mode" "$SMILARM_HOME/version" \
    "$SMILARM_HOME/install.log" "$SMILARM_HOME/.image-ref" "$SMILARM_HOME/compose/.env" "$SMILARM_HOME/compose/docker-compose.yml" "$SMILARM_HOME/compose/docker-compose.sound.yml"
  [ "$(readlink "$SMILARM_BIN_DIR/smilarm" 2>/dev/null)" = "$SMILARM_HOME/bin/smilarm" ] && act rm -f "$SMILARM_BIN_DIR/smilarm"
  act rmdir "$SMILARM_HOME/compose" "$SMILARM_HOME" 2>/dev/null || true
  if [ -f "$SMILARM_HOME/compose/docker-compose.local.yml" ]; then echo "${DRYTAG}kept your $SMILARM_HOME/compose/docker-compose.local.yml"; else echo "${DRYTAG}removed the smilarm command"; fi
}

ui_plan "${steps[@]}"
rc=0
for s in "${steps[@]}"; do
  case "$s" in
    "Back up your data") ui_run "$s" hint_plain do_backup || rc=1 ;;
    "Remove the container and image") [ $rc = 0 ] && { ui_run "$s" hint_plain do_container || rc=1; } ;;
    "Stop and remove the service") [ $rc = 0 ] && { ui_run "$s" hint_plain do_service || rc=1; } ;;
    "Remove the app and Python environment") [ $rc = 0 ] && { ui_run "$s" hint_plain do_app || rc=1; } ;;
    "Delete your data and config") [ $rc = 0 ] && { ui_run "$s" hint_plain do_data || rc=1; } ;; # never after a failed backup
    "Remove the OpenClaw skill") ui_run "$s" hint_plain do_skill || rc=1 ;;
    "Remove system packages") ui_run "$s" hint_plain do_pkgs || rc=1 ;;
    "Remove the smilarm command") [ $rc = 0 ] && { ui_run "$s" hint_plain do_command || rc=1; } ;;
  esac
done
rm -f /tmp/smilarm-mpv.sock 2>/dev/null

echo
if [ "$rc" -ne 0 ]; then printf '%s%s Stopped because of an error%s (details above; log: %s). Nothing after the failed step was done.\n' "$C_ERR" "$M_ERR" "$C_OFF" "$UI_LOG"; exit 1; fi
if [ "$DRY" = 1 ]; then printf '%s%s Dry run finished: nothing was changed%s\n' "$C_OK" "$M_OK" "$C_OFF"; exit 0; fi
printf '%s%s Smilarm is uninstalled%s\n' "$C_OK" "$M_OK" "$C_OFF"
[ "$DO_DATA" = 0 ] && [ "$have_data" = 1 ] && echo "  Kept: your config and alarms ($([ "$found_docker" = 1 ] && echo "volume $VOLUME" || echo "$SMILARM_HOME")). Reinstalling picks them up again."
[ "$legacy_volume" = 1 ] && echo "  Note: an old \"Alarm+\" volume still exists: docker volume rm $LEGACY_VOLUME"
echo "  Not touched: Docker itself, the ESP32-CAM firmware, and anything on the moOde device. The folder you cloned can simply be deleted."
