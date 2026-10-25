# Playing the alarm on another device with moOde Audio

Smilarm can ring through a second Raspberry Pi (for example an Rpi3 with speakers) running
[moOde Audio](https://moodeaudio.org). Smilarm stays on the main device (Rpi5) and only *remote-controls*
moOde's MPD server over the network. Nothing from Smilarm is installed on the moOde device.

```
Pi 1 (smilarm) ──MPD commands, TCP 6600──▶ Rpi3 (moOde/MPD) ──▶ speakers
        ▲                                         │
        └──────── HTTP GET /sound/current ◀───────┘   (moOde pulls the sound from the Rpi5)
```

If the moOde device is off or unreachable, the alarm falls back to playing on the Rpi5
(`moode.fallback_local = true`), so you never oversleep because of a dead speaker.

## 1. Install moOde on the second device
1. Download the moOde image for your Pi from <https://moodeaudio.org> and flash it to an SD card
   (Raspberry Pi Imager → "Use custom", or balenaEtcher). A 1 GB Rpi3 is plenty.
2. Boot it, connect it to the same network as the Rpi5 (Ethernet is the most reliable for an alarm).
3. Open `http://moode.local` (or the device's IP) in a browser.

## 2. Configure audio output in moOde
In moOde's **Audio** settings, choose the output device (I2S DAC, USB DAC, headphone jack or HDMI) and
check that music plays at all with a normal radio station before involving Smilarm.

**Volume type:** if moOde's volume is set to *Fixed (0dB)*, MPD ignores `setvol`, and Smilarm's gradual
volume ramp will do nothing (the alarm plays at whatever level moOde is set to). Use *Software* or
*Hardware* volume if you want the ramp.

### MPD settings page (moOde 0.24)
Smilarm needs **no changes** on moOde's *MPD* settings page: the defaults work. For reference
(DSD, SoX resampling, ReplayGain, buffers, input cache, HTTP proxy, and so on):

| Setting | What to do |
|---|---|
| DSD support, Thesycon patch | Leave as is. Irrelevant for an alarm. |
| SoX resampling | Leave **off**. It only adds CPU load on an Rpi3. |
| ReplayGain / Normalize volume | Leave **off**. If you enable ReplayGain and your files carry gain tags, the alarm gets quieter or louder than expected and the volume ramp feels wrong. |
| Audio buffer 4 MB / Output buffer 128 MB / Max queue items | Defaults are fine. The alarm file is small and the queue holds one file. |
| Input cache (experimental) | Leave off. Turn it on only if you stream a long HTTP file or radio and it stutters. |
| HTTP proxy | Leave empty, unless your network forces a proxy for the Rpi5 URL. |
| Close on pause, Silence before stop, Device buffer/period | Leave as is. Smilarm never pauses; it sends `stop`. |
| Log level | Optional: raise it temporarily while debugging, then set it back. |

**Important:** the page says *"MPD settings cannot be saved when Audio output in Bluetooth Control is set to
'Bluetooth speaker' or when Multiroom Sender or Receiver are On."* That only matters if you want to change a
setting there. But if the audio output is a Bluetooth speaker, or the device is a multiroom receiver, MPD's
output is not the local speakers, so the alarm may be silent. Use the local output (I2S/USB/headphones/HDMI)
for the alarm.

What actually matters is **outside** that page:
1. **Audio output device** selected and working (moOde *Audio* settings).
2. **Volume type** not *Fixed (0dB)* if you want the gradual volume ramp (see step 2).
3. **MPD reachable on port 6600** from the Rpi5 (step 4). moOde's MPD listens on the network by default.
4. For `library_dir`: the sound files are in the music library and the database knows them
   (`mpc update ALARM_TRACKS`).

> **Smilarm in Docker?** With the default host networking everything below works unchanged: the container has the
> device's own address, so moOde can fetch `/sound/current` from it. With **bridge networking** the container's private address is
> unreachable from the moOde device: set **Settings → `server.public_url`** to the Docker host's address, e.g. `http://192.168.1.20:8080`.
> Either way use IP addresses (`moode.local` does not resolve inside a container). "The Rpi5" below means the device running Smilarm.

## 3. Give the device a stable address
Reserve an IP for it in your router (DHCP reservation). `moode.local` (mDNS) often does **not** resolve
from a Raspberry Pi running Linux; an IP address always works.

## 4. Check that the Rpi5 can reach MPD
On the Rpi5:
```bash
printf 'status\nclose\n' | nc 10.0.0.50 6600     # replace with your moOde IP
```
You should see `OK MPD ...` followed by status lines. (`sudo apt install netcat-openbsd` if `nc` is missing;
or install `mpc` and run `mpc -h 10.0.0.50 status`.)

If it times out: check that moOde is running, the IP is right, and nothing blocks port 6600
(moOde's MPD listens on all interfaces by default).

## 5. Check that moOde can reach the Rpi5
moOde downloads the alarm sound from the Rpi5 over HTTP. From the moOde device (ssh in as `pi`):
```bash
curl -I http://10.0.0.20:8080/sound/current      # replace with your Rpi5 IP
```
Expect `HTTP/1.1 200 OK`. (The `/sound/...` URLs are deliberately not behind `server.token`, so MPD can fetch them;
they only serve the alarm sound.)
If you use a firewall on the Rpi5 (`ufw`), allow the web port from the moOde device.

## 6. Point Smilarm at it
In the web UI open **Settings** (Sound → `audio.backend`, Second speaker → `moode.*`). In `config.toml` terms:
```toml
[audio]
backend = "moode"        # "both" = Rpi5 and moOde ring together

[moode]
host = "10.0.0.50"       # moOde IP
port = 6600
fallback_local = true
# url = "http://10.0.0.20:8080/sound/current"   # optional, see below
```
The settings apply immediately; press **System & debug → Test sound**.
The "reachability" row in that panel shows whether Smilarm can currently reach moOde.

### Which sound does moOde play?
- Default: the sound Smilarm picked for this alarm (built-in tone, your `audio.sound`, or a random file from
  `audio.sound_dir`), served by the Rpi5 at `/sound/current`. Nothing needs copying to the moOde device.
- `moode.url`: any URL MPD can play, e.g. an internet radio stream
  (`http://stream.example.org/radio.mp3`), or a file on a NAS.
- If `audio.sound` itself is an `http(s)` URL, that URL is used as is.

### Play files that already live on the moOde device (no transfer)
If your alarm sounds are in moOde's own music library, point Smilarm at a folder of it. A random sound file
from that folder is chosen at every alarm and played by the moOde device itself, exactly like
`mpc listall ALARM_TRACKS | shuf -n 1`:
```toml
[moode]
host = "10.0.0.50"
library_dir = "ALARM_TRACKS"     # relative to moOde's library, as `mpc listall` shows it
```
After adding files to that folder, refresh MPD's database once on the moOde device: `mpc update ALARM_TRACKS`
(or "Update library" in moOde). If the folder has no sound files, the error says so (see the log in
*System & debug*) and the alarm falls back to the Rpi5. `library_dir` takes precedence over `moode.url`.

## What Smilarm does to MPD
On ring it runs: `stop`, `clear`, `add <url>`, `repeat 1`, `single 0`, `setvol <n>`, `play`, then `setvol`
every second during the ramp, and `stop` when you smile. **It clears moOde's current queue**, so don't
rely on a queue that is playing on that device. Restart your playlist after the alarm, or use the
after-alarm hook ([integrations.md](integrations.md#hooks)) to start it again, for example with `mpc`.

## Troubleshooting
**Nothing plays on the moOde device?** In order:
1. `audio.backend` must be `moode` or `both` (Settings → audio.backend). The default is `local`.
2. *System & debug → reachability* must show `moode: ok`. If not, fix host/port (step 4).
3. Run **Test sound** and read the log: `MoodePlayer failed: ...` tells you why (unreachable, `No such song`, empty library folder).
4. Test MPD directly from the Rpi5, the way Smilarm does: `mpc -h <moode-ip> clear && mpc -h <moode-ip> add ALARM_TRACKS/yourfile.mp3 && mpc -h <moode-ip> play`.
5. If that works but Smilarm doesn't, `moode.url`/`library_dir` is wrong, or moOde can't fetch the Rpi5 URL (step 5).

| Symptom | Likely cause |
|---|---|
| Alarm plays on the Rpi5 only | moOde unreachable (wrong IP, off, port 6600 blocked) → fallback. Check the log in *System & debug*. |
| Log: `MoodePlayer failed: ... ACK ... No such song` | moOde can't fetch the URL: Rpi5 firewall or wrong port (see step 5). |
| Sound starts but volume never rises | moOde volume type is *Fixed (0dB)* (see step 2). |
| Works, but 10–20 s late | MPD is buffering an HTTP stream. Use a local file via your own `moode.url`, or accept the delay. |
| `moode.local` doesn't resolve | Use the IP address. |
