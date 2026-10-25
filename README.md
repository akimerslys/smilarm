# Alarm+ — an alarm clock you switch off with a smile

The alarm keeps ringing until you smile at the camera for 2–3 seconds in a row.

```
ESP32-CAM ──MJPEG──▶ Rpi5: alarmplus (schedule + smile detection + sound + web UI + REST API)
                          │ optional
                          ├──MPD:6600──▶ Rpi3 (moOde + speakers)
                          ├──shell hook─▶ your script (e.g. ssh rpi3 ~/after_alarm.sh)
                          └──webhook / REST──▶ OpenClaw
```

**The core runs on a single device (the Rpi5).** Everything else is pluggable:
- **Camera:** an ESP32-CAM, or a USB/Pi camera attached to the Rpi5 (`camera.source = "0"`, no ESP32 needed).
- **Sound:** plays on the Rpi5 by default. A second Pi with moOde is an optional extra; if it is unreachable the alarm
  falls back to the Rpi5. A missing sound file or a dead audio device falls back to the built-in tone, so the alarm is never silent.

## Contents
[Features](#features) · [Install](#installation) · [Web UI](#web-ui) · [Configuration](#configuration) · [Optional parts](#optional-parts) ·
[Safety](#safety) · [Troubleshooting](#troubleshooting) · [Uninstall](#uninstall) · [Development](#development) · [Docs](#documentation)

## Features
- Smile detection with MediaPipe Face Landmarker (`mouthSmile` blendshapes, ≈10 ms/frame on a Pi 5); automatic fallback to OpenCV Haar.
- Runs only while ringing: the camera is opened and the detector works **only during an alarm** (~0 % CPU otherwise, and the
  ESP32 doesn't encode frames without a client). The scheduler sleeps until the next alarm.
- Camera presets `low` 320×240 (default) / `medium` 640×480 / `high` 800×600, `camera.rotate` for sideways mounting, and an optional
  **flashlight** (the ESP32's white LED lights the room while the alarm rings or the camera is being tested).
- Weekday schedules, one-shot alarms, smooth volume ramp, random sound from a folder.
- Web UI with live camera view, a **Smile test** with debug numbers, system health, a live log and validated **Settings**.
- Failsafes: a dead camera unlocks a manual stop, the alarm ends after `max_minutes`, failures in the detector, the webhook or
  a hook never stop the sound.
- Hooks (shell commands on ring/dismiss), webhook events, and an OpenClaw skill.

## Installation
**1. ESP32-CAM** (PlatformIO):
```bash
cd firmware/esp32cam/src && cp secrets.h.example secrets.h   # enter your Wi-Fi
cd .. && pio run -t upload
```
Then open `http://<camera-ip>:81/stream`. The Arduino IDE works too (put `main.cpp` and `secrets.h` in a sketch folder; board
"AI Thinker ESP32-CAM", PSRAM enabled). Reserve the camera's IP in your router and use the **IP address** in `camera.source`:
`.local` names often don't resolve on a Pi.

**2. Rpi5** (Raspberry Pi OS Bookworm, 64-bit):
```bash
git clone <repo> alarm+ && cd alarm+ && bash deploy/install.sh
```
The installer shows seven numbered steps with a spinner, what is downloading right now and how much (apt packages and Python
libraries with running totals; the face model with a progress bar, speed and ETA), and logs everything to `data/install.log`.
It generates `config.toml` with every default value (an existing one is never overwritten), opens it in `nano` (or `$EDITOR`) so you
can set `camera.source` and the rest, validates it (re-opening the editor if needed), and restarts the service.
Options: `--no-edit` skips the editor, `--plain` turns the animation off, `--demo` previews the whole UI with fake downloads and
installs nothing. Re-running it is safe.

Then open `http://<rpi5>.local:8080`.

## Web UI
- **Alarms:** add, enable/disable, delete. **Test ring** rings now and can always be stopped with **Stop alarm**.
- **Camera:** plain live view (for aiming). **Smile test:** live view with a green/red frame and a table of debug values (score,
  threshold, hold progress, inference ms, camera fps/state/error, …); also available as JSON at `/api/debug`.
- **System & debug:** CPU, memory, temperature, power/throttling (under-voltage!), load, disk, uptimes, reachability of the camera and
  moOde, package versions, last alarm result, last hook runs, the live log (filter: everything / warnings & errors) and **Test sound**.
- **Settings:** the options that are safe to change at runtime, each validated against its allowed range. **Save** edits `config.toml`
  in place (your comments and other lines stay; the previous version is kept as `config.toml.bak`) and applies the change live.
  **Reload file** re-reads `config.toml` after a hand edit; **Restore defaults** writes the built-in defaults back.
  Hooks and the webhook URL are editable here only when `server.token` is set.

## Configuration
`config.toml` is read at start; relative paths in it (`data_dir`, the model) are relative to the file, not to where you start the
program. After editing it by hand: `sudo systemctl restart alarmplus` (or **Reload file** in the UI for the runtime settings).
`python -m alarmplus --check-config config.toml` validates a file; `--write-config PATH` writes one with all defaults.

| Section | Highlights |
|---|---|
| `[server]` | `port`, `token` (set it on an untrusted network: every `/api` call then needs `Authorization: Bearer …` or `?token=`) |
| `[camera]` | `source`, `quality`, `rotate` (try 90, else 270, for a sideways camera; the detector needs upright faces), `flashlight` + `flashlight_level` (see below) |
| `[smile]` | `threshold` (lower = easier), `hold_seconds`, `fps` |
| `[audio]` | `backend` local/moode/both, volume ramp, `sound`, `sound_dir`, `device` |
| `[moode]` | `host`, `port`, `library_dir`, `url`, `fallback_local` |
| `[ring]` | `max_minutes`, `allow_manual_dismiss`, `camera_fail_seconds` |
| `[hooks]` / `[events]` | shell commands on ring/dismiss; webhook URL |
| `[logging]` | `level` DEBUG/INFO/WARNING (DEBUG adds per-second detection and camera numbers) |

## Optional parts
- **Second speaker with moOde** — [`docs/setup_moode_other_device.md`](docs/setup_moode_other_device.md). Set `audio.backend = "moode"`
  (or `"both"`) and `moode.host`. The Rpi3 either fetches the sound from the Rpi5 over HTTP (`/sound/current`), plays a random file
  from its own library (`moode.library_dir`), or plays your `moode.url`.
- **Flashlight** — `camera.flashlight = true` (or the Settings toggle) switches the ESP32's flash LED on whenever the camera is in use: while an
  alarm rings, in the **Camera** view and in the **Smile test**; it goes off when they end. `camera.flashlight_level` (percent, default 30) sets the
  brightness: the LED is very bright and warms up, so keep it modest for long alarms. Needs the current firmware (flash a sketch that accepts
  `/control?led=0..255&for=SECONDS`). Safety nets: the Pi refreshes the light every minute and the ESP32 switches it off by itself after 3 minutes
  without a refresh, so a crash or lost Wi-Fi cannot leave it on. Status and errors show in the Smile test table (`flashlight`) and the log.
  Off by default, and not available for USB/Pi cameras.
- **Your own sounds, output device, volume** — [`docs/sound_output.md`](docs/sound_output.md).
- **Run a script after the alarm** — [`docs/after_alarm_hooks.md`](docs/after_alarm_hooks.md): `hooks.on_ring` / `hooks.on_dismiss`
  run on the Rpi5 with `ALARM_*` variables; use `ssh` inside to act on another device.
- **OpenClaw** — `bash openclaw/install.sh` copies the skill to `~/.openclaw/workspace/skills/alarm-plus` (set `ALARM_URL` and, if
  you use a token, `ALARM_TOKEN` for OpenClaw). The skill documents the REST API (alarms, settings, diagnostics); events
  (`ringing`, `dismissed`) go to `events.webhook_url`. OpenClaw's skill folder and format were not verified against a real installation.

## Safety
- The smile is the only way to stop a real alarm. The **Stop alarm** button works for real alarms only if `ring.allow_manual_dismiss`
  is on (off by default), or when the camera has been silent for `camera_fail_seconds` or the detector cannot run.
- The alarm stops by itself after `ring.max_minutes`.
- A hook is a shell command your Pi runs, so hooks and the webhook URL are editable in the web UI only when `server.token` is set.
  The token is compared in constant time and is kept out of the logs.
- Everything on the web port is unencrypted HTTP: use it on a trusted LAN (or put a reverse proxy with HTTPS in front).

## Troubleshooting
| Symptom | Look at |
|---|---|
| No video / "camera error" | **System & debug → log** and the **Smile test** table: the cause is spelled out (wrong IP, connection refused, no answer, ESP32 busy with another viewer, `.local` not resolving). |
| Smile never counts | **Smile test:** is a face found? If not, try `camera.rotate`; if the score stays below the threshold, lower `smile.threshold`. |
| No sound | **Test sound**, then the log (`player exited at once …`, wrong device); see [`docs/sound_output.md`](docs/sound_output.md). |
| Nothing on the second speaker | `System & debug → reachability`, then [`docs/setup_moode_other_device.md`](docs/setup_moode_other_device.md). |
| Random freezes / reboots of the Pi or ESP32 | `throttled` in **System & debug**: under-voltage means a weak power supply. |
| Service won't start | `journalctl -u alarmplus -n 50 --no-pager`; the usual cause is a busy port (`sudo ss -ltnp \| grep :8080`). |

## Uninstall
```bash
bash deploy/uninstall.sh --dry-run     # preview
bash deploy/uninstall.sh               # interactive
```
Removes the service and `.venv`; asks before touching your alarms/`config.toml`, the OpenClaw skill and system packages
(`--purge` writes a backup tarball to your home folder first). The project folder itself is left for you to delete.
Full description: [`docs/uninstall.md`](docs/uninstall.md).

## Development
```bash
pip install -r requirements-dev.txt
python -m pytest tests
```
`config.example.toml` lists every parameter the program supports and is generated from the code (`python -m alarmplus --write-config config.example.toml --force`; a test fails if it goes stale).
The suite (≈120 tests) covers the schedule, the smile gate, the ring loop and its failure modes with fake camera/audio/detector, the REST
API, settings and in-place `config.toml` editing, the MPD client against a fake server, hooks, logging, the OpenClaw skill's consistency
with the API, and both shell scripts (animated and plain installer UI, uninstaller on a throw-away copy).
Layout: `alarmplus/` (Python package), `web/index.html` (UI), `firmware/esp32cam/` (ESP32 sketch), `deploy/` (install/uninstall),
`openclaw/` (skill), `docs/`, `tests/`.

## Documentation
- [`docs/setup_moode_other_device.md`](docs/setup_moode_other_device.md) — ring through a second Pi running moOde
- [`docs/sound_output.md`](docs/sound_output.md) — speakers, output device, your own sounds / a sound folder, volume
- [`docs/after_alarm_hooks.md`](docs/after_alarm_hooks.md) — scripts on ring/dismiss
- [`docs/uninstall.md`](docs/uninstall.md) — removing Alarm+

## Not verified on real hardware
Unit tests run on Windows, and an earlier version's suite also ran on a Raspberry Pi 5 (install, MediaPipe loading). Not yet verified end to end:
the full smile-to-dismiss flow, sound through `mpv` and the process-group stop test (POSIX only, skipped on Windows), the ESP32 firmware's
`/control` endpoint and trailing-boundary stream change (needs a reflash), and the Rpi3/moOde path. Tune `smile.threshold` to your lighting
with the **Smile test** view.
