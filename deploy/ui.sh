#!/usr/bin/env bash
# Terminal UI helpers for deploy/install.sh: numbered steps, spinner, live "what is being downloaded and how
# much" hints, a progress bar for single-file downloads, and a plain (no animation) mode for logs/CI.
# Meant to be sourced. Sizes are shown 1000-based (kB/MB/GB), like pip does.

UI_ANIM=0 STEP_N=0 STEP_TOTAL=0 UI_LOG=/dev/null STEP_LOG=/dev/null

# ui_init LOGFILE [PLAIN=0|1]
ui_init() {
  UI_LOG=${1:-/dev/null}
  if ! : >"$UI_LOG" 2>/dev/null; then UI_LOG=/dev/null; fi
  STEP_LOG=$(mktemp 2>/dev/null || echo /tmp/alarmplus-step.log)
  if { [ -t 1 ] || [ "${UI_FORCE_ANIM:-0}" = 1 ]; } && [ "${2:-0}" != 1 ] && [ "${TERM:-dumb}" != dumb ]; then UI_ANIM=1; fi
  if [ "$UI_ANIM" = 1 ] && [ -z "${NO_COLOR:-}" ]; then
    C_OK=$'\033[32m' C_ERR=$'\033[31m' C_DIM=$'\033[2m' C_BOLD=$'\033[1m' C_CY=$'\033[36m' C_OFF=$'\033[0m'
  else
    C_OK= C_ERR= C_DIM= C_BOLD= C_CY= C_OFF=
  fi
  case "${LC_ALL:-${LC_CTYPE:-${LANG:-}}}" in
    *UTF-8* | *utf8* | *UTF8*)
      FRAMES=(⠋ ⠙ ⠹ ⠸ ⠼ ⠴ ⠦ ⠧ ⠇ ⠏) M_OK="✔" M_ERR="✘" BAR_ON="█" BAR_OFF="░" ;;
    *)
      FRAMES=('|' '/' '-' '\') M_OK="OK" M_ERR="FAIL" BAR_ON="#" BAR_OFF="-" ;;
  esac
}

ui_hms() { printf '%02d:%02d' $(($1 / 60)) $(($1 % 60)); }

# bytes -> "3.8 MB"
ui_hsize() {
  awk -v b="${1:-0}" 'BEGIN{ if (b>=1e9) printf "%.1f GB", b/1e9; else if (b>=1e6) printf "%.1f MB", b/1e6;
                             else if (b>=1e3) printf "%.0f kB", b/1e3; else printf "%d B", b }'
}

# ui_bar PERCENT [WIDTH]  ->  "█████░░░░░"
ui_bar() {
  local pct=${1:-0} w=${2:-24} on off i out=""
  [ "$pct" -gt 100 ] && pct=100
  on=$((pct * w / 100)) off=$((w - on))
  for ((i = 0; i < on; i++)); do out+=$BAR_ON; done
  for ((i = 0; i < off; i++)); do out+=$BAR_OFF; done
  printf '%s' "$out"
}

ui_cols() { local c=${COLUMNS:-$(tput cols 2>/dev/null || echo 80)}; echo "${c:-80}"; }

ui_trunc() { # ui_trunc WIDTH TEXT
  local n=$1 s=$2
  if [ "${#s}" -gt "$n" ] && [ "$n" -gt 4 ]; then s="${s:0:$((n - 3))}..."; fi
  printf '%s' "$s"
}

# ---- log parsers (pure functions of a log file; unit tested) -------------------------------------

# "bytes count" of the sizes in pip's "Downloading <file> (35.9 MB)" lines
ui_sum_pip() {
  grep -E 'Downloading .*\([0-9.]+ (kB|MB|GB)\)' "$1" 2>/dev/null |
    sed -E 's/.*\(([0-9.]+) (kB|MB|GB)\).*/\1 \2/' |
    awk 'BEGIN{b=0;n=0} {u=($2=="GB")?1e9:(($2=="MB")?1e6:1e3); b+=$1*u; n++} END{printf "%d %d", b, n}'
}

# "bytes count" of apt's "Get:N ... [1,234 kB]" lines
ui_sum_apt() {
  grep -E '^Get:[0-9]+ ' "$1" 2>/dev/null |
    sed -nE 's/.*\[([0-9,.]+) ?(B|kB|MB|GB)\]$/\1 \2/p' | tr -d , |
    awk 'BEGIN{b=0;n=0} {u=($2=="GB")?1e9:(($2=="MB")?1e6:(($2=="kB")?1e3:1)); b+=$1*u; n++} END{printf "%d %d", b, n}'
}

