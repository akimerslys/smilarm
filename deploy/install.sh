#!/usr/bin/env bash
# Install Alarm+ on Raspberry Pi OS (Bookworm, 64-bit). Run from the project root as the normal user.
set -euo pipefail
ROOT="$(cd "$(dirname "$0")/.." && pwd)"
cd "$ROOT"

sudo apt-get install -y --no-install-recommends python3-venv mpv
python3 -m venv .venv
.venv/bin/pip install -q -r requirements.txt

mkdir -p data
[ -f data/face_landmarker.task ] || curl -fL -o data/face_landmarker.task \
  https://storage.googleapis.com/mediapipe-models/face_landmarker/face_landmarker/float16/1/face_landmarker.task
[ -f config.toml ] || cp config.example.toml config.toml

sed "s|@ROOT@|$ROOT|g; s|@USER@|$USER|g" deploy/alarmplus.service | sudo tee /etc/systemd/system/alarmplus.service >/dev/null
sudo systemctl daemon-reload
sudo systemctl enable --now alarmplus
echo "Done: http://$(hostname).local:8080  (edit $ROOT/config.toml, then: sudo systemctl restart alarmplus)"
