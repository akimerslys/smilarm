#!/usr/bin/env bash
# Install or update Smilarm with Docker (the recommended way). Normally run through `bash install.sh`.
#
#   bash install.sh                    get the image (pull, or build from this clone), start the container, wait until it is healthy
#   bash install.sh --build            build the image from this clone even if a prebuilt one can be pulled
#   bash install.sh --no-start         install everything but do not start it
#   bash install.sh --no-migrate       do not move data over from an old "Alarm+" install
#   bash install.sh --clean-cache      afterwards free Docker's build cache (a later build downloads again)
#   bash install.sh --clean-only       just free the build cache and old Smilarm images; install nothing
#   bash install.sh --install-docker   install Docker first with Docker's official script (https://get.docker.com), if it is missing
#   bash install.sh --plain            no animation (for logs)
#   bash install.sh --demo             simulate the whole run (nothing is pulled, built or started)
#
# The installer copies what it needs into ~/.smilarm (override: SMILARM_HOME), including the `smilarm` command, so the folder you
# cloned can be deleted afterwards. Re-running it updates; your config, alarms and logs live in the `smilarm-state` volume.
set -uo pipefail
HERE="$(cd "$(dirname "${BASH_SOURCE[0]:-$0}")" && pwd)"
SRC="$(cd "$HERE/.." && pwd)"
# shellcheck source=scripts/lib/common.sh
. "$HERE/lib/common.sh"
# shellcheck source=scripts/lib/ui.sh
. "$HERE/lib/ui.sh"

START=1 BUILD=0 MIGRATE=1 PLAIN=0 DEMO=0 GET_DOCKER=0 CLEAN=0 CLEAN_ONLY=0
for a in "$@"; do
  case "$a" in
    --no-start) START=0 ;;
    --build) BUILD=1 ;;
    --no-migrate) MIGRATE=0 ;;
    --clean-cache) CLEAN=1 ;;
    --clean-only) CLEAN=1 CLEAN_ONLY=1 ;;
    --plain) PLAIN=1 ;;
    --demo) DEMO=1 ;;
    --install-docker) GET_DOCKER=1 ;;
    -h | --help) sed -n '2,15p' "$0"; exit 0 ;;
    *) echo "unknown option: $a (see --help)"; exit 2 ;;
  esac
done
[ "${SMILARM_SKIP_MIGRATION:-0}" = 1 ] && MIGRATE=0

mkdir -p "$SMILARM_HOME"
ui_init "$SMILARM_HOME/install.log" "$PLAIN"
trap 'printf "\n"; exit 130' INT
DEMO_DELAY=${DEMO_DELAY:-0.25}
BASE_PORT=${SMILARM_DEFAULT_PORT:-8080}
REF_FILE="$SMILARM_HOME/.image-ref"

# ---------------------------------------------------------------- steps (one function each)
docker_ok() { $DC info >/dev/null 2>&1; }

