#!/usr/bin/env bash
# Install / update Alarm+ with Docker (the primary way). Run from the project root, or through `bash install.sh`.
#
#   bash install.sh                    build the image, start the container, wait until it is healthy
#   bash install.sh --no-start         build only
#   bash install.sh --clean-cache      afterwards free Docker's build cache (a later update downloads again)
#   bash install.sh --clean-only       just free the build cache and old Alarm+ images; build and start nothing
#   bash install.sh --install-docker   install Docker first with Docker's official script (https://get.docker.com), if it is missing
#   bash install.sh --plain            no animation (for logs)
#   bash install.sh --demo             simulate the whole run (nothing is built or started)
#   bash install.sh --native           the optional non-Docker install instead (see docs/native_install.md)
#
# Re-running it is how you UPDATE: `git pull && bash install.sh` rebuilds and recreates the container;
# your config, alarms and logs live in the `alarmplus-state` volume and are kept.
# There is nothing to edit: finish the setup in the web UI (Settings). Everything is also written to data/install.log.
set -uo pipefail
ROOT="$(cd "$(dirname "$0")/.." && pwd)"
cd "$ROOT" || exit 1

START=1 PLAIN=0 DEMO=0 GET_DOCKER=0 CLEAN=0 CLEAN_ONLY=0
for a in "$@"; do
  case "$a" in
    --no-start) START=0 ;;
    --clean-cache) CLEAN=1 ;;
    --clean-only) CLEAN=1 CLEAN_ONLY=1 ;;
    --plain) PLAIN=1 ;;
    --demo) DEMO=1 ;;
    --install-docker) GET_DOCKER=1 ;;
    -h | --help) sed -n '2,13p' "$0"; exit 0 ;;
    *) echo "unknown option: $a (see --help)"; exit 2 ;;
  esac
done

mkdir -p data
# shellcheck source=deploy/ui.sh
. "$ROOT/deploy/ui.sh"
ui_init "$ROOT/data/install.log" "$PLAIN"
trap 'printf "\n"; exit 130' INT
DEMO_DELAY=${DEMO_DELAY:-0.25}
DC=docker # becomes "sudo docker" if the current user may not talk to the Docker socket
BASE_PORT=${ALARMPLUS_DEFAULT_PORT:-8080}

port_busy() { (exec 3<>"/dev/tcp/127.0.0.1/$1") 2>/dev/null; }

# ---------------------------------------------------------------- steps (one function each)
docker_ok() { $DC info >/dev/null 2>&1; }

# Decide how to call docker. Runs in the main shell: steps run in background subshells, whose variable changes would be lost.
detect_dc() {
  if docker info >/dev/null 2>&1; then DC=docker
  elif command -v docker >/dev/null 2>&1 && { sudo -n docker info >/dev/null 2>&1 || { [ -t 0 ] && sudo docker info >/dev/null 2>&1; }; }; then DC="sudo docker"
  fi
}

check_docker() {
  if ! command -v docker >/dev/null 2>&1; then
    if [ "$GET_DOCKER" = 1 ]; then
      echo "Docker is missing: installing it with the official script from get.docker.com"
      curl -fsSL https://get.docker.com | sudo sh || return 1
      sudo usermod -aG docker "$USER" 2>/dev/null || true
      DC="sudo docker"
    else
      echo "Docker is not installed."
      echo "Install it from https://docs.docker.com/engine/install/ (on Raspberry Pi OS: https://docs.docker.com/engine/install/debian/),"
      echo "or let this script do it:  bash install.sh --install-docker"
      return 1
    fi
  fi
  detect_dc
  if ! docker_ok; then
    if [ "$DC" != docker ] && $DC info >/dev/null 2>&1; then
      echo "using sudo for docker (add yourself to the docker group to avoid it: sudo usermod -aG docker $USER, then log in again)"
    else
      echo "Docker is installed, but its daemon cannot be reached. Is it running? (sudo systemctl start docker)"
      return 1
    fi
  fi
  $DC compose version >/dev/null 2>&1 || { echo "the 'docker compose' plugin is missing (sudo apt install docker-compose-plugin)"; return 1; }
  echo "Docker $($DC version --format '{{.Server.Version}}' 2>/dev/null), $($DC compose version --short 2>/dev/null)"
}

