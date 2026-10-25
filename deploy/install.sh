#!/usr/bin/env bash
# OPTIONAL native install of Alarm+ (systemd service + Python venv), for people who do not want Docker.
# The recommended way is Docker: `bash install.sh` (see README). Use this one through `bash install.sh --native`.
# Raspberry Pi OS (Bookworm, 64-bit). Run from the project root as the normal user.
#
#   bash deploy/install.sh              install; open config.toml in an editor; (re)start the service
#   bash deploy/install.sh --no-edit    skip the editor (also skipped when there is no terminal)
#   bash deploy/install.sh --clean-cache  afterwards free the download caches (pip + apt); a later update downloads again
#   bash deploy/install.sh --plain      no animation (for logs); every step still prints its result
#   bash deploy/install.sh --demo       simulate the whole run with fake downloads (preview the UI, installs nothing)
#
# Everything the installer prints is also saved to data/install.log.
set -uo pipefail
ROOT="$(cd "$(dirname "$0")/.." && pwd)"
cd "$ROOT" || exit 1

EDIT=1 PLAIN=0 DEMO=0 CLEAN=0
for a in "$@"; do
  case "$a" in
    --no-edit) EDIT=0 ;;
    --clean-cache) CLEAN=1 ;;
    --plain) PLAIN=1 ;;
    --demo) DEMO=1 ;;
    -h | --help) sed -n '2,12p' "$0"; exit 0 ;;
    *) echo "unknown option: $a (see --help)"; exit 2 ;;
  esac
done

MODEL_URL=https://storage.googleapis.com/mediapipe-models/face_landmarker/face_landmarker/float16/1/face_landmarker.task
APT_PKGS=(python3-venv mpv nano libgl1 libgles2 libegl1)
DEMO_DELAY=${DEMO_DELAY:-0.25} # seconds between fake output lines in --demo

mkdir -p data
# shellcheck source=deploy/ui.sh
. "$ROOT/deploy/ui.sh"
ui_init "$ROOT/data/install.log" "$PLAIN"
trap 'printf "\n"; exit 130' INT

# ---------------------------------------------------------------- the real work (one function per step)
real_apt() { sudo apt-get install -y --no-install-recommends -o Dpkg::Use-Pty=0 -o APT::Color=0 "${APT_PKGS[@]}"; }
real_venv() { python3 -m venv .venv; }
real_pip() { .venv/bin/pip install --no-color --progress-bar off -r requirements.txt; }
real_service() {
  sed "s|@ROOT@|$ROOT|g; s|@USER@|$USER|g" deploy/alarmplus.service | sudo tee /etc/systemd/system/alarmplus.service >/dev/null &&
    sudo systemctl daemon-reload && sudo systemctl enable alarmplus && sudo systemctl restart alarmplus
}
real_clean_native() {
  .venv/bin/pip cache purge 2>&1 || true # "Files removed: N"
  sudo apt-get clean && echo "apt package cache cleaned"
}
demo_clean_native() { demo_line "Files removed: 214"; demo_line "apt package cache cleaned"; }
hint_clean_native() { if [ "$1" = final ]; then echo "pip: $(grep -oE 'Files removed: [0-9]+|Cache is (already )?empty|No cache' "$2" | head -1 | tr 'A-Z' 'a-z'), apt cache cleaned"; else tail -n 1 "$2"; fi; }

real_health() { # wait until the web server answers (200 = ok, 401 = up but a token is set)
  local port code i
  port=$(.venv/bin/python -c "from alarmplus import config; print(config.load('config.toml').server.port)" 2>/dev/null || echo 8080)
  for i in $(seq 1 40); do
    code=$(curl -s -o /dev/null -w '%{http_code}' "http://127.0.0.1:$port/api/state" 2>/dev/null)
    case "$code" in 200 | 401) echo "answered on port $port after $((i / 2))s"; return 0 ;; esac
    echo "waiting for port $port ($i)…"
    sleep 0.5
  done
  echo "no answer on port $port. Recent service log:"
  sudo journalctl -u alarmplus -n 15 --no-pager 2>&1
  return 1
}
hint_health() { if [ "$1" = final ]; then tail -n 1 "$2"; else echo "waiting for the web server to answer…"; fi; }

