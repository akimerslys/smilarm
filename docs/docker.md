# Running Alarm+ with Docker (recommended)

Docker is the primary way to run Alarm+: one command builds everything (Python, MediaPipe, the face model, `mpv`) inside an image, nothing
is installed on the Raspberry Pi itself, updating is one command, and removing it leaves the system clean.

**You do not edit any file.** After the container starts, open the web UI and finish the setup in **Settings** (camera address, timezone,
access token, sound). Everything you set is saved in a Docker volume and survives updates.

## Quick start
```bash
git clone <repo> alarm+ && cd alarm+
bash install.sh
```
`install.sh` checks Docker, builds the image (showing what is downloading and how much), starts the container and waits until it is
healthy. Then open **`http://<this-device>:8080`**. The **Setup** card at the top of the page lists what still needs attention
(the camera address, your timezone, an access token) with an **Open** button for each.

No Docker yet? `bash install.sh --install-docker` installs it with Docker's official script (https://get.docker.com), or follow
https://docs.docker.com/engine/install/. Docker Compose (the `docker compose` plugin) is required; it comes with that script.

Without the script, the same thing by hand: `docker compose up -d --build`.

If port 8080 is already used by another program (qBittorrent and many others use it), the installer notices on a first install, starts
Alarm+ on the next free port instead (it tells you which) and prints the right URL. The port is a normal setting later (**Settings →
`server.port`**, then **Restart now**).

## What runs where
| Part | Where |
|---|---|
| Alarm+ (web UI, scheduler, smile detection, sound) | the container `alarmplus` |
| Config (`config.toml`), alarms, logs, the face model | the volume `alarmplus-state`, mounted at `/state` |
| ESP32-CAM | stays a separate device; the container reads its video over the network |
| moOde speaker (optional) | a separate device; the container sends it MPD commands |

`docker-compose.yml` is the whole deployment description. It uses **host networking**, so the container has the device's real network
address: the ESP32 and the moOde speaker can talk to it, and the web UI is simply on port 8080 (or whatever you set in Settings).
`restart: unless-stopped` brings it back after a reboot or crash.

## First-time setup in the web UI
Open the UI and work down the **Setup** card (or open **Settings**):
1. **Timezone** (`general.timezone`): e.g. `Europe/London`. A container's clock is UTC; without this a 07:00 alarm rings at 07:00 UTC.
2. **Camera**: `camera.source` = `http://<camera-ip>:81/stream` (use the IP, not a `.local` name; containers don't resolve those).
   Check it with **Camera** and **Smile test** in the UI. Turn on the `camera.flashlight` option if the room is dark.
3. **Access token** (`server.token`): recommended. After you save it the UI keeps working (it remembers the token in your browser);
   other devices need it. It also unlocks editing the after-alarm commands and the webhook.
4. **Sound**: press **System & debug → Test sound**. See [sound_output.md](sound_output.md) for output devices and your own sounds.
5. Optional: a second speaker ([setup_moode_other_device.md](setup_moode_other_device.md)), hooks ([after_alarm_hooks.md](after_alarm_hooks.md)).

Settings marked **restart** (`server.host`, `server.port`) show a banner with a **Restart now** button: the container restarts itself
and the page reconnects (to the new port, if you changed it).

## Extra compose files (how optional hardware is added)
`docker-compose.yml` is the plain deployment. `install.sh` writes a small `.env` next to it (`COMPOSE_FILE=...`) that adds:
- **`docker-compose.sound.yml`**: this device's speakers (`/dev/snd`). Added automatically **only if the device has sound hardware**, so a
  server without sound does not fail to start; it then plays on remote (moOde) speakers only (the installer tells you).
- **`docker-compose.local.yml`**: *your* additions: a USB camera, a PulseAudio socket, bridge networking (examples below). Create it yourself; it is
  picked up the next time you run `bash install.sh`, git ignores it, and updates never touch it.

Plain `docker compose ...` commands in the project folder use the same set through `.env`. If you never ran `install.sh`, start with
`docker compose -f docker-compose.yml -f docker-compose.sound.yml up -d --build`.

## Sound from the container
There are three ways to get sound; pick the one that fits.

**1. This device's own speakers (automatic).** `docker-compose.sound.yml` hands the sound hardware to the container (the same as
`docker run --device /dev/snd --group-add audio`). That is enough for HDMI audio, a USB speaker or an I2S DAC.
- In **Settings → `audio.device`** choose the output; the field suggests every device the container can see (also at `/api/audio-devices`, or
  `docker compose exec alarmplus mpv --audio-device=help`). Empty = the system default.