# hint_pip live|final FILE
hint_pip() {
  local b n last name size so_far=""
  read -r b n < <(ui_sum_pip "$2")
  [ "${n:-0}" -gt 0 ] && so_far=" · $(ui_hsize "$b") in $n file(s)"
  if [ "$1" = final ]; then
    if [ "${n:-0}" -gt 0 ]; then echo "$(ui_hsize "$b") downloaded in $n file(s)"; else echo "nothing to download (cached or already installed)"; fi
    return
  fi
  last=$(grep -E '^ *(Collecting|Downloading|Using cached|Installing collected|Successfully|Requirement already)' "$2" 2>/dev/null | tail -1 | sed 's/^ *//')
  case "$last" in
    Downloading*)
      name=$(sed -E 's#^Downloading ([^ ]*/)?([^ /]+) .*#\2#; s#^([A-Za-z0-9_.]+-[0-9][^-]*)-.*#\1#' <<<"$last") # package-version only
      size=$(sed -nE 's/.*\(([0-9.]+ (kB|MB|GB))\).*/\1/p' <<<"$last")
      echo "downloading $name (${size:-?})$so_far" ;;
    "Using cached"*) echo "cached: $(sed -E 's#^Using cached ([^ ]*/)?([^ /]+).*#\2#' <<<"$last")$so_far" ;;
    Collecting*) echo "looking up ${last#Collecting }$so_far" ;;
    "Installing collected"*) echo "installing the downloaded libraries$so_far" ;;
    *) echo "finishing up…" ;;
  esac
}

# hint_apt live|final FILE
hint_apt() {
  local b n last name size
  read -r b n < <(ui_sum_apt "$2")
  if [ "$1" = final ]; then
    if [ "${n:-0}" -gt 0 ]; then echo "$n package(s), $(ui_hsize "$b") downloaded"; else echo "already installed"; fi
    return
  fi
  last=$(grep -E '^(Get:|Unpacking |Setting up )' "$2" 2>/dev/null | tail -1)
  case "$last" in
    Get:*)
      name=$(awk '{print $5}' <<<"$last")
      size=$(sed -nE 's/.*\[([^]]+)\]$/\1/p' <<<"$last")
      echo "downloading $name (${size:-?}) · $n of ${APT_PLAN_N:-?}, $(ui_hsize "$b") so far" ;;
    Unpacking*) echo "unpacking $(awk '{print $2}' <<<"$last")" ;;
    "Setting up"*) echo "configuring $(awk '{print $3}' <<<"$last")" ;;
    *) echo "contacting package servers…" ;;
  esac
}


# last line of the step output as the summary, nothing while running
hint_plain() { if [ "$1" = final ]; then tail -n 1 "$2"; fi; }

# ---- steps --------------------------------------------------------------------------------------

