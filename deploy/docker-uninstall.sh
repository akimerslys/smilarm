#!/usr/bin/env bash
# Remove the Docker install of Alarm+. Safe by default: your config and alarms (the `alarmplus-state` volume) are kept.
#
#   bash uninstall.sh                  stop and remove the container, network and image; KEEP your data
#   bash uninstall.sh --purge          also delete the data volume (config, alarms, logs) — a backup tarball is written to ~ first
#   bash uninstall.sh --no-backup      with --purge: skip the backup
#   bash uninstall.sh --yes            no questions (without --purge your data is still kept)
#   bash uninstall.sh --dry-run        show what would happen, change nothing
#   bash uninstall.sh --plain          no animation
#
# The project folder is never deleted; the end of the output tells you the command.
set -uo pipefail
ROOT="$(cd "$(dirname "$0")/.." && pwd)"
cd "$ROOT" || exit 1

YES=0 PURGE=0 BACKUP=1 DRY=0 PLAIN=0
for a in "$@"; do
  case "$a" in
    --yes | -y) YES=1 ;;
    --purge) PURGE=1 ;;
    --no-backup) BACKUP=0 ;;
    --dry-run) DRY=1 ;;
    --plain) PLAIN=1 ;;
    -h | --help) sed -n '2,11p' "$0"; exit 0 ;;
    *) echo "unknown option: $a (see --help)"; exit 2 ;;
  esac
done

# shellcheck source=deploy/ui.sh
. "$ROOT/deploy/ui.sh"
ui_init "${TMPDIR:-/tmp}/alarmplus-uninstall.log" "$PLAIN"
VOLUME=alarmplus-state IMAGE=alarmplus:latest NAME=alarmplus
DC=docker
docker info >/dev/null 2>&1 || { sudo -n docker info >/dev/null 2>&1 && DC="sudo docker"; }
if ! $DC info >/dev/null 2>&1; then echo "Cannot reach Docker (is the daemon running, and may you use it?). Nothing to do."; exit 1; fi

interactive=0
{ [ "$YES" = 0 ] && [ "$DRY" = 0 ] && [ -t 0 ] && [ -t 1 ]; } && interactive=1
ask() { # ask QUESTION DEFAULT(y|n)
  [ "$interactive" = 1 ] || { [ "$2" = y ]; return; }
  local ans hint="[y/N]"; [ "$2" = y ] && hint="[Y/n]"
  read -r -p "  $1 $hint " ans; ans=${ans:-$2}
  case "$ans" in [yY]*) return 0 ;; *) return 1 ;; esac
}
act() { if [ "$DRY" = 1 ]; then echo "[dry-run] $*"; else "$@"; fi; }
DRYTAG=""; [ "$DRY" = 1 ] && DRYTAG="(dry run, not done) "

has_container=0; $DC ps -a --format '{{.Names}}' 2>/dev/null | grep -qx "$NAME" && has_container=1
has_image=0;     $DC image inspect "$IMAGE" >/dev/null 2>&1 && has_image=1
has_volume=0;    $DC volume ls -q 2>/dev/null | grep -qx "$VOLUME" && has_volume=1

ui_title "Alarm+ uninstaller (Docker)"
[ "$DRY" = 1 ] && printf '%s(dry run: nothing will be changed)%s\n' "$C_DIM" "$C_OFF"
printf '\nFound:\n'
printf '  %-14s %s\n' "container" "$([ $has_container = 1 ] && echo "$NAME" || echo "none")"
printf '  %-14s %s\n' "image" "$([ $has_image = 1 ] && echo "$IMAGE ($(ui_hsize "$($DC image inspect "$IMAGE" --format '{{.Size}}' 2>/dev/null || echo 0)"))" || echo "none")"
printf '  %-14s %s\n' "data volume" "$([ $has_volume = 1 ] && echo "$VOLUME (config, alarms, logs)" || echo "none")"
printf '\n'

