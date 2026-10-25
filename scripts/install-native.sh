#!/usr/bin/env bash
# OPTIONAL native install of Smilarm (systemd service + Python venv) for people who do not want Docker.
# Docker is the recommended way: `bash install.sh`. This one is `bash install.sh --native`. Debian-based Linux (Raspberry Pi OS), 64-bit.
#
#   bash install.sh --native                run it; opens config.toml in an editor at the end (skip with --no-edit)
#   bash install.sh --native --no-edit      skip the editor (also skipped when there is no terminal)
#   bash install.sh --native --clean-cache  afterwards free the download caches (pip + apt); a later update downloads again
#   bash install.sh --native --plain        no animation (for logs)
#   bash install.sh --native --demo         simulate the whole run with fake downloads (installs nothing)
#
# Everything lives in ~/.smilarm (override: SMILARM_HOME): the app, a Python venv, config.toml and data/. The folder you cloned
# is not needed afterwards. Re-running it updates. The install log is ~/.smilarm/install.log.
set -uo pipefail
HERE="$(cd "$(dirname "${BASH_SOURCE[0]:-$0}")" && pwd)"
SRC="$(cd "$HERE/.." && pwd)"
# shellcheck source=scripts/lib/common.sh
. "$HERE/lib/common.sh"
# shellcheck source=scripts/lib/ui.sh
. "$HERE/lib/ui.sh"

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
CASCADE_URL=https://raw.githubusercontent.com/opencv/opencv/4.x/data/haarcascades
APT_PKGS=(python3-venv mpv nano libgl1 libgles2 libegl1)
DEMO_DELAY=${DEMO_DELAY:-0.25} # seconds between fake output lines in --demo
APP="$SMILARM_HOME/app" VENV="$SMILARM_HOME/venv" CFG="$SMILARM_HOME/config.toml" DATA="$SMILARM_HOME/data"

mkdir -p "$SMILARM_HOME"
ui_init "$SMILARM_HOME/install.log" "$PLAIN"
trap 'printf "\n"; exit 130' INT

# ---------------------------------------------------------------- the real work (one function per step)
real_apt() { sudo apt-get install -y --no-install-recommends -o Dpkg::Use-Pty=0 -o APT::Color=0 "${APT_PKGS[@]}"; }
real_venv() { # the app is COPIED here: nothing refers to the cloned folder
  mkdir -p "$APP" && rm -rf "$APP/smilarm" "$APP/web" && cp -r "$SRC/smilarm" "$SRC/web" "$SRC/requirements.txt" "$APP/" &&
    find "$APP" -name __pycache__ -prune -exec rm -rf {} + &&
    { [ -x "$VENV/bin/python" ] || python3 -m venv "$VENV"; }
}
real_pip() { "$VENV/bin/pip" install --no-color --progress-bar off -r "$APP/requirements.txt"; }
real_service() {
  sed "s|@HOME@|$SMILARM_HOME|g; s|@USER@|$USER|g" "$SRC/scripts/smilarm.service" | sudo tee "$SMILARM_UNIT" >/dev/null &&
    sudo systemctl daemon-reload && sudo systemctl enable smilarm && sudo systemctl restart smilarm
}
real_clean_native() {
  "$VENV/bin/pip" cache purge 2>&1 || true # "Files removed: N"
  sudo apt-get clean && echo "apt package cache cleaned"
}
demo_clean_native() { demo_line "Files removed: 214"; demo_line "apt package cache cleaned"; }
hint_clean_native() { if [ "$1" = final ]; then echo "pip: $(grep -oE 'Files removed: [0-9]+|Cache is (already )?empty|No cache' "$2" | head -1 | tr 'A-Z' 'a-z'), apt cache cleaned"; else tail -n 1 "$2"; fi; }

