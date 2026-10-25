# Optional: installing without Docker (native)

Docker is the recommended way ([docker.md](docker.md)). The native install is for people who don't want Docker: it installs the
dependencies into a Python virtual environment inside the project folder and runs Alarm+ as a systemd service.

Requirements: Raspberry Pi OS (Bookworm, 64-bit) or another Debian-based system with `sudo`, Python 3.11+, about 1 GB of free disk.

## Install
```bash
git clone <repo> alarm+ && cd alarm+
bash install.sh --native
```
The installer shows seven numbered steps with a spinner, what is downloading right now and how much (apt packages and Python libraries
with running totals; the face model with a progress bar, speed and ETA), and logs everything to `data/install.log`.
It installs `mpv`, `nano` and a few libraries with `apt`, creates `.venv/`, installs the Python packages, downloads the face model,
generates `config.toml` with every default value (an existing file is never overwritten), and registers and starts the
`alarmplus` systemd service.

Options: `--clean-cache` frees the pip and apt download caches at the end (a later update downloads again), `--no-edit` skips opening `config.toml` in `nano` (the editor step is optional: **everything can be set in the web UI**
afterwards), `--plain` turns the animation off, `--demo` previews the installer with fake downloads and installs nothing.
Re-running it is safe, and is also how you update (`git pull && bash install.sh --native`).

Then open `http://<this-device>:8080` and finish the setup in **Settings** exactly as described in [docker.md](docker.md#first-time-setup-in-the-web-ui).

## Differences from Docker
| | Docker | Native |
|---|---|---|
| Config + data | volume `alarmplus-state` (`/state`) | `config.toml` and `data/` in the project folder |
| Restart | `docker compose restart alarmplus` (or the UI) | `sudo systemctl restart alarmplus` (or the UI) |
| Logs | `docker compose logs -f alarmplus` | `journalctl -u alarmplus -n 100 --no-pager` |
| Hooks run | inside the container | on the device, as your user (`ssh` keys in `~/.ssh`) |
| Timezone | Settings → `general.timezone` | the system timezone, unless you set `general.timezone` |
| Bluetooth speaker | not supported | works through the desktop audio server (see [sound_output.md](sound_output.md)) |
| Remove | `bash uninstall.sh` | `bash uninstall.sh --native` |

The service runs `python -m alarmplus --config <project>/config.toml`. Useful checks:
```bash
python -m alarmplus --check-config config.toml     # validate a hand-edited file
sudo ss -ltnp | grep :8080                           # is the port taken?
sudo systemctl status alarmplus
```

## Uninstall
`bash uninstall.sh --native` (preview with `--dry-run`). See [uninstall.md](uninstall.md).
