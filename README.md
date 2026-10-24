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
Check `http://alarmcam.local:81/stream`.

**2. Rpi5:**
```bash
git clone <repo> alarm+ && cd alarm+ && bash deploy/install.sh
nano config.toml    # camera.source etc.
```
Web UI: `http://<rpi5>.local:8080` — add an alarm, “Camera” button (preview, handy for aiming), “Test ring”.

**3. Rpi3 + moOde (optional):** in `config.toml` set `audio.backend = "moode"` (or `"both"`) and `moode.host = "<rpi3>"`. moOde's MPD listens on port 6600 by default. The Rpi3 fetches the sound over HTTP from the Rpi5 (`/sound/alarm.wav`) — or set your own `moode.url` (e.g. an internet radio stream).

**4. OpenClaw (optional):**
```bash
bash openclaw/install.sh        # copies the skill to ~/.openclaw/workspace/skills/alarm-plus
```
The agent manages alarms through the REST API (see `openclaw/skills/alarm-plus/SKILL.md`). For events (`ringing`, `dismissed`) set `[events] webhook_url`. I haven't verified OpenClaw's skill paths and format against a real installation — check them against your version.

## Safety
- Manual dismissal is disabled (`allow_manual_dismiss = false`). Exception: if the camera is silent for longer than `camera_fail_seconds`, a button appears in the UI, so a broken camera can't ring forever.
- The alarm stops by itself after `max_minutes`.
- On an untrusted network, set `server.token`.

## Tests
`python -m pytest tests` (schedule logic, the “smile gate”, the MPD client against a fake server).

## Not verified
The code has only been checked with unit tests and an API smoke test on Windows. It has not been run on hardware: the ESP32 firmware has not been compiled, mediapipe/mpv on the Pi 5 have not been tried, and `smile.threshold = 0.5` should be tuned to your lighting.