real_health() { # wait until the web server answers (200 = ok, 401 = up but a token is set)
  local port code i
  port=$(cd "$SMILARM_HOME" && PYTHONPATH="$APP" "$VENV/bin/python" -c "from smilarm import config; print(config.load('$CFG').server.port)" 2>/dev/null || echo 8080)
  for i in $(seq 1 40); do
    code=$(curl -s -o /dev/null -w '%{http_code}' "http://127.0.0.1:$port/api/state" 2>/dev/null)
    case "$code" in 200 | 401) echo "answered on port $port after $((i / 2))s"; return 0 ;; esac
    echo "waiting for port $port ($i)…"
    sleep 0.5
  done
  echo "no answer on port $port. Recent service log:"
  sudo journalctl -u smilarm -n 15 --no-pager 2>&1
  return 1
}
hint_health() { if [ "$1" = final ]; then tail -n 1 "$2"; else echo "waiting for the web server to answer…"; fi; }

# ---------------------------------------------------------------- --demo: fake versions of the same steps
demo_line() { printf '%s\n' "$1"; sleep "$DEMO_DELAY"; }
demo_apt() {
  local p
  for p in "libgles2 arm64 1.7.0-1 [18.0 kB]" "libegl1 arm64 1.7.0-1 [33.5 kB]" "mpv arm64 0.40.0-3 [1,266 kB]" "nano arm64 8.4-1 [685 kB]"; do
    demo_line "Get:1 http://deb.debian.org/debian trixie/main arm64 $p"
  done
  demo_line "Unpacking mpv:arm64 (0.40.0-3) ..."
  demo_line "Setting up mpv:arm64 (0.40.0-3) ..."
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
    if [[ "$*" == *-fsSIL* ]]; then printf 'HTTP/2 200\r\ncontent-length: 3758596\r\n\r\n'; return; fi
    local i
    for i in $(seq 1 25); do head -c 150343 /dev/zero >>"$3"; sleep "$(awk -v d="$DEMO_DELAY" 'BEGIN{print d/4}')"; done
  }
}
demo_service() { demo_line "enabled"; demo_line "started"; }
demo_health() { demo_line "answered on port 8080 after 1s"; }

# ---------------------------------------------------------------- go
ui_title "Smilarm installer (native, no Docker)"
[ "$DEMO" = 1 ] && printf '%s(demo mode: nothing is downloaded or installed)%s\n' "$C_DIM" "$C_OFF"

if [ "$DEMO" = 0 ]; then
  sudo -v || { echo "sudo is needed to install system packages"; exit 1; } # ask for the password up front, not behind a spinner
  free_kb=$(df -Pk "$HOME" | awk 'NR==2{print $4}')
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
printf '  %-18s %s\n\n' "Face model" "$([ -s "$DATA/face_landmarker.task" ] && echo "nothing (already downloaded)" || ui_hsize "${MODEL_B:-0}")"

if [ "$CLEAN" = 1 ]; then ui_plan s1 s2 s3 s4 s5 s6 s7 s8; else ui_plan s1 s2 s3 s4 s5 s6 s7; fi
T_ALL=$SECONDS

# 1. system packages
if [ "$DEMO" = 1 ]; then ui_run "System packages" hint_apt demo_apt || exit 1
elif [ "${APT_PLAN_N:-0}" -eq 0 ]; then ui_skip "System packages" "already installed"
else ui_run "System packages" hint_apt real_apt || exit 1; fi

# 2. the app and its virtualenv, in ~/.smilarm
if [ "$DEMO" = 1 ]; then ui_run "App and Python environment" hint_plain demo_line "copied to ~/.smilarm/app, created ~/.smilarm/venv" || exit 1
else ui_run "App and Python environment" hint_plain real_venv || exit 1; fi

# 3. Python libraries
if [ "$DEMO" = 1 ]; then ui_run "Python libraries" hint_pip demo_pip || exit 1
else ui_run "Python libraries" hint_pip real_pip || exit 1; fi

