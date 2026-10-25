#!/usr/bin/env bash
# Remove the NATIVE (non-Docker) install of Alarm+. For the Docker install use `bash uninstall.sh` (it finds both).
# Safe by default: it asks before deleting anything you may want to keep.
#
#   bash deploy/uninstall.sh                  interactive: stop + remove the service and .venv, then ask about
#                                             your data (alarms, config.toml), the OpenClaw skill and system packages
#   bash deploy/uninstall.sh --yes            no questions: service + .venv only; data/config/skill/packages are kept
#   bash deploy/uninstall.sh --purge          also delete data/ (alarms, model, logs) and config.toml
#                                             (a backup tarball is written to your home folder first)
#   bash deploy/uninstall.sh --openclaw       also remove the OpenClaw skill
#   bash deploy/uninstall.sh --packages       also apt-remove what the installer added (mpv, libgles2, libegl1, python3-venv)
#   bash deploy/uninstall.sh --no-backup      with --purge: skip the backup tarball
#   bash deploy/uninstall.sh --dry-run        show what would happen, change nothing
#   bash deploy/uninstall.sh --plain          no animation
#
# The project folder itself is never deleted by this script; it tells you the command at the end.
set -uo pipefail
ROOT="$(cd "$(dirname "$0")/.." && pwd)"
cd "$ROOT" || exit 1

YES=0 PURGE=0 OPENCLAW=0 PACKAGES=0 BACKUP=1 DRY=0 PLAIN=0
for a in "$@"; do
  case "$a" in
    --yes | -y) YES=1 ;;
    --purge) PURGE=1 ;;
    --openclaw) OPENCLAW=1 ;;
    --packages) PACKAGES=1 ;;
    --no-backup) BACKUP=0 ;;
    --dry-run) DRY=1 ;;
    --plain) PLAIN=1 ;;
    -h | --help) sed -n '2,15p' "$0"; exit 0 ;;
    *) echo "unknown option: $a (see --help)"; exit 2 ;;
  esac
done

DRYTAG=""
[ "$DRY" = 1 ] && DRYTAG="(dry run, not done) "
UNIT=${ALARMPLUS_UNIT:-/etc/systemd/system/alarmplus.service}
SKILL_DIR="${OPENCLAW_WORKSPACE:-$HOME/.openclaw/workspace}/skills/alarm-plus"
APT_PKGS=(mpv libgles2 libegl1 python3-venv) # nano and libgl1 are left alone: most systems already had them for other reasons

# shellcheck source=deploy/ui.sh
. "$ROOT/deploy/ui.sh"
ui_init "${TMPDIR:-/tmp}/alarmplus-uninstall.log" "$PLAIN" # not inside data/: data/ may be about to be deleted

interactive=0
{ [ "$YES" = 0 ] && [ "$DRY" = 0 ] && [ -t 0 ] && [ -t 1 ]; } && interactive=1

# ask QUESTION DEFAULT(y|n): returns 0 for yes. Non-interactive runs take the default.
ask() {
  [ "$interactive" = 1 ] || { [ "$2" = y ]; return; }
  local ans hint="[y/N]"
  [ "$2" = y ] && hint="[Y/n]"
  read -r -p "  $1 $hint " ans
  ans=${ans:-$2}
  case "$ans" in [yY]*) return 0 ;; *) return 1 ;; esac
}

act() { if [ "$DRY" = 1 ]; then echo "[dry-run] $*"; else "$@"; fi; }
have_unit() { [ -f "$UNIT" ]; }

# ---------------------------------------------------------------- what exists
n_alarms=0
[ -f data/alarms.json ] && n_alarms=$(grep -o '"id"' data/alarms.json 2>/dev/null | wc -l | tr -d ' ')
data_size=0
[ -d data ] && data_size=$(du -sk data | awk '{printf "%d", $1*1000}')
venv_size=0
[ -d .venv ] && venv_size=$(du -sk .venv | awk '{printf "%d", $1*1000}')

ui_title "Alarm+ uninstaller"
[ "$DRY" = 1 ] && printf '%s(dry run: nothing will be changed)%s\n' "$C_DIM" "$C_OFF"
printf '\nFound:\n'
printf '  %-16s %s\n' "systemd service" "$(have_unit && echo "$UNIT" || echo "not installed")"
printf '  %-16s %s\n' "Python env" "$([ -d .venv ] && echo ".venv ($(ui_hsize "$venv_size"))" || echo "none")"
printf '  %-16s %s\n' "your data" "$([ -d data ] && echo "data/ ($(ui_hsize "$data_size"), $n_alarms alarm(s))" || echo "none")$([ -f config.toml ] && echo ", config.toml")"
printf '  %-16s %s\n' "OpenClaw skill" "$([ -d "$SKILL_DIR" ] && echo "$SKILL_DIR" || echo "not installed")"
printf '\n'