- If the default output does not work (on a Raspberry Pi with two HDMI ports the default is often the one with nothing plugged in), the alarm
  does not stay silent: it falls back to the built-in tone and then tries the machine's other ALSA outputs one by one, and logs which one worked
  ("Set audio.device to that value to make it permanent").
- The image is based on Debian 13 on purpose: its ALSA library can drive the Raspberry Pi's HDMI audio through the `plughw` converter, an older
  one cannot ("Sample format non available"). Verified on a Raspberry Pi 5.

**2. Only remote speakers (a moOde device): no sound hardware needed.** If `/dev/snd` does not exist (a server, a VM, Docker Desktop on
Windows/macOS) `install.sh` leaves the sound file out and says so. Set `audio.backend = moode` in Settings
([setup_moode_other_device.md](setup_moode_other_device.md)).

**3. A PulseAudio/PipeWire socket (Bluetooth speakers, desktop audio): optional, not tested by us.** If the host runs PulseAudio or
PipeWire-pulse, share its socket. Create `docker-compose.local.yml`:
```yaml
services:
  alarmplus:
    environment:
      - PULSE_SERVER=unix:/run/user/1000/pulse/native
    volumes:
      - /run/user/1000/pulse:/run/user/1000/pulse
```
(`1000` is your user id: `id -u`), then `bash install.sh`. The player prefers PipeWire/Pulse over ALSA automatically; pick the sink in
Settings → `audio.device` (e.g. `pulse/<sink-name>`). A Raspberry Pi OS Lite install has no such server.

## USB or Pi camera
Create `docker-compose.local.yml`:
```yaml
services:
  alarmplus:
    devices:
      - /dev/video0:/dev/video0
```
run `bash install.sh`, and set `camera.source` to `0` in Settings. (The flashlight option is ESP32-only.)

## Hooks that SSH to other devices
Hook commands run **inside the container** (where `openssh-client` is installed). Keep the keys in the state volume:
```bash
docker compose exec alarmplus bash -c 'mkdir -p /state/ssh && ssh-keygen -t ed25519 -N "" -f /state/ssh/id_ed25519'
docker compose exec -it alarmplus ssh-copy-id -i /state/ssh/id_ed25519.pub pi@rpi3.local     # asks the Rpi3's password once
```
Then use the key in the hook (Settings → `hooks.on_dismiss`, needs an access token), for example:
`ssh -i /state/ssh/id_ed25519 -o BatchMode=yes -o StrictHostKeyChecking=accept-new -o UserKnownHostsFile=/state/ssh/known_hosts pi@rpi3.local 'bash ~/after_alarm.sh'`.
Scripts of your own can live in `/state` too (`docker compose cp my.sh alarmplus:/state/my.sh`). See [after_alarm_hooks.md](after_alarm_hooks.md).

## Updating
```bash
cd alarm+ && git pull && bash install.sh
```
It rebuilds the image and recreates the container. Your volume (settings, alarms) is kept. New settings appear in the UI with their defaults.

