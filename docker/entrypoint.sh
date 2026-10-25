#!/usr/bin/env bash
# Container entry point: make sure the state volume has a config and the face model, then run Alarm+.
# Everything else is configured in the web UI (Settings), which saves into /state/config.toml.
set -euo pipefail

STATE_DIR=${STATE_DIR:-/state}
MODEL_SRC=${MODEL_SRC:-/opt/models/face_landmarker.task}
PY=${ALARMPLUS_PY:-python}
RUN=${ALARMPLUS_EXEC:-python -m alarmplus}

mkdir -p "$STATE_DIR/data"

if [ ! -f "$STATE_DIR/config.toml" ]; then
  echo "[alarmplus] first start: writing $STATE_DIR/config.toml with all default values"
  # ALARMPLUS_PORT: set by install.sh when 8080 is already taken (host networking shares the Pi's ports)
  $PY -m alarmplus --write-config "$STATE_DIR/config.toml" ${ALARMPLUS_PORT:+--port "$ALARMPLUS_PORT"}
fi

if [ ! -s "$STATE_DIR/data/face_landmarker.task" ]; then
  if [ -s "$MODEL_SRC" ]; then
    cp "$MODEL_SRC" "$STATE_DIR/data/face_landmarker.task"
  else
    echo "[alarmplus] warning: no face model found; the simpler OpenCV detector will be used"
  fi
fi

echo "[alarmplus] starting; open the web UI and use Settings to finish the setup"
# shellcheck disable=SC2086
exec $RUN --config "$STATE_DIR/config.toml"