# 4. face model
if [ "$DEMO" = 1 ]; then demo_download_setup; ui_download "Face-detection model" "$MODEL_URL" "$DEMO_TMP/model" || exit 1
else
  mkdir -p "$DATA/cascades"
  ui_download "Face-detection model" "$MODEL_URL" "$DATA/face_landmarker.task" || exit 1
  # the OpenCV 5 wheels ship no Haar cascade files; the fallback detector needs them (2 small files, best effort)
  for f in haarcascade_frontalface_default haarcascade_smile; do
    [ -s "$DATA/cascades/$f.xml" ] || curl -fsSL -o "$DATA/cascades/$f.xml" "$CASCADE_URL/$f.xml" \
      || printf '      %swarning: could not fetch %s.xml (only matters if MediaPipe cannot load)%s\n' "$C_DIM" "$f" "$C_OFF"
  done
fi

# 5. configuration: generate every default, let the user edit, validate (an existing file is never overwritten)
ui_note "Configuration"
if [ "$DEMO" = 1 ]; then
  echo "      (demo) would write ~/.smilarm/config.toml with all defaults and open it in ${EDITOR:-nano}"
else
  run_py() { (cd "$SMILARM_HOME" && PYTHONPATH="$APP" "$VENV/bin/python" -m smilarm "$@"); }
  if [ -f "$CFG" ]; then echo "      keeping existing config.toml"; else run_py --write-config "$CFG" | sed 's/^/      /'; fi
  if [ "$EDIT" = 1 ] && [ -t 0 ] && [ -t 1 ]; then
    while true; do
      "${EDITOR:-nano}" "$CFG"
      if run_py --check-config "$CFG"; then break; fi
      read -r -p "      config.toml has the problems listed above. Edit again? [Y/n] " ans
      case "${ans:-Y}" in [nN]*) echo "      continuing as is (invalid values are rejected at start)"; break ;; esac
    done
  fi
fi

# 6. service (+ the `smilarm` command and its helper scripts, so the clone can be deleted)
if [ "$DEMO" = 0 ]; then install_home "$SRC" native; fi
if [ "$DEMO" = 1 ]; then ui_run "System service" hint_plain demo_service || exit 1
else ui_run "System service" hint_plain real_service || exit 1; fi

# 7. health check
if [ "$DEMO" = 1 ]; then ui_run "Starting Smilarm" hint_health demo_health || exit 1
else ui_run "Starting Smilarm" hint_health real_health || exit 1; fi

# 8. optional: free the download caches (pip wheels, apt packages). They are only needed to re-install faster.
if [ "$CLEAN" = 1 ]; then
  if [ "$DEMO" = 1 ]; then ui_run "Clean the download caches" hint_clean_native demo_clean_native || exit 1
  else ui_run "Clean the download caches" hint_clean_native real_clean_native || exit 1; fi
fi

PORT=8080
[ "$DEMO" = 0 ] && PORT=$(cd "$SMILARM_HOME" && PYTHONPATH="$APP" "$VENV/bin/python" -c "from smilarm import config; print(config.load('$CFG').server.port)" 2>/dev/null || echo 8080)
VENV_SIZE=$([ -d "$VENV" ] && du -sk "$VENV" | awk '{printf "%d", $1*1000}' || echo 0)
printf '\n%s%s Smilarm is running%s  (installed in %s, the Python environment uses %s)\n' "$C_OK" "$M_OK" "$C_OFF" "$(ui_hms $((SECONDS - T_ALL)))" "$(ui_hsize "$VENV_SIZE")"
printf '  Open:     http://%s.local:%s\n' "$(hostname)" "$PORT"
printf '  Manage:   smilarm status | logs | restart | update | backup | uninstall        (smilarm help)\n'
in_path || printf '  Note:     add %s to your PATH to use the `smilarm` command:  export PATH="%s:$PATH"\n' "$SMILARM_BIN_DIR" "$SMILARM_BIN_DIR"
printf '  Config:   %s   (everything is also in the web UI, Settings)\n' "$CFG"
printf '  The folder you cloned is no longer needed: Smilarm lives in %s. You may delete it.\n' "$SMILARM_HOME"