clean_cache() {
  echo "removing ALL unused Docker build cache on this machine (images, containers and volumes are not touched) ..."
  $DC builder prune -af || return 1
  echo "removing old untagged Alarm+ images left behind by updates ..."
  $DC image prune -f --filter label=app=alarmplus || return 1
}
# "Total reclaimed space: 1.2GB" (image prune) / "Total:  3.1GB" (builder prune) -> "3.1GB + 1.2GB freed"
hint_clean() {
  if [ "$1" = final ]; then
    local sizes
    sizes=$(grep -hE '^Total' "$2" | sed -E 's/^Total( reclaimed space)?:[[:space:]]*//' | paste -sd'+' | sed 's/+/ + /g')
    if [ -n "$sizes" ]; then echo "freed $sizes (build cache + old images)"; else echo "nothing to clean"; fi
  else tail -n 1 "$2"; fi
}
demo_clean() { demo_line "Deleted build cache objects: 41"; demo_line "Total:  3.132GB"; demo_line "Total reclaimed space: 1.58GB"; }

build_image() { $DC compose --progress=plain build; }
start_container() { $DC compose up -d --remove-orphans; }

wait_healthy() { # the image has a HEALTHCHECK; give it up to ~100 s (MediaPipe loads on first start)
  local i st
  for i in $(seq 1 50); do
    st=$($DC inspect --format '{{if .State.Health}}{{.State.Health.Status}}{{else}}{{.State.Status}}{{end}}' alarmplus 2>/dev/null || echo "missing")
    case "$st" in
      healthy | running) echo "container is $st after $((i * 2))s"; return 0 ;;
      exited | dead | missing) break ;;
    esac
    echo "waiting for the container ($st)…"
    sleep 2
  done
  echo "the container did not become healthy. Last log lines:"
  $DC logs --tail 25 alarmplus 2>&1
  return 1
}
hint_health() { if [ "$1" = final ]; then tail -n 1 "$2"; else tail -n 1 "$2"; fi; }

# ---------------------------------------------------------------- --demo: fake versions of the same steps
demo_line() { printf '%s\n' "$1"; sleep "$DEMO_DELAY"; }
demo_check() { demo_line "Docker 27.3.1, 2.29.7"; }
demo_build() {
  demo_line "#5 [2/9] RUN apt-get update && apt-get install -y mpv alsa-utils ..."
  demo_line "#5 4.1 Get:1 http://deb.debian.org/debian bookworm/main arm64 mpv arm64 0.35.1-3 [1,266 kB]"
  demo_line "#5 5.2 Get:2 http://deb.debian.org/debian bookworm/main arm64 libgles2 arm64 1.6.0-1 [18.0 kB]"
  demo_line "#7 [4/9] RUN pip install -r requirements.txt"
  demo_line "#7 9.0 Collecting opencv-contrib-python<6,>=4.9"
  demo_line "#7 10.1   Downloading opencv_contrib_python-5.0.0.93-cp37-abi3-manylinux_2_28_aarch64.whl (69.8 MB)"
  demo_line "#7 14.3 Collecting mediapipe>=0.10.14"
  demo_line "#7 15.0   Downloading mediapipe-0.10.21-cp313-cp313-manylinux_2_28_aarch64.whl (35.6 MB)"
  demo_line "#8 [5/9] RUN curl -fsSL -o /opt/models/face_landmarker.task ..."
  demo_line "#12 exporting to image"
  demo_line "#12 DONE 0.0s"
}
demo_start() { demo_line "Container alarmplus  Started"; }
demo_health() { demo_line "container is healthy after 6s"; }

# ---------------------------------------------------------------- go
ui_title "Alarm+ installer (Docker)"
[ "$DEMO" = 1 ] && printf '%s(demo mode: nothing is built or started)%s\n' "$C_DIM" "$C_OFF"
printf '\n'

steps=(check)
if [ "$CLEAN_ONLY" = 0 ]; then
  steps+=(build)
  [ "$START" = 1 ] && steps+=(start health)
fi
[ "$CLEAN" = 1 ] && steps+=(clean)
ui_plan "${steps[@]}"
T_ALL=$SECONDS

if [ "$DEMO" = 1 ]; then ui_run "Check Docker" hint_plain demo_check || exit 1; else ui_run "Check Docker" hint_plain check_docker || exit 1; fi
if [ "$DEMO" = 0 ] && [ "$CLEAN_ONLY" = 0 ]; then
  detect_dc
  # Host networking shares the device's ports. On a first install, if 8080 is taken (qBittorrent and many others use it),
  # start the new config on the next free port instead of crash-looping. Later the port is a normal setting in the UI.
  if ! $DC volume ls -q 2>/dev/null | grep -qx alarmplus-state && port_busy "$BASE_PORT"; then
    p=$((BASE_PORT + 1))
    while port_busy "$p" && [ "$p" -lt $((BASE_PORT + 50)) ]; do p=$((p + 1)); done
    export ALARMPLUS_PORT=$p
    printf '  %sPort %s is already in use by another program: Alarm+ will use port %s (change it any time in Settings).%s\n' "$C_DIM" "$BASE_PORT" "$p" "$C_OFF"
  fi
