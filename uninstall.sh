#!/usr/bin/env bash
# Alarm+ uninstaller. Finds what is installed and removes it (asking before it deletes your data).
#   bash uninstall.sh              Docker and/or native install, whichever exists
#   bash uninstall.sh --docker     only the Docker install
#   bash uninstall.sh --native     only the native (non-Docker) install
# Other options (--yes, --purge, --no-backup, --dry-run, --plain, ...) are passed on; see the scripts' --help.
ROOT="$(cd "$(dirname "$0")" && pwd)"
only="" args=()
for a in "$@"; do
  case "$a" in
    --docker) only=docker ;;
    --native) only=native ;;
    *) args+=("$a") ;;
  esac
done

DC=docker
docker info >/dev/null 2>&1 || { sudo -n docker info >/dev/null 2>&1 && DC="sudo docker"; }

found_docker=0
if $DC info >/dev/null 2>&1; then
  { $DC ps -a --format '{{.Names}}' | grep -qx alarmplus; } || { $DC volume ls -q | grep -qx alarmplus-state; } || { $DC image inspect alarmplus:latest >/dev/null 2>&1; } && found_docker=1
fi
found_native=0
{ [ -f "${ALARMPLUS_UNIT:-/etc/systemd/system/alarmplus.service}" ] || [ -d "$ROOT/.venv" ]; } && found_native=1

[ "$only" = docker ] && found_native=0 && found_docker=1
[ "$only" = native ] && found_docker=0 && found_native=1

if [ "$found_docker" = 0 ] && [ "$found_native" = 0 ]; then
  echo "No Alarm+ installation found (no Docker container/volume/image, no systemd service, no .venv)."
  exit 0
fi
if [ "$found_docker" = 1 ] && [ "$found_native" = 1 ] && [ -z "$only" ]; then
  # Both exist (e.g. a native install you are replacing with Docker). Never remove one just because the other was asked for.
  echo "Found BOTH a Docker install and a native install of Alarm+."
  case " ${args[*]} " in
    *" --dry-run "*) echo "(dry run: showing both)" ;;
    *)
      if [ -t 0 ] && [ -t 1 ] && [[ " ${args[*]} " != *" --yes "* ]]; then
        read -r -p "  Remove both? [y/N] " ans
        case "${ans:-N}" in [yY]*) ;; *) echo "Choose one: bash uninstall.sh --docker   or   bash uninstall.sh --native"; exit 2 ;; esac
      else
        echo "Say which one to remove: bash uninstall.sh --docker   or   bash uninstall.sh --native   (nothing was changed)"
        exit 2
      fi ;;
  esac
fi
rc=0
if [ "$found_docker" = 1 ]; then bash "$ROOT/deploy/docker-uninstall.sh" "${args[@]}" || rc=1; fi
if [ "$found_native" = 1 ]; then bash "$ROOT/deploy/uninstall.sh" "${args[@]}" || rc=1; fi
exit $rc