check_docker() {
  if ! command -v docker >/dev/null 2>&1; then
    if [ "$GET_DOCKER" = 1 ]; then
      echo "Docker is missing: installing it with the official script from get.docker.com"
      curl -fsSL https://get.docker.com | sudo sh || return 1
      sudo usermod -aG docker "$USER" 2>/dev/null || true
      DC="sudo docker"
    else
      echo "Docker is not installed."
      echo "Install it from https://docs.docker.com/engine/install/ (Raspberry Pi OS: https://docs.docker.com/engine/install/debian/),"
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

build_local() { $DC build --progress=plain -t "$LOCAL_IMAGE" "$SRC" && echo "$LOCAL_IMAGE" >"$REF_FILE"; }

# Prefer the prebuilt image; fall back to building from the sources next to this script (the only option before the project is published).
get_image() {
  local ref="$SMILARM_IMAGE_REPO:$SMILARM_TAG"
  if [ "$BUILD" = 0 ] && is_published; then
    echo "pulling $ref"
    if $DC pull "$ref"; then echo "$ref" >"$REF_FILE"; return 0; fi
    echo "could not pull $ref: building from the sources instead"
  elif [ "$BUILD" = 0 ]; then
    echo "no prebuilt image is published yet (SMILARM_REPO is not set): building from the sources"
  fi
  [ -f "$SRC/Dockerfile" ] || { echo "no Dockerfile next to this script: cannot build"; return 1; }
  build_local
}

# Data from an installation made when the project was still called Alarm+: copy it into the new volume. The old volume stays untouched.
legacy_present() { $DC volume ls -q 2>/dev/null | grep -qx "$LEGACY_VOLUME" && ! $DC volume ls -q 2>/dev/null | grep -qx "$VOLUME"; }
migrate_legacy() {
  local ref; ref=$(cat "$REF_FILE")
  if $DC ps -a --format '{{.Names}}' | grep -qx "$LEGACY_CONTAINER"; then
    echo "stopping the old container $LEGACY_CONTAINER"
    $DC stop "$LEGACY_CONTAINER" >/dev/null && $DC rm "$LEGACY_CONTAINER" >/dev/null
  fi
  $DC volume create "$VOLUME" >/dev/null || return 1
  $DC run --rm --user 0 -v "$LEGACY_VOLUME":/from:ro -v "$VOLUME":/to --entrypoint sh "$ref" \
    -c 'cp -a /from/. /to/ && chown -R 1000:1000 /to' || return 1
  echo "copied your config and alarms from $LEGACY_VOLUME to $VOLUME (the old volume is kept; remove it later: docker volume rm $LEGACY_VOLUME)"
}

clean_cache() {
  echo "removing ALL unused Docker build cache on this machine (images, containers and volumes are not touched) ..."
  $DC builder prune -af || return 1
  echo "removing old untagged Smilarm images left behind by updates ..."
  $DC image prune -f --filter label=app=smilarm || return 1
}
# "Total reclaimed space: 1.2GB" (image prune) / "Total:  3.1GB" (builder prune) -> "3.1GB + 1.2GB freed"
hint_clean() {
  if [ "$1" = final ]; then
    local sizes
    sizes=$(grep -hE '^Total' "$2" | sed -E 's/^Total( reclaimed space)?:[[:space:]]*//' | paste -sd'+' | sed 's/+/ + /g')
    if [ -n "$sizes" ]; then echo "freed $sizes (build cache + old images)"; else echo "nothing to clean"; fi
  else tail -n 1 "$2"; fi
}

start_container() { cd "$SMILARM_HOME/compose" && $DC compose up -d --remove-orphans; }

wait_healthy() { # the image has a HEALTHCHECK; give it up to ~100 s (MediaPipe loads on first start)
  local i st
  for i in $(seq 1 50); do
    st=$($DC inspect --format '{{if .State.Health}}{{.State.Health.Status}}{{else}}{{.State.Status}}{{end}}' "$CONTAINER" 2>/dev/null || echo "missing")
    case "$st" in
      healthy | running) echo "container is $st after $((i * 2))s"; return 0 ;;
      exited | dead | missing) break ;;
    esac
    echo "waiting for the container ($st)…"
    sleep 2
  done
  echo "the container did not become healthy. Last log lines:"
  $DC logs --tail 25 "$CONTAINER" 2>&1
  return 1
}
hint_health() { tail -n 1 "$2"; }

