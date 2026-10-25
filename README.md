# Smilarm

**An alarm clock you switch off with a smile.** It rings until you smile at the camera for 2–3 seconds in a row.

```
ESP32-CAM ──video──▶ Raspberry Pi: Smilarm (schedule · smile detection · sound · web UI · REST API)
                          ├──▶ speakers on the Pi, or a second Pi running moOde
                          └──▶ your script / webhook / OpenClaw (optional)
```

## Quick start
```bash
git clone https://github.com/akimerslys/smilarm && cd smilarm
bash install.sh        # install (and update)
bash uninstall.sh      # remove
```
Then open **`http://<your-pi>:8080`**. The **Setup** card at the top walks you through it: **timezone → camera address → access token → test the sound**.
Everything is configured in the web UI; there is no file to edit. The folder you cloned is not needed afterwards: Smilarm lives in Docker and `~/.smilarm`,
and a `smilarm` command (`status`, `logs`, `update`, `backup`, `uninstall`, …) works from anywhere.

You need: a Raspberry Pi 4/5 (or any 64-bit Linux box), Docker (`bash install.sh --install-docker` installs it), and an AI Thinker ESP32-CAM ([hardware.md](docs/hardware.md)).

## More install options
| | |
|---|---|
| `bash install.sh --native` | no Docker: systemd service + Python venv in `~/.smilarm` |
| `bash install.sh --demo` | preview the installer; installs nothing |
| `bash install.sh --clean-cache` | free Docker's build cache afterwards (`--clean-only`: just that) |
| `bash install.sh --build` / `--no-start` / `--plain` / `--no-migrate` | build from this clone / install only / no animation / skip moving data from the old "Alarm+" |
| `bash uninstall.sh --purge` | also delete your config and alarms (a backup is written first) |
| `smilarm update` · `backup` · `restore` · `clean` | update, save/restore your settings and alarms, free disk space |

All options, the `smilarm` command, updating, backup, bridge networking, USB camera, migration from Alarm+ and publishing: **[docs/install.md](docs/install.md)**.

## Features
- **Smile detection** with MediaPipe (about 7 ms/frame on a Pi 5), with an OpenCV fallback. It only runs while an alarm rings, so the Pi is idle otherwise.
- **Web UI:** alarms, live camera view, **Smile test** with debug numbers, system health, live log, a first-run **Setup** card and validated **Settings** for every parameter
  (saved to `config.toml` in place and applied live; a few need a **Restart now**).
- **Camera:** presets 320×240 / 640×480 / 800×600, rotation for sideways mounting, optional **flashlight** (the ESP32's LED lights the room), USB camera too.
- **Sound:** the Pi's speakers (HDMI/USB/I2S), a second Pi with moOde, or both; smooth volume ramp; your own sounds or a random one from a folder; a missing file or dead output falls back so the alarm is never silent.
- **Reliable:** a failing detector, camera, webhook or hook never stops the sound; a dead camera unlocks a manual stop; the alarm ends after `ring.max_minutes`; your timezone is a setting.
- **Integrations:** shell hooks on ring/dismiss, a webhook, a REST API and an OpenClaw skill ([integrations.md](docs/integrations.md)).

## Configuration
Use **Settings** in the UI. The file behind it is `/state/config.toml` in the Docker volume (`~/.smilarm/config.toml` for a native install);
[`config.example.toml`](config.example.toml) lists every parameter with its default and a description (generated from the code, so it cannot go stale).
`smilarm config` edits the file by hand; `python -m smilarm --check-config FILE` validates one.

| Section | Highlights |
|---|---|
| `[general]` | `timezone` (a container is UTC otherwise) |
| `[server]` | `port`, `token` (access token: every `/api` call then needs `Authorization: Bearer …` or `?token=`), `public_url` |
| `[camera]` | `source`, `quality`, `rotate`, `flashlight`, `flashlight_level` |
| `[smile]` | `threshold` (lower = easier), `hold_seconds`, `fps`, `backend` |
| `[audio]` / `[moode]` | `backend` local/moode/both, volume ramp, `sound`, `sound_dir`, `device`; second speaker `host`, `library_dir` |
| `[ring]` | `max_minutes`, `allow_manual_dismiss`, `camera_fail_seconds` |
| `[hooks]` / `[events]` / `[logging]` | commands on ring/dismiss, webhook URL, log level |

## Safety
- The smile is the only way to stop a real alarm. **Stop alarm** works for a real alarm only if you enable `ring.allow_manual_dismiss`, or when the camera has been silent for
  `camera_fail_seconds` or the detector cannot run. A **Test ring** can always be stopped. The alarm ends by itself after `ring.max_minutes`.
- **Set an access token** (Setup card) on any network you do not fully trust: without one, anyone on it can use the page, change settings and restart the service.
  Hooks and the webhook URL can only be edited in the UI when a token is set. The page is plain HTTP: use it on a trusted LAN or behind an HTTPS reverse proxy.
- The container runs as an unprivileged user and gets only `/dev/snd` (and `/dev/video0` if you add it).

## Documentation
- **[Install, update, remove](docs/install.md)** — every option, the `smilarm` command, Docker and native details, backup, disk space, moving from Alarm+
- **[Hardware](docs/hardware.md)** — flashing the ESP32-CAM, flashlight, USB camera, speakers, output device, your own sounds
- **[Second speaker (moOde)](docs/moode.md)** · **[Integrations](docs/integrations.md)** (hooks, webhook, API, OpenClaw) · **[Troubleshooting](docs/troubleshooting.md)**

## Status
Checked on a Raspberry Pi 5 (Debian 13, Docker 29): a fresh `bash install.sh` (image build, port 8081 chosen because 8080 was taken), deleting the clone and then using the
`smilarm` command (status, url, logs, restart, backup, update message), `smilarm uninstall --purge` (backup first, then removal, command and `~/.smilarm` gone), moving data from an
old "Alarm+" install (tested with a throw-away volume: the alarm and the port came over, the old volume was kept), `--clean-cache` (freed 3.4 GB), the full test suite, and earlier
the ESP32 stream, sound through `/dev/snd`, test rings with hooks, token/port changes with **Restart now** and MediaPipe in the container. Not yet verified on hardware: the full
smile-to-dismiss flow with a real face, a real scheduled alarm, the flashlight and the current ESP32 sketch (needs a reflash), the moOde path with the current code,
PulseAudio/USB-camera/bridge-network setups, the native install on a fresh machine, and pulling a prebuilt image from the registry (the project is not published yet).

## Development
```bash
pip install -r requirements-dev.txt && python -m pytest tests
```
About 200 tests: schedule, smile gate, the ring loop and its failure modes, REST API, settings and in-place `config.toml` editing, the MPD client, hooks, the flashlight against a fake
ESP32, the image definition, entrypoint and health check, every install/uninstall path (against a stub `docker` or a throw-away home), and documentation consistency.
Layout: `smilarm/` (Python package), `web/index.html`, `firmware/smilarm_cam/`, `Dockerfile` + `docker-compose*.yml` + `docker/`, `install.sh` / `uninstall.sh` + `scripts/`, `openclaw/`, `docs/`, `tests/`.