DO_PURGE=$PURGE
if [ "$interactive" = 1 ] && [ "$PURGE" = 0 ] && [ "$has_volume" = 1 ]; then
  ask "Also delete your data (config + alarms)? A backup is saved to your home folder first." n && DO_PURGE=1
  echo
fi
[ "$has_volume" = 1 ] || DO_PURGE=0

steps=()
[ "$DO_PURGE" = 1 ] && [ "$BACKUP" = 1 ] && [ "$has_image" = 1 ] && steps+=("Back up your data")
{ [ "$has_container" = 1 ] || [ "$has_image" = 1 ]; } && steps+=("Remove the container and image")
[ "$DO_PURGE" = 1 ] && steps+=("Delete the data volume")
if [ "${#steps[@]}" -eq 0 ]; then echo "Nothing to remove."; [ "$has_volume" = 1 ] && echo "Your data volume '$VOLUME' is kept (delete it with --purge)."; exit 0; fi
printf 'Will do:\n'; printf '  - %s\n' "${steps[@]}"
[ "$DO_PURGE" = 0 ] && [ "$has_volume" = 1 ] && printf '    (the data volume %s is kept)\n' "$VOLUME"
echo
if [ "$interactive" = 1 ]; then ask "Continue?" y || { echo "Cancelled, nothing changed."; exit 0; }; fi

do_backup() { # tar the volume with the app image itself (no extra image to pull); run as the caller so the file is yours
  local out="$HOME/alarmplus-backup-$(date +%Y%m%d-%H%M%S).tar.gz"
  act $DC run --rm --user "$(id -u):$(id -g)" -v "$VOLUME":/state:ro -v "$HOME":/backup --entrypoint tar "$IMAGE" czf "/backup/$(basename "$out")" -C /state . || return 1
  act chmod 600 "$out" # the config holds your access token
  echo "${DRYTAG}backup: $out"
}
do_remove() {
  act $DC compose down --remove-orphans || true
  act $DC rm -f "$NAME" >/dev/null 2>&1 || true
  act $DC image rm -f "$IMAGE" || true
  echo "${DRYTAG}container and image removed"
}
do_volume() { act $DC volume rm "$VOLUME" && echo "${DRYTAG}deleted volume $VOLUME"; }

ui_plan "${steps[@]}"
rc=0
[ "$DO_PURGE" = 1 ] && [ "$BACKUP" = 1 ] && [ "$has_image" = 1 ] && { ui_run "Back up your data" hint_plain do_backup || rc=1; }
if [ "$rc" = 0 ]; then # never delete data after a failed backup
  { [ "$has_container" = 1 ] || [ "$has_image" = 1 ]; } && { ui_run "Remove the container and image" hint_plain do_remove || rc=1; }
  [ "$DO_PURGE" = 1 ] && { ui_run "Delete the data volume" hint_plain do_volume || rc=1; }
fi

echo
if [ "$rc" -ne 0 ]; then printf '%s%s Stopped because of an error%s (details above; log: %s). Nothing after the failed step was done.\n' "$C_ERR" "$M_ERR" "$C_OFF" "$UI_LOG"; exit 1; fi
if [ "$DRY" = 1 ]; then printf '%s%s Dry run finished: nothing was changed%s\n' "$C_OK" "$M_OK" "$C_OFF"; exit 0; fi
printf '%s%s Alarm+ is uninstalled%s\n' "$C_OK" "$M_OK" "$C_OFF"
[ "$DO_PURGE" = 0 ] && [ "$has_volume" = 1 ] && echo "  Kept: your data volume '$VOLUME'. Reinstalling picks it up again; delete it with:  docker volume rm $VOLUME"
echo "  Not touched: the project folder $ROOT"
echo "               delete it yourself when you no longer need it:  rm -rf \"$ROOT\""
echo "  Not touched: Docker itself, the ESP32-CAM firmware, and anything on the moOde device."