# ---------------------------------------------------------------- --demo: fake versions of the same steps
demo_line() { printf '%s\n' "$1"; sleep "$DEMO_DELAY"; }
demo_check() { demo_line "Docker 27.3.1, 2.29.7"; }
demo_image() {
  demo_line "no prebuilt image is published yet (SMILARM_REPO is not set): building from the sources"
  demo_line "#5 [2/9] RUN apt-get update && apt-get install -y mpv alsa-utils ..."
  demo_line "#5 4.1 Get:1 http://deb.debian.org/debian trixie/main arm64 mpv arm64 0.40.0-3 [1,266 kB]"
  demo_line "#5 5.2 Get:2 http://deb.debian.org/debian trixie/main arm64 libgles2 arm64 1.7.0-1 [18.0 kB]"
  demo_line "#7 [4/9] RUN pip install -r requirements.txt"
  demo_line "#7 9.0 Collecting opencv-contrib-python<6,>=4.9"
  demo_line "#7 10.1   Downloading opencv_contrib_python-5.0.0.93-cp37-abi3-manylinux_2_28_aarch64.whl (69.8 MB)"
  demo_line "#7 14.3 Collecting mediapipe>=0.10.14"
  demo_line "#7 15.0   Downloading mediapipe-0.10.21-cp313-cp313-manylinux_2_28_aarch64.whl (35.6 MB)"
  demo_line "#12 exporting to image"
  demo_line "#12 DONE 0.0s"
}
demo_migrate() { demo_line "stopping the old container alarmplus"; demo_line "copied your config and alarms from alarmplus-state to smilarm-state"; }
demo_start() { demo_line "Container smilarm  Started"; }
demo_health() { demo_line "container is healthy after 6s"; }
demo_clean() { demo_line "Deleted build cache objects: 41"; demo_line "Total:  3.132GB"; demo_line "Total reclaimed space: 1.58GB"; }

run_step() { # run_step "Title" hint real_fn demo_fn
  if [ "$DEMO" = 1 ]; then ui_run "$1" "$2" "$4" || exit 1; else ui_run "$1" "$2" "$3" || exit 1; fi
}

# ---------------------------------------------------------------- go
ui_title "Smilarm installer (Docker)"
[ "$DEMO" = 1 ] && printf '%s(demo mode: nothing is pulled, built or started)%s\n' "$C_DIM" "$C_OFF"
printf '\n'

DO_MIGRATE=0
[ "$DEMO" = 1 ] && [ "$MIGRATE" = 1 ] && [ "${SMILARM_DEMO_LEGACY:-0}" = 1 ] && DO_MIGRATE=1

steps=(check)
if [ "$CLEAN_ONLY" = 0 ]; then
  steps+=(image)
  if [ "$DEMO" = 0 ] && [ "$MIGRATE" = 1 ]; then # decided before the plan is printed, so the step count is right
    detect_dc
    legacy_present && DO_MIGRATE=1
  fi
  [ "$DO_MIGRATE" = 1 ] && steps+=(migrate)
  [ "$START" = 1 ] && steps+=(start health)
fi
[ "$CLEAN" = 1 ] && steps+=(clean)
ui_plan "${steps[@]}"
T_ALL=$SECONDS

run_step "Check Docker" hint_plain check_docker demo_check
[ "$DEMO" = 0 ] && detect_dc

if [ "$CLEAN_ONLY" = 0 ]; then
  if [ "$DEMO" = 0 ]; then
    install_home "$SRC" docker
    # First install on a Pi where 8080 is taken (qBittorrent and many others use it): start on the next free port instead of crash-looping.
    # A migrated install keeps its own port. Later the port is a normal setting in the UI.
    if [ "$DO_MIGRATE" = 0 ] && ! $DC volume ls -q 2>/dev/null | grep -qx "$VOLUME" && port_busy "$BASE_PORT"; then
      p=$((BASE_PORT + 1))
      while port_busy "$p" && [ "$p" -lt $((BASE_PORT + 50)) ]; do p=$((p + 1)); done
      export SMILARM_PORT=$p
      printf '  %sPort %s is already in use by another program: Smilarm will use port %s (change it any time in Settings).%s\n' "$C_DIM" "$BASE_PORT" "$p" "$C_OFF"
    fi
  fi

  run_step "Get the image" hint_docker get_image demo_image

  if [ "$DEMO" = 0 ]; then
    # which compose files to use: always the base one; the sound file only when this device has sound hardware (otherwise the container
    # would fail to start on the missing /dev/snd); the user's own docker-compose.local.yml when it exists
    envfile="$SMILARM_HOME/compose/.env"
    files="docker-compose.yml"
    if [ -e "${SMILARM_SND_DEVICE:-/dev/snd}" ]; then files="$files:docker-compose.sound.yml"
    else printf '  %sNo sound hardware found on this device: Smilarm will play on remote (moOde) speakers only. See docs/hardware.md%s\n' "$C_DIM" "$C_OFF"; fi
    [ -f "$SMILARM_HOME/compose/docker-compose.local.yml" ] && files="$files:docker-compose.local.yml"
    : >>"$envfile"
    set_env_var "$envfile" COMPOSE_FILE "$files"
    set_env_var "$envfile" SMILARM_IMAGE "$(cat "$REF_FILE")"
  fi

  [ "$DO_MIGRATE" = 1 ] && run_step "Move your data from Alarm+" hint_plain migrate_legacy demo_migrate
  if [ "$START" = 1 ]; then
    run_step "Start the container" hint_plain start_container demo_start
    run_step "Wait until it is healthy" hint_health wait_healthy demo_health
  fi