ui_plan() { STEP_TOTAL=$#; STEP_N=0; }

ui_title() { printf '%s%s%s\n' "$C_BOLD" "$1" "$C_OFF"; }

_ui_done() { # title elapsed detail
  local d=""; [ -n "$3" ] && d="  ${C_DIM}$3${C_OFF}"
  if [ "$UI_ANIM" = 1 ]; then printf '\r\033[K'; fi
  printf '  %s%s%s [%d/%d] %s %s(%s)%s%s\n' "$C_OK" "$M_OK" "$C_OFF" "$STEP_N" "$STEP_TOTAL" "$1" "$C_DIM" "$(ui_hms "$2")" "$C_OFF" "$d"
}

_ui_fail() { # title
  if [ "$UI_ANIM" = 1 ]; then printf '\r\033[K'; fi
  printf '  %s%s%s [%d/%d] %s %sFAILED%s\n' "$C_ERR" "$M_ERR" "$C_OFF" "$STEP_N" "$STEP_TOTAL" "$1" "$C_ERR" "$C_OFF"
  printf '%s--- last output ---%s\n' "$C_DIM" "$C_OFF"
  tail -n 20 "$STEP_LOG"
  printf '%s-------------------%s\nFull log: %s\n' "$C_DIM" "$C_OFF" "$UI_LOG"
}

# ui_skip TITLE REASON: a step that needs no work
ui_skip() {
  STEP_N=$((STEP_N + 1))
  _ui_done "$1" 0 "$2"
}

# ui_note TITLE: header for an interactive step (no spinner; the command owns the terminal)
ui_note() {
  STEP_N=$((STEP_N + 1))
  printf '  %s>%s [%d/%d] %s\n' "$C_CY" "$C_OFF" "$STEP_N" "$STEP_TOTAL" "$1"
}

# ui_run TITLE HINT_FN CMD...  — run CMD in the background, animate until it exits
ui_run() {
  local title=$1 hint=$2 pid i=0 t0=$SECONDS rc live=""
  shift 2
  STEP_N=$((STEP_N + 1))
  : >"$STEP_LOG"
  "$@" >"$STEP_LOG" 2>&1 </dev/null &
  pid=$!
  if [ "$UI_ANIM" = 1 ]; then
    while kill -0 "$pid" 2>/dev/null; do
      [ $((i % 2)) -eq 0 ] && live=$("$hint" live "$STEP_LOG" 2>/dev/null)
      local left="  ${FRAMES[$((i % ${#FRAMES[@]}))]} [$STEP_N/$STEP_TOTAL] $title  $(ui_hms $((SECONDS - t0)))  "
      printf '\r\033[K%s%s%s%s%s' "$C_CY" "$left" "$C_DIM" "$(ui_trunc $(($(ui_cols) - ${#left} - 1)) "$live")" "$C_OFF"
      i=$((i + 1))
      sleep 0.1
    done
  else
    printf '  [%d/%d] %s ...\n' "$STEP_N" "$STEP_TOTAL" "$title"
  fi
  wait "$pid"
  rc=$?
  cat "$STEP_LOG" >>"$UI_LOG"
  if [ "$rc" -eq 0 ]; then
    _ui_done "$title" $((SECONDS - t0)) "$("$hint" final "$STEP_LOG" 2>/dev/null)"
  else
    _ui_fail "$title"
  fi
  return "$rc"
}

# ui_download TITLE URL DEST — single file with a real progress bar (size, percent, speed, ETA)
ui_download() {
  local title=$1 url=$2 dest=$3 total pid ticks=0 size pct speed eta t0=$SECONDS rc
  STEP_N=$((STEP_N + 1))
  if [ -s "$dest" ]; then
    _ui_done "$title" 0 "already downloaded ($(ui_hsize "$(stat -c%s "$dest")"))"
    return 0
  fi
  total=$(curl -fsSIL "$url" 2>/dev/null | tr -d '\r' | awk 'tolower($1)=="content-length:"{v=$2} END{print v+0}')
  rm -f "$dest.part"
  : >"$STEP_LOG"
  curl -fsSL -o "$dest.part" "$url" 2>"$STEP_LOG" &
  pid=$!
  if [ "$UI_ANIM" = 1 ]; then
    while kill -0 "$pid" 2>/dev/null; do
      size=$(stat -c%s "$dest.part" 2>/dev/null || echo 0)
      speed=$((ticks > 0 ? size * 10 / ticks : 0))
      if [ "${total:-0}" -gt 0 ]; then
        pct=$((size * 100 / total))
        eta=$((speed > 0 ? (total - size) / speed : 0))
        printf '\r\033[K%s  %s [%d/%d] %s%s  %s %3d%%  %s / %s  %s/s  ETA %ss%s' "$C_CY" "${FRAMES[$((ticks % ${#FRAMES[@]}))]}" \
          "$STEP_N" "$STEP_TOTAL" "$title" "$C_OFF" "$(ui_bar "$pct")" "$pct" "$(ui_hsize "$size")" "$(ui_hsize "$total")" "$(ui_hsize "$speed")" "$eta" ""
      else
        printf '\r\033[K%s  %s [%d/%d] %s  %s so far%s' "$C_CY" "${FRAMES[$((ticks % ${#FRAMES[@]}))]}" "$STEP_N" "$STEP_TOTAL" "$title" "$(ui_hsize "$size")" "$C_OFF"
      fi
      ticks=$((ticks + 1))
      sleep 0.1
    done
  else
    printf '  [%d/%d] %s (%s) ...\n' "$STEP_N" "$STEP_TOTAL" "$title" "$(ui_hsize "${total:-0}")"
  fi
  wait "$pid"
  rc=$?
  cat "$STEP_LOG" >>"$UI_LOG"
  if [ "$rc" -eq 0 ] && [ -s "$dest.part" ]; then
    mv "$dest.part" "$dest"
    size=$(stat -c%s "$dest")
    _ui_done "$title" $((SECONDS - t0)) "$(ui_hsize "$size") downloaded"
  else
    rm -f "$dest.part"
    _ui_fail "$title"
    return 1
  fi
}