## Disk space: cleaning the build cache
Building leaves a Docker **build cache** behind (about 3 GB after a few builds) that Alarm+ does not need to run; it only makes the next
update faster. Updates also leave the previous image behind as an untagged "dangling" image (1.5 GB each). The installer mentions it when the cache is large.
```bash
bash install.sh --clean-cache    # install/update as usual, then free the caches
bash install.sh --clean-only     # just free them: builds and starts nothing
```
This runs `docker builder prune -af` and `docker image prune -f --filter label=app=alarmplus`: it frees **all unused build cache on this machine**
(it is shared by every Docker project, which then rebuilds a little slower once) and Alarm+'s own old images. It never touches containers,
volumes (your settings and alarms) or other images. The next Alarm+ build then downloads the Python libraries again (about 3 minutes on a Pi 5).

## Everyday commands
| | |
|---|---|
| Logs | `docker compose logs -f alarmplus`, or **System & debug** in the web UI |
| Restart | `docker compose restart alarmplus` (or the UI) |
| Stop / start | `docker compose stop` / `docker compose start` |
| Status and health | `docker ps` (shows `healthy`) |
| Open a shell | `docker compose exec alarmplus bash` |
| Edit the config by hand | `docker compose exec -it alarmplus nano /state/config.toml`, then **Reload file** in Settings (or restart). Normally you never need this: everything is in Settings. |

## Backup and restore
```bash
# backup (the uninstaller does the same before deleting data)
docker run --rm --user "$(id -u):$(id -g)" -v alarmplus-state:/state:ro -v "$HOME":/backup --entrypoint tar alarmplus:latest czf /backup/alarmplus-backup.tar.gz -C /state .
# restore into a fresh volume
docker compose down && docker volume rm alarmplus-state && docker volume create alarmplus-state
docker run --rm --user 1000:1000 -v alarmplus-state:/state -v "$HOME":/backup --entrypoint tar alarmplus:latest xzf /backup/alarmplus-backup.tar.gz -C /state
docker compose up -d
```

## Bridge networking (if you can't use host networking)
Host networking is Linux-only. For the default bridge network instead, put this in `docker-compose.local.yml` (a later file overrides the
base file's `network_mode`), then run `bash install.sh`:
```yaml
services:
  alarmplus:
    network_mode: bridge
    ports:
      - "8080:8080"
```
and, because the container then has a private address the moOde speaker can't reach, set **Settings → `server.public_url`** to the address
of the Docker host, e.g. `http://192.168.1.20:8080`. The ESP32 camera needs nothing (the container reaches it by IP).
Keep `server.host` at `0.0.0.0` and `server.port` at `8080` (the mapped port). Hostnames such as `moode.local` don't resolve inside a
container either way: use IP addresses.

## Troubleshooting
| Symptom | Fix |
|---|---|
| `bash install.sh` says the daemon can't be reached | `sudo systemctl start docker`; add yourself to the `docker` group (`sudo usermod -aG docker $USER`, then log in again) or the script uses `sudo`. |
| Container restarts in a loop | `docker compose logs alarmplus`. The log says why. (A missing `/dev/snd` no longer causes it: `install.sh` leaves the sound file out. If you started compose by hand with `-f docker-compose.sound.yml` on a machine without sound hardware, drop that file.) |
| Alarm rings at the wrong hour | Settings → `general.timezone`. |
| Container restarts in a loop with "address already in use" | Another program owns the port (host networking shares the Pi's ports). `sudo ss -ltnp \| grep :8080`; change `server.port` by editing the config (`docker compose exec -it alarmplus nano /state/config.toml`) and `docker compose restart alarmplus`. |
| "camera error" | The log and the Smile test table say why. Use the camera's IP address. |
| No sound | **Test sound**, then the log (`player exited at once …`); check `audio.device`. |
| Web UI unreachable after changing the port | Open the new port. If you locked yourself out with a token, read it: `docker compose exec alarmplus grep token /state/config.toml`. |
| MediaPipe not loaded ("Simple smile detector in use") | The log shows the reason. The image ships the model; make sure `smile.model` is still `data/face_landmarker.task`. |