# ---------------------------------------------------------------- decisions (all questions up front)
DO_DATA=$PURGE DO_SKILL=$OPENCLAW DO_PKGS=$PACKAGES
if [ "$interactive" = 1 ]; then
  if [ "$PURGE" = 0 ] && { [ -d data ] || [ -f config.toml ]; }; then
    echo "  Your alarms and settings live in data/ and config.toml."
    ask "Delete them too? (a backup is saved to your home folder first)" n && DO_DATA=1
  fi
  if [ "$OPENCLAW" = 0 ] && [ -d "$SKILL_DIR" ]; then ask "Remove the OpenClaw skill ($SKILL_DIR)?" n && DO_SKILL=1; fi
  if [ "$PACKAGES" = 0 ]; then ask "Also remove system packages the installer added (${APT_PKGS[*]})? Other programs might use them." n && DO_PKGS=1; fi
  echo
fi
[ -d data ] || [ -f config.toml ] || DO_DATA=0
[ -d "$SKILL_DIR" ] || DO_SKILL=0

steps=()
have_unit && steps+=("Stop and remove the service")
[ -d .venv ] && steps+=("Remove the Python environment")
[ "$DO_DATA" = 1 ] && steps+=("Delete your data and config")
[ "$DO_SKILL" = 1 ] && steps+=("Remove the OpenClaw skill")
[ "$DO_PKGS" = 1 ] && steps+=("Remove system packages")
if [ "${#steps[@]}" -eq 0 ]; then echo "Nothing to remove."; exit 0; fi

printf 'Will do:\n'
printf '  - %s\n' "${steps[@]}"
[ "$DO_DATA" = 1 ] && [ "$BACKUP" = 1 ] && printf '    (backup first: ~/alarmplus-backup-<date>.tar.gz)\n'
[ "$DO_DATA" = 0 ] && { [ -d data ] || [ -f config.toml ]; } && printf '    (your data/ and config.toml are kept)\n'
echo
if [ "$interactive" = 1 ]; then ask "Continue?" y || { echo "Cancelled, nothing changed."; exit 0; }; fi

{ have_unit || [ "$DO_PKGS" = 1 ]; } && [ "$DRY" = 0 ] && { sudo -v || { echo "sudo is needed for the service/packages"; exit 1; }; }

# ---------------------------------------------------------------- the work
do_service() {
  act sudo systemctl disable --now alarmplus 2>&1 || true # also stops mpv: it lives in the service's cgroup
  act sudo rm -f "$UNIT"
  act sudo systemctl daemon-reload
  act sudo systemctl reset-failed alarmplus 2>/dev/null || true
  echo "${DRYTAG}service removed"
}
do_venv() { act rm -rf .venv && echo "${DRYTAG}removed .venv ($(ui_hsize "$venv_size"))"; }
do_data() {
  if [ "$BACKUP" = 1 ]; then
    local files=() out="$HOME/alarmplus-backup-$(date +%Y%m%d-%H%M%S).tar.gz"
    [ -d data ] && files+=(data)
    [ -f config.toml ] && files+=(config.toml)
    act tar -czf "$out" "${files[@]}" || return 1
    act chmod 600 "$out" # the config holds your access token
    echo "${DRYTAG}backup: $out"
  fi
  act rm -rf data config.toml config.toml.bak config.toml.tmp
  echo "${DRYTAG}deleted data/ and config.toml"
}
do_skill() { act rm -rf "$SKILL_DIR" && echo "${DRYTAG}removed $SKILL_DIR"; }
do_pkgs() { act sudo apt-get remove -y "${APT_PKGS[@]}" && echo "${DRYTAG}removed ${APT_PKGS[*]}"; }

ui_plan "${steps[@]}"
rc=0
have_unit && { ui_run "Stop and remove the service" hint_plain do_service || rc=1; }
[ -d .venv ] && { ui_run "Remove the Python environment" hint_plain do_venv || rc=1; }
[ "$DO_DATA" = 1 ] && { ui_run "Delete your data and config" hint_plain do_data || rc=1; }
[ "$DO_SKILL" = 1 ] && { ui_run "Remove the OpenClaw skill" hint_plain do_skill || rc=1; }
[ "$DO_PKGS" = 1 ] && { ui_run "Remove system packages" hint_plain do_pkgs || rc=1; }
rm -f /tmp/alarmplus-mpv.sock 2>/dev/null

echo
if [ "$rc" -ne 0 ]; then printf '%s%s Finished with errors%s (details above; log: %s)\n' "$C_ERR" "$M_ERR" "$C_OFF" "$UI_LOG"; exit 1; fi
if [ "$DRY" = 1 ]; then printf '%s%s Dry run finished: nothing was changed%s\n' "$C_OK" "$M_OK" "$C_OFF"; exit 0; fi
printf '%s%s Alarm+ is uninstalled%s\n' "$C_OK" "$M_OK" "$C_OFF"
echo "  Not touched: the project folder $ROOT"
echo "               delete it yourself when you no longer need it:  rm -rf \"$ROOT\""
echo "  Not touched: the ESP32-CAM firmware (reflash it or just unplug it) and anything on the moOde device."