# ---------------------------------------------------------------- --demo: fake versions of the same steps
demo_line() { printf '%s\n' "$1"; sleep "$DEMO_DELAY"; }
demo_apt() {
  local p
  for p in "libgles2 arm64 1.6.0-1 [18.0 kB]" "libegl1 arm64 1.6.0-1 [33.5 kB]" "mpv arm64 0.35.1-3 [1,266 kB]" "nano arm64 7.2-1 [685 kB]"; do
    demo_line "Get:1 http://deb.debian.org/debian bookworm/main arm64 $p"
  done
  demo_line "Unpacking mpv:arm64 (0.35.1-3) ..."
  demo_line "Setting up mpv:arm64 (0.35.1-3) ..."
}
demo_pip() {
  demo_line "Collecting aiohttp>=3.9"
  demo_line "  Downloading aiohttp-3.9.5-cp311-cp311-manylinux_2_17_aarch64.whl (1.2 MB)"
  demo_line "Collecting opencv-contrib-python<6,>=4.9"
  demo_line "  Downloading opencv_contrib_python-5.0.0.93-cp37-abi3-manylinux_2_28_aarch64.whl (69.8 MB)"
  demo_line "Collecting mediapipe>=0.10.14"
  demo_line "  Downloading mediapipe-0.10.21-cp311-cp311-manylinux_2_28_aarch64.whl (35.6 MB)"
  demo_line "Installing collected packages: aiohttp, opencv-contrib-python, mediapipe"
  demo_line "Successfully installed aiohttp mediapipe opencv-contrib-python"
}
demo_download_setup() { # make `curl` a fake that "downloads" 3.8 MB slowly, so the real progress bar can be previewed
  DEMO_TMP=$(mktemp -d)
  trap 'rm -rf "$DEMO_TMP"' EXIT
  curl() {
    if [[ "$*" == *-fsSIL* ]]; then printf 'HTTP/2 200
content-length: 3758596

'; return; fi
    local i
    for i in $(seq 1 25); do head -c 150343 /dev/zero >>"$3"; sleep "$(awk -v d="$DEMO_DELAY" 'BEGIN{print d/4}')"; done
  }
}
demo_service() { demo_line "enabled"; demo_line "started"; }
demo_health() { demo_line "answered on port 8080 after 1s"; }

# ---------------------------------------------------------------- go
ui_title "Alarm+ installer"
[ "$DEMO" = 1 ] && printf '%s(demo mode: nothing is downloaded or installed)%s\n' "$C_DIM" "$C_OFF"

if [ "$DEMO" = 0 ]; then
  sudo -v || { echo "sudo is needed to install system packages"; exit 1; } # ask for the password up front, not behind a spinner
  free_kb=$(df -Pk . | awk 'NR==2{print $4}')
  [ "${free_kb:-0}" -lt 1000000 ] && printf '%sWarning: only %s free here; the Python libraries need roughly 1 GB.%s\n' "$C_ERR" "$(ui_hsize $((free_kb * 1000)))" "$C_OFF"
fi

# what will be downloaded (apt and the model are known up front; pip prints each size as it downloads)
if [ "$DEMO" = 1 ]; then APT_PLAN_N=4 APT_PLAN_B=2002500 MODEL_B=3758596
else
  read -r APT_PLAN_N APT_PLAN_B < <(sudo apt-get install --print-uris -qq -y --no-install-recommends "${APT_PKGS[@]}" 2>/dev/null |
    awk '$0 ~ /^.https?:/ {n++; b+=$3} END{printf "%d %d", n, b}')
  MODEL_B=$(curl -fsSIL "$MODEL_URL" 2>/dev/null | tr -d '\r' | awk 'tolower($1)=="content-length:"{v=$2} END{print v+0}')
fi
export APT_PLAN_N
printf '\nWill download:\n'
printf '  %-18s %s\n' "System packages" "$([ "${APT_PLAN_N:-0}" -gt 0 ] && echo "${APT_PLAN_N} package(s), $(ui_hsize "$APT_PLAN_B")" || echo "nothing (already installed)")"
printf '  %-18s %s\n' "Python libraries" "sizes appear below as each file downloads (aiohttp, OpenCV, MediaPipe, ...)"
printf '  %-18s %s\n\n' "Face model" "$([ -s data/face_landmarker.task ] && echo "nothing (already downloaded)" || ui_hsize "${MODEL_B:-0}")"

if [ "$CLEAN" = 1 ]; then ui_plan s1 s2 s3 s4 s5 s6 s7 s8; else ui_plan s1 s2 s3 s4 s5 s6 s7; fi
T_ALL=$SECONDS

