# Troubleshooting

Start with **System & debug** in the web UI (health, reachability, the live log) and `smilarm logs`.

| Symptom | What to check |
|---|---|
| Alarm rings at the wrong hour | Settings → `general.timezone`. A container's clock is UTC. |
| "camera error" / no video | The log and the **Smile test** table spell out the cause: wrong IP, connection refused, no answer, the ESP32 is busy with another viewer, a `.local` name that does not resolve. Use the camera's IP address. |
| Camera drops off the network now and then (`no answer from 10.x.x.x:81`, `No route to host`), often when the flashlight turns on | The ESP32-CAM is power-starved: 5 V / 2 A supply, short cable, lower `camera.flashlight_level`, flash the current sketch ([hardware.md](hardware.md#the-camera-esp32-cam)). |
| Smile never counts | **Smile test:** is a face found? If not, check `camera.rotate`. If the score stays below the threshold, lower `smile.threshold`. |
| No sound | **Test sound**, then the log (`player exited at once …`), the chosen `audio.device`: [hardware.md](hardware.md#speakers). |
| Nothing on the second speaker | **System & debug → reachability**, then [moode.md](moode.md). In bridge networking set `server.public_url`. |
| "Simple smile detector in use" | MediaPipe could not load; the log says why. The image ships the model: keep `smile.model` at `data/face_landmarker.task`. |
| Pi or ESP32 freezes/reboots at random | `throttled` in **System & debug**: under-voltage means a weak power supply. |
| Web UI unreachable after changing the port | Open the new port. Locked out by a token? `docker exec smilarm grep token /state/config.toml` (native: `grep token ~/.smilarm/config.toml`). |
| Container restarts in a loop with "address already in use" | Another program owns the port (host networking shares the Pi's ports): `sudo ss -ltnp \| grep :8080`; change `server.port` with `smilarm config`, then `smilarm restart`. |
| `install.sh`: daemon cannot be reached | `sudo systemctl start docker`; add yourself to the `docker` group (`sudo usermod -aG docker $USER`, log in again) or the installer uses `sudo`. |
| `smilarm: command not found` | Add `~/.local/bin` to your PATH. |
| Disk is filling up | `smilarm clean` ([install.md](install.md#freeing-disk-space)). |
