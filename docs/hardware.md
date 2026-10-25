# Hardware: camera and speakers

## The camera: ESP32-CAM
An AI Thinker ESP32-CAM streams video over Wi-Fi; all smile detection runs on the Pi. The sketch is `firmware/smilarm_cam/smilarm_cam.ino` (one copy for
both the Arduino IDE and PlatformIO).

**Flash it** (once):
1. Copy `firmware/smilarm_cam/secrets.h.example` to `secrets.h` in the same folder and enter your Wi-Fi.
2. *Arduino IDE:* open `smilarm_cam.ino`, board **AI Thinker ESP32-CAM**, **PSRAM enabled**, pick the port, Upload. *PlatformIO:* `cd firmware/smilarm_cam && pio run -t upload`.
3. The AI Thinker has no USB port. Use a USB-serial adapter (CP2102/CH340/FTDI; 5 V, GND, TX→U0R, RX→U0T), bridge **IO0 to GND**, press RESET, upload,
   then remove the bridge and press RESET again.
4. Reserve the camera's IP address in your router and enter `http://<camera-ip>:81/stream` as **Settings → `camera.source`** (use the IP, not a `.local`
   name: containers and many Pis do not resolve those). Check it with **Camera** and **Smile test** in the web UI.

**Keep it stable** (an ESP32-CAM is easily power-starved):
- 5 V / 2 A supply and a short, thick cable. A weak supply makes the camera drop off the network, often right when the flash LED turns on.
- The sketch caps the stream at about 15 fps and ramps the LED up. Smilarm sends the resolution only when it changes and switches the LED on only once
  video flows. If the camera still vanishes, lower `camera.flashlight_level`.

**Camera settings** (Settings in the UI): `camera.quality` (`low` 320×240, `medium` 640×480, `high` 800×600), `camera.rotate` (0/90/180/270 clockwise; the
detector needs upright faces, so use 90 or 270 for a sideways camera and check the **Smile test**), `camera.flashlight` + `camera.flashlight_level`.

**Flashlight:** the ESP32's white LED lights the room while an alarm rings, in the **Camera** view and in the **Smile test**, and goes off afterwards.
Off by default; ESP32 only. The app refreshes it every minute and the sketch switches it off by itself after 3 minutes, so a crash or lost Wi-Fi cannot leave
it on. It is very bright and warms up: keep the level modest.

**USB or Pi camera instead:** see [install.md](install.md#docker-details) (Docker) and set `camera.source` to `0`.

## Speakers
By default Smilarm plays on the device that runs it, through `mpv`. Three ways to get sound:

1. **This device's speakers (automatic).** HDMI, a USB sound card, an I2S DAC. Docker gets them through `/dev/snd`
   (`docker-compose.sound.yml`, added by the installer when the device has sound hardware). The Rpi5 has **no 3.5 mm jack**.
2. **A second Pi with moOde** (remote speakers; no sound hardware needed here): [moode.md](moode.md).
3. **PulseAudio/PipeWire** (Bluetooth speakers, desktop audio), optional and not tested by us. Docker: put this in `~/.smilarm/compose/docker-compose.local.yml`
   and re-run `bash install.sh`:
   ```yaml
   services:
     smilarm:
       environment:
         - PULSE_SERVER=unix:/run/user/1000/pulse/native
       volumes:
         - /run/user/1000/pulse:/run/user/1000/pulse
   ```
   (`1000` = your user id, `id -u`). The player prefers PipeWire/Pulse over ALSA; pick the sink in `audio.device` (`pulse/<sink-name>`). Native: pair with
   `bluetoothctl`; the service has no desktop session, so you may need `Environment=XDG_RUNTIME_DIR=/run/user/1000` (`sudo systemctl edit smilarm`) and
   `sudo loginctl enable-linger $USER`. A wired/USB ALSA device is the most reliable choice for an alarm.

**Choose the output:** Settings → `audio.device`. The field suggests every device the player can see (also `GET /api/audio-devices`, or
`mpv --audio-device=help`; in Docker `docker exec smilarm mpv --audio-device=help`). Empty = the system default. **If the default output does not work**
(on a Pi with two HDMI ports the default is often the one with nothing plugged in) the alarm does not stay silent: it falls back to the built-in tone and
then tries the machine's other ALSA outputs one by one, and logs which one worked ("set `audio.device` to that value to make it permanent").
The image is based on Debian 13 on purpose: its ALSA library drives the Pi's HDMI audio through `plughw`; an older one cannot ("Sample format non available").

**Your own sounds** (`.wav .mp3 .ogg .flac .m4a .opus`): `audio.sound` = one file or an http(s) URL; `audio.sound_dir` = a folder, one random file per
alarm. Empty folder or missing file falls back to the built-in tone (a warning is logged). The settings check that the path exists before saving.
*Docker:* paths are inside the container: put files into the state volume,
`docker exec smilarm mkdir -p /state/sounds && docker cp ./morning.mp3 smilarm:/state/sounds/`, then `audio.sound_dir = /state/sounds`.

**Volume:** `audio.volume_start`, `audio.volume_max`, `audio.ramp_seconds` (percent of the player's volume, relative to the *system* volume: if it is too
quiet at 100 %, raise it once with `alsamixer`).

**No sound?** Press **System & debug → Test sound**. Then: the log (`player exited at once …` names the reason); is the right device chosen; is it muted in
`alsamixer`; does `speaker-test -t wav -c 2 -l 1` work on the host (if not, it is the OS audio setup).
