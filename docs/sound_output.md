# Sound output without moOde

> **Docker (the default install):** the container plays through the host's sound hardware (`/dev/snd` is passed in by `docker-compose.sound.yml`, which `install.sh` adds automatically when the device has sound hardware).
> Paths such as `audio.sound` and `audio.sound_dir` are paths **inside the container**. Put your files in the state volume, e.g.
> `docker compose cp ./morning.mp3 alarmplus:/state/sounds/morning.mp3` (create the folder first:
> `docker compose exec alarmplus mkdir -p /state/sounds`), then set `audio.sound_dir` to `/state/sounds` in Settings. List output
> devices with `docker compose exec alarmplus mpv --audio-device=help`. Bluetooth speakers need a PulseAudio/PipeWire socket shared into the container (see [docker.md](docker.md#sound-from-the-container)) or the native install.
> The `sudo systemctl`/`~/alarm-sounds` examples below are for the native install; in Docker use the commands above.

By default Alarm+ plays the alarm on the same device that runs it (the Rpi5), using `mpv`.
This page covers choosing the speaker, your own sounds, and volume.

## 1. Check that the device can play sound at all
```bash
speaker-test -t wav -c 2 -l 1          # or: aplay /usr/share/sounds/alsa/Front_Center.wav
```
Then use **System & debug → Test sound (6 s)** in the web UI. It plays the current alarm sound without
the camera. The log below it shows errors, and "audio players" shows which backend started.

## 2. Pick the output device
Raspberry Pi models differ:
- **Rpi5 has no 3.5 mm headphone jack.** Use HDMI, a USB sound card/DAC, an I2S DAC hat, or Bluetooth.
- Rpi3/Rpi4 have the 3.5 mm jack and HDMI.

List what `mpv` can use:
```bash
mpv --audio-device=help
```
Pick a line such as `alsa/plughw:CARD=Device,DEV=0` (a USB DAC) or `alsa/hdmi:CARD=vc4hdmi0,DEV=0`,
and set it in `config.toml`:
```toml
[audio]
device = "alsa/plughw:CARD=Device,DEV=0"
```
(or in the web UI under **Settings → audio.device**). Empty means the system default.
Without mpv, `aplay` is used and takes the device from `aplay -L` (e.g. `plughw:1,0`).

**Bluetooth speaker:** pair it with `bluetoothctl` and make it the default sink in your desktop audio settings
or `wpctl set-default <id>`. Alarm+ runs as a system service without your desktop session, so PipeWire may
not be reachable. If you get no sound from a PipeWire/Pulse device, add to the service
(`sudo systemctl edit alarmplus`):
```ini
[Service]
Environment=XDG_RUNTIME_DIR=/run/user/1000
```
(replace `1000` with your user id from `id -u`) and run `sudo loginctl enable-linger $USER`.
A wired/USB ALSA device avoids all of this and is the most reliable choice for an alarm.

The service user must be allowed to use audio: `sudo usermod -aG audio $USER` (then restart the service).

## 3. Your own alarm sounds
Supported: `.wav .mp3 .ogg .flac .m4a .opus`.

**One sound:**
```toml
[audio]
sound = "/home/pi/alarm-sounds/morning.mp3"     # a file path or an http(s) URL
```

**A folder, random file per alarm:**
```bash
mkdir -p ~/alarm-sounds
scp *.mp3 pi@rpi5.local:alarm-sounds/           # or copy them over the network share
```
```toml
[audio]
sound_dir = "/home/pi/alarm-sounds"             # overrides `sound`
```
Empty folder or missing files → falls back to `sound`, then to the built-in tone (a warning is logged).
Both options are also editable in the web UI (**Settings**), which checks that the path exists on the
device before saving.

Tip: choose something that is loud and rises sharply; the volume ramps from `volume_start` to `volume_max`
over `ramp_seconds` anyway. Files loop until you smile.

## 4. Volume
- `audio.volume_start`, `audio.volume_max`, `audio.ramp_seconds` control the ramp (percent of mpv's volume).
- mpv's volume is relative to the **system** volume. If it's too quiet even at 100 %, raise the system
  volume once: `alsamixer` (or `amixer sset Master 90%`), then save it with `sudo alsactl store`.
- Without mpv (`aplay`/`ffplay` fallback) there is no ramp: the system volume is used as is.

## 5. "No sound" checklist
1. `speaker-test` works? If not, it's the OS audio setup, not Alarm+.
2. **Test sound** in the web UI: does the log show an error? Is "audio players" empty?
3. `which mpv` finds it? (`sudo apt install mpv`)
4. Wrong device: run `mpv --audio-device=help` and set `audio.device`.
5. Volume muted in `alsamixer` (`M` = muted).
6. Using Bluetooth/PipeWire: see the note in step 2.
