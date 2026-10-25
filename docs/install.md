# Install, update, remove

Two scripts do everything; the rest of this page is optional extras.

```bash
bash install.sh      # install (and update)
bash uninstall.sh    # remove
```

## Quick start
```bash
git clone <repository-url> smilarm && cd smilarm
bash install.sh
```
It checks Docker, gets the image (a prebuilt one, or built from this clone), starts the container and waits until it is healthy, showing
what is downloading and how much. Then open **`http://<your-pi>:8080`**: the **Setup** card at the top lists what to do (timezone, camera address,
access token). You configure everything in the web UI; there is no file to edit.

No Docker yet? `bash install.sh --install-docker` installs it with Docker's official script. Without Docker at all: `bash install.sh --native`.

## You can delete the folder afterwards
Smilarm does not live in the folder you cloned. The installer copies what it needs into **`~/.smilarm`** and installs a `smilarm` command, so you
can delete the clone (and later update, back up or uninstall from anywhere).

```
~/.smilarm/
  bin/smilarm            the command (also linked into ~/.local/bin)
  lib/                   helper scripts it uses (including the uninstaller)
  compose/               docker-compose.yml, docker-compose.sound.yml, .env  (+ your own docker-compose.local.yml)
  share/                 the OpenClaw skill and config.example.toml
  mode, version, install.log
  (native install only)  app/  venv/  config.toml  data/
```
Your settings and alarms are not in this folder for Docker installs: they live in the Docker volume **`smilarm-state`** (`/state` in the container).
Override the location with `SMILARM_HOME`.

## The `smilarm` command
| | |
|---|---|
| `smilarm status` | running? address, version, mode |
| `smilarm logs [-f]` | recent log lines (or follow them) |
| `smilarm start` / `stop` / `restart` | |
| `smilarm update` | newest version; settings and alarms are kept |
| `smilarm backup [FILE]` / `restore FILE` | config + alarms to/from a tarball (owner-only file: it holds your access token) |
| `smilarm config` | edit `config.toml` by hand (you normally use Settings in the UI) |
| `smilarm clean` | free download/build caches |
| `smilarm openclaw` | install the OpenClaw skill |
| `smilarm uninstall` | remove Smilarm |

If `smilarm` is not found, add `~/.local/bin` to your PATH (`export PATH="$HOME/.local/bin:$PATH"`); the installer prints this when needed.

## Install options
Options go after the script name, e.g. `bash install.sh --build --clean-cache`.

| Option | Docker | Native | |
|---|---|---|---|
| `--native` | | | install without Docker (systemd service + Python venv in `~/.smilarm`) |
| `--plain` | ✓ | ✓ | no animation (for logs) |
| `--demo` | ✓ | ✓ | simulate the whole run with fake downloads; installs nothing |
| `--no-start` | ✓ | | install but do not start |
| `--build` | ✓ | | build the image from this clone even if a prebuilt one exists |
| `--no-migrate` | ✓ | | do not move data over from an old "Alarm+" install |
| `--install-docker` | ✓ | | install Docker first (official script from get.docker.com) |
| `--clean-cache` | ✓ | ✓ | afterwards free the build cache (Docker) / pip and apt caches (native) |
| `--clean-only` | ✓ | | only free the Docker build cache and old Smilarm images; install nothing |
| `--no-edit` | | ✓ | do not open `config.toml` in an editor at the end |

Environment variables: `SMILARM_HOME` (default `~/.smilarm`), `SMILARM_BIN_DIR` (default `~/.local/bin`), `SMILARM_TAG` (image tag, default `latest`),
`SMILARM_REPO` (see "Publishing" below).

## Updating
`smilarm update`. With a prebuilt image it pulls and recreates the container; if the install was built from a clone it fetches the newest sources
(once a download address is configured) or you run `git pull && bash install.sh` in your clone. Re-running `install.sh` is also an update.
Your volume (settings, alarms) is never touched; new settings appear in the UI with their defaults.

## Backup and restore
`smilarm backup` writes `~/smilarm-backup-<date>.tar.gz` (the config, alarms and logs, readable only by you). `smilarm restore FILE` puts it back.
Moving to a new Raspberry Pi: install there, then `smilarm restore` the backup.

## Docker details
- **Image:** the installer pulls `ghcr.io/<owner>/smilarm` when the project is published, otherwise builds `smilarm:local` from the clone
  (about 3 minutes on a Pi 5, 1.5 GB). The Python libraries, the face model and the fallback detector's cascade files are all inside the image.