fi
[ "$CLEAN" = 1 ] && run_step "Clean the Docker build cache" hint_clean clean_cache demo_clean

if [ "$CLEAN_ONLY" = 1 ]; then
  printf '\n%s%s Docker caches cleaned%s  (your Smilarm container and data were not touched; the next build downloads again, a few minutes)\n' "$C_OK" "$M_OK" "$C_OFF"
  exit 0
fi

IMG_SIZE=0
[ "$DEMO" = 0 ] && IMG_SIZE=$($DC image inspect "$(cat "$REF_FILE")" --format '{{.Size}}' 2>/dev/null || echo 0)
HOSTIP=$(hostname -I 2>/dev/null | awk '{print $1}')
PORT=$BASE_PORT
[ "$START" = 1 ] && [ "$DEMO" = 0 ] && PORT=$($DC exec "$CONTAINER" python -c "from smilarm import config; print(config.load('/state/config.toml').server.port)" 2>/dev/null || echo "$BASE_PORT")
printf '\n%s%s Smilarm %s%s  (%s, image %s)\n' "$C_OK" "$M_OK" "$([ "$START" = 1 ] && echo "is running" || echo "is installed; start it with: smilarm start")" "$C_OFF" "$(ui_hms $((SECONDS - T_ALL)))" "$(ui_hsize "${IMG_SIZE:-0}")"
if [ "$START" = 1 ]; then
  printf '  Open:     http://%s:%s   (or http://%s.local:%s)\n' "${HOSTIP:-<this-device>}" "$PORT" "$(hostname)" "$PORT"
  printf '  Then finish the setup in the web UI, Settings: camera address, timezone, access token, sound.\n'
fi
printf '  Manage:   smilarm status | logs | restart | update | backup | uninstall        (smilarm help)\n'
in_path || printf '  Note:     add %s to your PATH to use the `smilarm` command:  export PATH="%s:$PATH"\n' "$SMILARM_BIN_DIR" "$SMILARM_BIN_DIR"
printf '  The folder you cloned is no longer needed: Smilarm lives in Docker and %s. You may delete it.\n' "$SMILARM_HOME"
if [ "$CLEAN" = 0 ] && [ "$DEMO" = 0 ]; then # tell the user when the build left a lot of cache behind (it is not needed to run Smilarm)
  cache=$($DC system df --format '{{.Type}}|{{.Reclaimable}}' 2>/dev/null | awk -F'|' '$1=="Build Cache"{print $2}')
  val=${cache%% *}; num=${val%%[A-Za-z]*}; unit=${val##*[0-9.]}
  case "$unit" in # only worth mentioning from about half a gigabyte up
    GB | TB) show=1 ;;
    MB) show=$(awk -v n="${num:-0}" 'BEGIN{print (n >= 500) ? 1 : 0}') ;;
    *) show=0 ;;
  esac
  if [ "${show:-0}" = 1 ]; then printf '  Disk:     Docker is holding %s of build cache. Free it with:  smilarm clean\n' "$val"; fi
fi
