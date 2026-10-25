# Alarm+ — an alarm clock you switch off with a smile

The alarm keeps ringing until you smile at the camera for 2–3 seconds in a row.

```
ESP32-CAM ──MJPEG──▶ Raspberry Pi: Alarm+ in Docker (schedule + smile detection + sound + web UI + REST API)
                          │ optional
                          ├──MPD:6600──▶ second Pi (moOde + speakers)
                          ├──shell hook─▶ your script (e.g. ssh to another device)
                          └──webhook / REST──▶ OpenClaw
```

**Everything runs in one Docker container on one device.** The camera and the second speaker are optional, pluggable parts.
You never edit a config file: after it starts, you set everything up in the web UI.

## Quick start
```bash
git clone <repo> alarm+ && cd alarm+
bash install.sh
```
That builds the image (showing what is downloading and how much), starts the container and waits until it is healthy.
Then open **`http://<your-pi>:8080`** and follow the **Setup** card at the top:
**timezone → camera address → access token → test the sound**. Details: [`docs/docker.md`](docs/docker.md).

- No Docker yet? `bash install.sh --install-docker` installs it with Docker's official script.
- Update: `git pull && bash install.sh` (your settings and alarms are kept in a Docker volume).
- Free disk space: builds leave a few GB of Docker cache behind. `bash install.sh --clean-cache` (or `--clean-only`) clears it; see [`docs/docker.md`](docs/docker.md#disk-space-cleaning-the-build-cache).
- Remove: `bash uninstall.sh` (asks before touching your data).
- Don't want Docker? There is an optional native install: `bash install.sh --native` ([`docs/native_install.md`](docs/native_install.md)).
- Preview the installer without installing anything: `bash install.sh --demo`.

## Features
- Smile detection with MediaPipe Face Landmarker (`mouthSmile` blendshapes, ≈10 ms/frame on a Pi 5); automatic fallback to OpenCV Haar.
- Runs only while ringing: the camera is opened and the detector works **only during an alarm** (~0 % CPU otherwise, and the ESP32
  doesn't encode frames without a client). The scheduler sleeps until the next alarm.
- Camera presets `low` 320×240 (default) / `medium` 640×480 / `high` 800×600, `rotate` for sideways mounting, and an optional
  **flashlight** (the ESP32's white LED lights the room while the alarm rings or the camera is being tested).
- Weekday schedules, one-shot alarms, smooth volume ramp, random sound from a folder, your own timezone.
- Web UI: live camera view, **Smile test** with debug numbers, system health, a live log, a first-run **Setup** checklist and validated
  **Settings** for every parameter.
- Failsafes: a dead camera unlocks a manual stop, the alarm ends after `max_minutes`, failures in the detector, the webhook or a hook never
  stop the sound, and a missing sound file or dead audio device falls back to the built-in tone.
- Hooks (shell commands on ring/dismiss), webhook events, and an OpenClaw skill.

## The ESP32-CAM
Flash `firmware/esp32cam/` with PlatformIO (or the sketch in `camera/alarmcam/` with the Arduino IDE; board "AI Thinker ESP32-CAM", PSRAM enabled):
```bash
cd firmware/esp32cam/src && cp secrets.h.example secrets.h   # enter your Wi-Fi
cd .. && pio run -t upload
```
Reserve the camera's IP in your router and use `http://<camera-ip>:81/stream` as the camera address in Settings
(containers and many Pis don't resolve `.local` names).

## Web UI
- **Setup** — what still needs attention on a fresh install, with an **Open** button that jumps to the right setting.
- **Alarms** — add, enable/disable, delete. **Test ring** rings now and can always be stopped with **Stop alarm**.
- **Camera** — plain live view (for aiming). **Smile test** — live view with a green/red frame and a table of debug values (score, threshold,
  hold progress, inference ms, camera fps/state/error, …); also JSON at `/api/debug`.
- **System & debug** — CPU, memory, temperature, power/throttling (under-voltage!), load, disk, uptimes, device time and timezone, whether it
  runs in Docker, reachability of the camera and moOde, package versions, last alarm result, last hook runs, the live log
  (filter: everything / warnings & errors) and **Test sound**.
- **Settings** — every parameter, validated against its allowed range; saved into `config.toml` (edited in place, previous version kept as
  `config.toml.bak`) and applied live. Settings that are read only at start (`server.host`, `server.port`) are marked **restart** and
  offer a **Restart now** button. **Reload file** re-reads `config.toml` after a hand edit; **Restore defaults** writes the defaults back.
  Hooks and the webhook URL are editable only when an access token is set, because a hook is a command your device runs.

## Configuration
Use **Settings** in the UI. The file behind it is `/state/config.toml` in the Docker volume (`config.toml` in the project folder for the
native install); [`config.example.toml`](config.example.toml) lists every parameter with its default and a description (it is generated
from the code, and a test fails if it goes stale).

| Section | Highlights |
|---|---|
| `[general]` | `timezone` (IANA name; a container is UTC otherwise) |
| `[server]` | `host`, `port`, `token` (access token: every `/api` call then needs `Authorization: Bearer …` or `?token=`), `public_url` (how other devices reach this app) |
| `[camera]` | `source`, `quality`, `rotate`, `flashlight` + `flashlight_level`, `width` |
| `[smile]` | `threshold` (lower = easier), `hold_seconds`, `fps`, `backend`, `model` |
| `[audio]` | `backend` local/moode/both, volume ramp, `sound`, `sound_dir`, `device` |
| `[moode]` | `host`, `port`, `library_dir`, `url`, `fallback_local` |
| `[ring]` | `max_minutes`, `allow_manual_dismiss`, `camera_fail_seconds` |
| `[hooks]` / `[events]` | shell commands on ring/dismiss; webhook URL |
| `[logging]` | `level` DEBUG/INFO/WARNING (DEBUG adds per-second detection and camera numbers) |

Relative paths in the file (`data_dir`, the model) are relative to the file itself.
`python -m alarmplus --check-config config.toml` validates a file; `--write-config PATH` writes one with all defaults.

## Optional parts
- **Second speaker with moOde** — [`docs/setup_moode_other_device.md`](docs/setup_moode_other_device.md). Set `audio.backend` to `moode`
  (or `both`) and `moode.host`. It either fetches the sound from this app over HTTP (`/sound/current`), plays a random file from its own
  library (`moode.library_dir`), or plays your `moode.url`.
- **Your own sounds, output device, volume** — [`docs/sound_output.md`](docs/sound_output.md).
- **Run a script after the alarm** — [`docs/after_alarm_hooks.md`](docs/after_alarm_hooks.md): `hooks.on_ring` / `hooks.on_dismiss` with `ALARM_*`
  variables; use `ssh` inside to act on another device.
- **Flashlight** — `camera.flashlight` switches the ESP32's flash LED on whenever the camera is in use: while an alarm rings, in the
  **Camera** view and in the **Smile test**. `camera.flashlight_level` (percent, default 30): it is very bright and warms up, so keep it modest.
  Needs the current firmware (`/control?led=0..255&for=SECONDS`). The app refreshes the light every minute and the ESP32 turns it off by
  itself after 3 minutes without a refresh, so a crash or lost Wi-Fi cannot leave it on. Off by default; ESP32 only.
- **OpenClaw** — `bash openclaw/install.sh` copies the skill to `~/.openclaw/workspace/skills/alarm-plus` (set `ALARM_URL` and, if you use a
  token, `ALARM_TOKEN` for OpenClaw). It documents the REST API (alarms, settings, diagnostics); events go to `events.webhook_url`.
  OpenClaw's skill folder and format were not verified against a real installation.

## Safety
- The smile is the only way to stop a real alarm. **Stop alarm** works for real alarms only if `ring.allow_manual_dismiss` is on (off by default),
  or when the camera has been silent for `camera_fail_seconds` or the detector cannot run. A **Test ring** can always be stopped.
- The alarm stops by itself after `ring.max_minutes`.
- Set an access token (`server.token`) on any network you don't fully trust. The token is compared in constant time and kept out of the logs.
  Without one, anyone on the network can use the page, including changing settings and restarting the service.
- Everything on the web port is plain HTTP: use it on a trusted LAN, or put a reverse proxy with HTTPS in front.
- The container runs as an unprivileged user. From the host it gets only what you give it: `/dev/snd` for local speakers (added automatically if the device has sound hardware; without it Alarm+ plays on remote moOde speakers) and optionally `/dev/video0` for a USB camera.

## Troubleshooting
| Symptom | Look at |
|---|---|
| Alarm rings at the wrong hour | Settings → `general.timezone` |
| No video / "camera error" | **System & debug → log** and the **Smile test** table: the cause is spelled out (wrong IP, connection refused, no answer, ESP32 busy with another viewer). Use the camera's IP, not a `.local` name. |
| Smile never counts | **Smile test:** is a face found? If not, try `camera.rotate`; if the score stays below the threshold, lower `smile.threshold`. |
| No sound | **Test sound**, then the log (`player exited at once …`); see [`docs/sound_output.md`](docs/sound_output.md). |
| Nothing on the second speaker | `System & debug → reachability`, then [`docs/setup_moode_other_device.md`](docs/setup_moode_other_device.md) (in bridge networking set `server.public_url`). |
| Camera drops off the network now and then ("no answer from 10.x.x.x:81", "No route to host"), often right after the flashlight turns on | The ESP32-CAM is power-starved. Use a 5 V / 2 A supply and a short cable, keep `camera.flashlight_level` low, and flash the current firmware (it caps the stream at ~15 fps and ramps the LED up). Alarm+ already sends the resolution only when it changes and switches the LED on only once video flows. |
| Random freezes / reboots of the Pi or ESP32 | `throttled` in **System & debug**: under-voltage means a weak power supply. |
| Container won't start / restarts | `docker compose logs alarmplus`; more in [`docs/docker.md`](docs/docker.md#troubleshooting). |

## Uninstall
```bash
bash uninstall.sh --dry-run     # preview
bash uninstall.sh               # interactive; finds the Docker and/or native install
```
Removes the container, network and image; **your data volume is kept unless you say so** (`--purge` writes a backup tarball to your home
folder first). The project folder is left for you to delete. Full description: [`docs/uninstall.md`](docs/uninstall.md).

## Development
```bash
pip install -r requirements-dev.txt
python -m pytest tests
```
About 180 tests: schedule, smile gate, the ring loop and its failure modes with fake camera/audio/detector, the REST API, settings and in-place
`config.toml` editing, timezone, the MPD client against a fake server, hooks, logging, the flashlight against a fake ESP32, the OpenClaw skill's
consistency with the API, the Docker image definition and entrypoint/healthcheck, and all shell scripts (installers and uninstallers run against
a stub `docker` or a throw-away copy).
Layout: `alarmplus/` (Python package), `web/index.html` (UI), `Dockerfile`, `docker-compose.yml`, `docker/` (entrypoint, healthcheck),
`install.sh` / `uninstall.sh` and `deploy/` (the scripts behind them), `firmware/esp32cam/`, `openclaw/`, `docs/`, `tests/`.

## Documentation
- [`docs/docker.md`](docs/docker.md) — Docker guide: setup, sound, USB camera, SSH hooks, updating, backup, bridge networking, troubleshooting
- [`docs/native_install.md`](docs/native_install.md) — the optional install without Docker
- [`docs/setup_moode_other_device.md`](docs/setup_moode_other_device.md) — ring through a second Pi running moOde
- [`docs/sound_output.md`](docs/sound_output.md) — speakers, output device, your own sounds / a sound folder, volume
- [`docs/after_alarm_hooks.md`](docs/after_alarm_hooks.md) — scripts on ring/dismiss
- [`docs/uninstall.md`](docs/uninstall.md) — removing Alarm+

## Verified on a Raspberry Pi 5 (and what is not)
Checked on a real Raspberry Pi 5 (Debian 13, Docker 29): the **Docker build** (about 3 minutes the first time, image 1.6 GB, cached rebuilds in seconds), the
installer (including picking port 8081 because qBittorrent owned 8080), the health check, **MediaPipe loading in the container** (about 7 ms per frame), the
ESP32 video stream from inside the container, **sound through `/dev/snd`** (HDMI, including the fallback to a working output when the default one is dead), test
rings with the Stop button and both hooks, a token and a port change followed by **Restart now** (the container restarted itself and came back healthy on the new
port), updating over an existing volume, **one** OpenCV copy in the image (the fallback detector's cascade files are shipped separately, because the OpenCV 5 wheels no longer include them), `install.sh --clean-cache` (it freed 5.8 GB of build cache), and the Docker uninstaller (backup first, then removal; a native install running next to it was untouched). The
whole test suite (190+ tests) also passes on the Pi. The native service on that Pi was not restarted onto this code.

Not verified: the full smile-to-dismiss flow with a real face, a real alarm at its scheduled time, the ESP32 flashlight and the firmware's flash/control changes (need a
reflash), the second-speaker (moOde) path with the new code, the PulseAudio-socket option, the USB camera option, bridge networking, and the native install on a fresh machine.
Tune `smile.threshold` to your lighting with the **Smile test** view.