- **Compose files:** `docker-compose.yml` is the plain deployment (host networking, the `smilarm-state` volume, restart policy). The installer adds
  `docker-compose.sound.yml` (this device's speakers, `/dev/snd`) only when the device has sound hardware, so a server without sound does not fail to
  start; then Smilarm plays on remote (moOde) speakers only and the installer says so. Your own additions go into
  `~/.smilarm/compose/docker-compose.local.yml` (picked up on the next `install.sh`; updates keep it).
- **Port:** if 8080 is taken (qBittorrent and many others use it) a first install starts on the next free port and prints the address.
  Change it later in Settings → `server.port` (then **Restart now**).
- **USB or Pi camera:** put this in `docker-compose.local.yml`, run `bash install.sh`, set `camera.source` to `0`:
  ```yaml
  services:
    smilarm:
      devices:
        - /dev/video0:/dev/video0
  ```
- **Bridge networking** (if host networking is not possible): in `docker-compose.local.yml`
  ```yaml
  services:
    smilarm:
      network_mode: bridge
      ports:
        - "8080:8080"
  ```
  and set Settings → `server.public_url` to the Docker host's address (`http://192.168.1.20:8080`) so the moOde speaker can reach the app.
  Hostnames like `moode.local` do not resolve inside a container: use IP addresses.
- **Sound, PulseAudio/Bluetooth:** see [hardware.md](hardware.md#speakers).
- **Hooks that SSH to other devices** run inside the container: see [integrations.md](integrations.md#hooks).
- **Everyday:** `docker ps` shows `healthy`; `docker exec -it smilarm bash` opens a shell.

## Native install (optional, no Docker)
`bash install.sh --native` installs `mpv` and a few libraries with `apt`, puts the app, a Python venv, `config.toml` and `data/` into `~/.smilarm`,
and runs it as the `smilarm` systemd service. Debian-based Linux with `sudo`, Python 3.11+ and about 1 GB free. Same Setup card and Settings as Docker.

| | Docker | Native |
|---|---|---|
| Config + data | volume `smilarm-state` | `~/.smilarm/config.toml`, `~/.smilarm/data/` |
| Hooks run | inside the container | on the device, as your user (`ssh` keys in `~/.ssh`) |
| Timezone | Settings → `general.timezone` | the system timezone, unless you set it |
| Bluetooth speaker | via a shared PulseAudio socket | works through the desktop audio server |
| Update | `smilarm update` | `git pull && bash install.sh --native` |
| Logs | `smilarm logs` | `smilarm logs` (journalctl) |

## Freeing disk space
Docker builds leave a **build cache** (a few GB) that Smilarm does not need to run; it only speeds up the next build. Updates also leave the old image
behind. `smilarm clean` (or `bash install.sh --clean-only`) runs `docker builder prune -af` and removes old untagged Smilarm images. It frees **all unused build
cache on the machine** (shared by every Docker project, which then rebuilds a little slower once) and never touches containers, volumes or other images.
The installer mentions the cache when it is large.

## Moving from "Alarm+"
The project used to be called Alarm+ (container `alarmplus`, volume `alarmplus-state`). `bash install.sh` finds such an install, stops the old container,
and **copies its config and alarms** into `smilarm-state`; the old volume is kept (remove it later: `docker volume rm alarmplus-state`). Skip with
`--no-migrate`. The webhook payload's `source` is now `smilarm`; the OpenClaw skill is now named `smilarm` (`smilarm openclaw`).
A native "Alarm+" service (`alarmplus.service`) is not migrated: install Smilarm natively and copy `config.toml` into `~/.smilarm/`.

## Uninstall
```bash
bash uninstall.sh --dry-run      # preview
bash uninstall.sh                # interactive
smilarm uninstall                # the same, without the clone
```
It finds the Docker and/or native install. **Your config and alarms are kept unless you say so**: `--purge` deletes them after writing
`~/smilarm-backup-<date>.tar.gz`. If both a Docker and a native install exist it refuses to guess: pass `--docker` or `--native`.

| Option | Meaning |
|---|---|
| `--yes` | no questions: remove the program, keep your data |
| `--purge` | also delete config and alarms (backup first; `--no-backup` skips it; never deleted after a failed backup) |
| `--docker` / `--native` | only that install |
| `--openclaw` | also remove the OpenClaw skill |
| `--packages` | native only: also `apt remove` mpv and a few libraries (other programs may use them) |
| `--dry-run` | show what would happen, change nothing |

Removes the container, its image(s), the data volume (only with `--purge`) and `~/.smilarm` (keeping your `docker-compose.local.yml`). Never touched:
Docker itself, the ESP32 firmware, the moOde device, and your clone of the repository (delete it yourself).

## Publishing (maintainers)
Until the project is published, installs build the image from the clone and `smilarm update` needs a new clone. To publish:
1. Push the repository to GitHub and set `SMILARM_REPO=<owner>/smilarm` as the default in `scripts/lib/common.sh` (or export it).
2. `.github/workflows/docker.yml` builds a multi-arch image (amd64 + arm64) and pushes it to `ghcr.io/<owner>/smilarm` on every push to `main` and on tags;
   make the package public.
3. Users can then install with one line: `curl -fsSL https://raw.githubusercontent.com/<owner>/smilarm/main/install.sh | bash`.