fi
if [ "$DEMO" = 0 ] && [ "$CLEAN_ONLY" = 0 ]; then
  # Which compose files to use: always the base file; the sound file only when this device has sound hardware (otherwise the container
  # would fail to start on the missing /dev/snd and could only ever work with remote speakers); plus the user's own docker-compose.local.yml.
  # Stored in .env (COMPOSE_FILE) so plain `docker compose ...` commands in this folder use the same set.
  files="docker-compose.yml"
  if [ -e "${ALARMPLUS_SND_DEVICE:-/dev/snd}" ]; then files="$files:docker-compose.sound.yml"
  else printf '  %sNo sound hardware found on this device: Alarm+ will play on remote (moOde) speakers only. Details: docs/docker.md%s\n' "$C_DIM" "$C_OFF"; fi
  [ -f docker-compose.local.yml ] && files="$files:docker-compose.local.yml"
  envfile="${ALARMPLUS_ENV_FILE:-$ROOT/.env}"
  { grep -v '^COMPOSE_FILE=' "$envfile" 2>/dev/null; echo "COMPOSE_FILE=$files"; } >"$envfile.tmp" && mv "$envfile.tmp" "$envfile"
  export COMPOSE_FILE="$files"
fi

[ "$DEMO" = 0 ] && detect_dc
if [ "$CLEAN_ONLY" = 0 ]; then
  if [ "$DEMO" = 1 ]; then ui_run "Build the image" hint_docker demo_build || exit 1; else ui_run "Build the image" hint_docker build_image || exit 1; fi
  if [ "$START" = 1 ]; then
    if [ "$DEMO" = 1 ]; then ui_run "Start the container" hint_plain demo_start || exit 1; else ui_run "Start the container" hint_plain start_container || exit 1; fi
    if [ "$DEMO" = 1 ]; then ui_run "Wait until it is healthy" hint_health demo_health || exit 1; else ui_run "Wait until it is healthy" hint_health wait_healthy || exit 1; fi
  fi
fi
if [ "$CLEAN" = 1 ]; then
  if [ "$DEMO" = 1 ]; then ui_run "Clean the Docker build cache" hint_clean demo_clean || exit 1; else ui_run "Clean the Docker build cache" hint_clean clean_cache || exit 1; fi
fi

if [ "$CLEAN_ONLY" = 1 ]; then
  printf '\n%s%s Docker caches cleaned%s  (your Alarm+ container and data were not touched; the next build downloads again, a few minutes)\n' "$C_OK" "$M_OK" "$C_OFF"
  exit 0
fi

IMG_SIZE=0
[ "$DEMO" = 0 ] && IMG_SIZE=$($DC image inspect alarmplus:latest --format '{{.Size}}' 2>/dev/null || echo 0)
HOSTIP=$(hostname -I 2>/dev/null | awk '{print $1}')
PORT=$BASE_PORT
[ "$START" = 1 ] && [ "$DEMO" = 0 ] && PORT=$($DC exec alarmplus python -c "from alarmplus import config; print(config.load('/state/config.toml').server.port)" 2>/dev/null || echo "$BASE_PORT")
printf '\n%s%s Alarm+ %s%s  (%s, image %s)\n' "$C_OK" "$M_OK" "$([ "$START" = 1 ] && echo "is running" || echo "is built; start it with: docker compose up -d")" "$C_OFF" "$(ui_hms $((SECONDS - T_ALL)))" "$(ui_hsize "${IMG_SIZE:-0}")"
if [ "$START" = 1 ]; then
  printf '  Open:  http://%s:%s   (or http://%s.local:%s)\n' "${HOSTIP:-<this-device>}" "$PORT" "$(hostname)" "$PORT"
  printf '  Then finish the setup in the web UI, Settings: camera address, timezone, access token, sound.\n'
fi
if [ "$CLEAN" = 0 ] && [ "$DEMO" = 0 ]; then # tell the user when the build left a lot of cache behind (it is not needed to run Alarm+)
  cache=$($DC system df --format '{{.Type}}|{{.Reclaimable}}' 2>/dev/null | awk -F'|' '$1=="Build Cache"{print $2}')
  case "$cache" in "" | 0B*) ;; *) printf '  Disk:    Docker is holding %s of build cache. Free it with:  bash install.sh --clean-only  (the next build downloads again)\n' "${cache%% *}" ;; esac
fi
printf '  Logs:    docker compose logs -f alarmplus        (and System & debug in the web UI)\n'
printf '  Update:  git pull && bash install.sh\n'
printf '  Remove:  bash uninstall.sh\n'
