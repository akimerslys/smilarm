# Alarm+ — an alarm clock you switch off with a smile

The alarm keeps ringing until you smile at the camera for 2–3 seconds in a row.

```
ESP32-CAM ──MJPEG──▶ Rpi5: alarmplus (schedule + smile detection + sound + web UI)
                          │ optional
                          ├──MPD:6600──▶ Rpi3 (moOde + speakers)
                          └──webhook───▶ OpenClaw
```

**The core runs on a single device (the Rpi5).** The camera and the Rpi3 are pluggable parts:
- Camera: an ESP32-CAM, or a USB/Pi camera attached directly to the Rpi5 (`camera.source = "0"`) — no ESP32 needed then.
- Sound: plays locally on the Rpi5 by default (`audio.backend = "local"`). The Rpi3 with moOde is an optional extra; if it is unreachable, sound automatically falls back to the Rpi5 (`moode.fallback_local`).

## Optimization
- The camera is opened and the detector runs **only while an alarm is ringing** (~0% CPU the rest of the time; the ESP32 doesn't encode frames without a client).
- The scheduler sleeps until the next alarm instead of polling every second.
- Default QVGA 320×240 (`camera.quality = "low"`; also `medium` 640×480 and `high` 800×600), ~6 frames/s, MediaPipe Face Landmarker (`mouthSmile` blendshape) ≈ 10 ms/frame on a Pi 5. If mediapipe is unavailable, it falls back to OpenCV Haar automatically.
- One web-stack dependency (aiohttp); the UI is a single HTML file with no build step.
- Volume ramps up smoothly (`ramp_seconds`).

## Installation
**1. ESP32-CAM** (PlatformIO):
```bash
cd firmware/esp32cam/src && cp secrets.h.example secrets.h   # enter your Wi-Fi
cd .. && pio run -t upload
```
Check `http://alarmcam.local:81/stream`. (The Arduino IDE works too: put `main.cpp` and `secrets.h` in a sketch folder, board "AI Thinker ESP32-CAM", PSRAM enabled.)

**2. Rpi5:**
```bash
git clone <repo> alarm+ && cd alarm+ && bash deploy/install.sh
nano config.toml    # camera.source etc.
```
If the camera is mounted sideways or upside down, set `camera.rotate` (0/90/180/270, clockwise; try 90, else 270) and check the result in **Smile test**. The detector only works on upright faces.

Prefer the camera's IP address in `camera.source` (e.g. `http://10.9.141.122:81/stream`): `.local` names often don't resolve on the Pi. Reserve that IP in your router.

Web UI: `http://<rpi5>.local:8080` — alarms, “Test ring”, “Camera” (plain live view, handy for aiming) and “Smile test” (live view with a smiling / not-smiling frame plus a table of debug values: score, threshold, progress, fps, frame age, …). The same values are available as JSON at `/api/debug`.

Two more panels in the UI:
- **System & debug** — CPU, memory, temperature, power/throttling (under-voltage!), load, disk, uptimes, reachability of the camera and moOde, package versions, last alarm result, last hook runs, the live log, and a **Test sound** button.
- **Settings** — the options that are safe to change at runtime (smile threshold/hold, camera preset, volume ramp, sound file/folder/device, moOde, failsafes), each validated against its allowed range. Changes are stored in `data/settings.json`; `config.toml` is never rewritten and **Reset** returns to it. Commands and the webhook are editable here only when `server.token` is set.

**3. Rpi3 + moOde (optional):** in `config.toml` set `audio.backend = "moode"` (or `"both"`) and `moode.host = "<rpi3>"`. moOde's MPD listens on port 6600 by default. The Rpi3 fetches the sound over HTTP from the Rpi5 (`/sound/alarm.wav`) — or set your own `moode.url` (e.g. an internet radio stream).

**4. OpenClaw (optional):**
```bash
bash openclaw/install.sh        # copies the skill to ~/.openclaw/workspace/skills/alarm-plus
```
The agent manages alarms through the REST API (see `openclaw/skills/alarm-plus/SKILL.md`). For events (`ringing`, `dismissed`) set `[events] webhook_url`. I haven't verified OpenClaw's skill paths and format against a real installation — check them against your version.

## Documentation
- [`docs/setup_moode_other_device.md`](docs/setup_moode_other_device.md) — ring through a second Pi running moOde.
- [`docs/sound_output.md`](docs/sound_output.md) — speakers, output device, your own sounds / a sound folder, volume (no moOde needed).
- [`docs/after_alarm_hooks.md`](docs/after_alarm_hooks.md) — run your own script when an alarm starts/ends (on the Rpi5, e.g. `ssh rpi3 ~/after_alarm.sh`).

## Safety
- The web UI has a **Stop alarm** button while ringing. For real alarms it works only if `ring.allow_manual_dismiss` is on (Settings or `config.toml`; off by default, since it defeats the purpose), or when the camera has been silent for `camera_fail_seconds`. A **Test ring** can always be stopped.
- Manual dismissal is disabled by default (`allow_manual_dismiss = false`). Exception: if the camera is silent for longer than `camera_fail_seconds`, a button appears in the UI, so a broken camera can't ring forever.
- The alarm stops by itself after `max_minutes`.
- On an untrusted network, set `server.token`.

## Tests
`python -m pytest tests` — schedule logic, the smile gate, the ring loop with fake camera/audio/detector, the REST API, the MPD client against a fake server, config and camera presets.

## Not verified
Unit tests run on Windows and on a Raspberry Pi 5 (install, MediaPipe backend loading). Not yet verified end to end: the real smile-to-dismiss flow on hardware, sound through `mpv`, and the Rpi3/moOde path. Tune `smile.threshold = 0.5` to your lighting — the “Smile test” view shows the live score.