# 1. system packages
if [ "$DEMO" = 1 ]; then ui_run "System packages" hint_apt demo_apt || exit 1
elif [ "${APT_PLAN_N:-0}" -eq 0 ]; then ui_skip "System packages" "already installed"
else ui_run "System packages" hint_apt real_apt || exit 1; fi

# 2. virtualenv
if [ "$DEMO" = 1 ]; then ui_run "Python environment" hint_plain demo_line "created .venv" || exit 1
elif [ -x .venv/bin/python ]; then ui_skip "Python environment" ".venv exists"
else ui_run "Python environment" hint_plain real_venv || exit 1; fi

# 3. Python libraries
if [ "$DEMO" = 1 ]; then ui_run "Python libraries" hint_pip demo_pip || exit 1
else ui_run "Python libraries" hint_pip real_pip || exit 1; fi

# 4. face model
if [ "$DEMO" = 1 ]; then demo_download_setup; ui_download "Face-detection model" "$MODEL_URL" "$DEMO_TMP/model" || exit 1
else
  ui_download "Face-detection model" "$MODEL_URL" data/face_landmarker.task || exit 1
  # the OpenCV 5 wheels ship no Haar cascade files; the fallback detector needs them (2 small files, best effort)
  mkdir -p data/cascades
  for f in haarcascade_frontalface_default haarcascade_smile; do
    [ -s "data/cascades/$f.xml" ] || curl -fsSL -o "data/cascades/$f.xml" "https://raw.githubusercontent.com/opencv/opencv/4.x/data/haarcascades/$f.xml" \
      || printf '      %swarning: could not fetch %s.xml (only matters if MediaPipe cannot load)%s\n' "$C_DIM" "$f" "$C_OFF"
  done
fi

# 5. configuration: generate every default, let the user edit, validate (an existing file is never overwritten)
ui_note "Configuration"
if [ "$DEMO" = 1 ]; then
  echo "      (demo) would write config.toml with all defaults and open it in ${EDITOR:-nano}"
else
  if [ -f config.toml ]; then echo "      keeping existing config.toml"; else .venv/bin/python -m alarmplus --write-config config.toml | sed 's/^/      /'; fi
  if [ "$EDIT" = 1 ] && [ -t 0 ] && [ -t 1 ]; then
    while true; do
      "${EDITOR:-nano}" config.toml
      if .venv/bin/python -m alarmplus --check-config config.toml; then break; fi
      read -r -p "      config.toml has the problems listed above. Edit again? [Y/n] " ans
      case "${ans:-Y}" in [nN]*) echo "      continuing as is (invalid values are rejected at start)"; break ;; esac
    done
  fi
fi

# 6. service
if [ "$DEMO" = 1 ]; then ui_run "System service" hint_plain demo_service || exit 1
else ui_run "System service" hint_plain real_service || exit 1; fi

# 7. health check
if [ "$DEMO" = 1 ]; then ui_run "Starting Alarm+" hint_health demo_health || exit 1
else ui_run "Starting Alarm+" hint_health real_health || exit 1; fi

# 8. optional: free the download caches (pip wheels, apt packages). They are only needed to re-install faster.
if [ "$CLEAN" = 1 ]; then
  if [ "$DEMO" = 1 ]; then ui_run "Clean the download caches" hint_clean_native demo_clean_native || exit 1
  else ui_run "Clean the download caches" hint_clean_native real_clean_native || exit 1; fi
fi

PORT=8080
[ "$DEMO" = 0 ] && PORT=$(.venv/bin/python -c "from alarmplus import config; print(config.load('config.toml').server.port)" 2>/dev/null || echo 8080)
VENV_SIZE=$([ -d .venv ] && du -sk .venv | awk '{printf "%d", $1*1000}' || echo 0)
printf '\n%s%s Alarm+ is running%s  (installed in %s, .venv uses %s)\n' "$C_OK" "$M_OK" "$C_OFF" "$(ui_hms $((SECONDS - T_ALL)))" "$(ui_hsize "$VENV_SIZE")"
printf '  Web UI:   http://%s.local:%s\n' "$(hostname)" "$PORT"
printf '  Settings: edit %s/config.toml, then: sudo systemctl restart alarmplus\n' "$ROOT"
printf '  Log:      %s\n' "$ROOT/data/install.log"
