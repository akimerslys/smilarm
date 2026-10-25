# Uninstalling Alarm+

```bash
cd ~/alarm+                              # the project folder
bash deploy/uninstall.sh --dry-run       # shows what would happen, changes nothing
bash deploy/uninstall.sh                 # interactive
```

## What it does
| Step | When | Details |
|---|---|---|
| Stop and remove the service | always (if installed) | `systemctl disable --now alarmplus`, deletes `/etc/systemd/system/alarmplus.service`, reloads systemd. Stopping the service also stops any `mpv` it started. |
| Remove the Python environment | always (if present) | deletes `.venv/` (the installed libraries, usually several hundred MB). |
| Delete your data and config | only if you say yes, or with `--purge` | deletes `data/` (alarms, the face model, logs, the built-in tone) and `config.toml` (+ `.bak`). **A backup tarball `~/alarmplus-backup-<date>.tar.gz` is written first** (skip with `--no-backup`). |
| Remove the OpenClaw skill | only if you say yes, or with `--openclaw` | deletes `~/.openclaw/workspace/skills/alarm-plus` (or `$OPENCLAW_WORKSPACE/skills/alarm-plus`). |
| Remove system packages | only if you say yes, or with `--packages` | `apt-get remove mpv libgles2 libegl1 python3-venv`. `nano` and `libgl1` are left alone. Other programs may use these packages, which is why this is opt-in. |

With `--yes` there are no questions: only the service and `.venv` are removed, and everything you might want to keep stays.

## Options
| Option | Meaning |
|---|---|
| `--dry-run` | print what would be done; change nothing (no `sudo` needed) |
| `--yes` | no questions; safe defaults (see above) |
| `--purge` | also delete `data/` and `config.toml` (with a backup) |
| `--no-backup` | with `--purge`: don't write the tarball |
| `--openclaw` | also remove the OpenClaw skill |
| `--packages` | also apt-remove the packages the installer added |
| `--plain` | no animation (for logs) |

The script is idempotent: running it again when nothing is left prints "Nothing to remove".

## What it does not touch
- **The project folder.** Delete it yourself when you are done:
  ```bash
  cd .. && rm -rf alarm+
  ```
  Keep the backup tarball if you may want your alarms back; to restore, unpack it inside a fresh checkout:
  `tar -xzf ~/alarmplus-backup-<date>.tar.gz` and run `bash deploy/install.sh` again (it keeps an existing `config.toml`).
- **The ESP32-CAM firmware.** It keeps streaming whenever someone opens the stream, but is harmless without Alarm+. Unplug it or flash
  something else.
- **The second speaker (moOde).** Nothing was installed there. Alarm+ only sent MPD commands; it did clear moOde's queue when it rang.
- **Your router's DHCP reservations**, SSH keys you created for hooks, and anything your own hook scripts changed.

## Manual uninstall (if the script is gone)
```bash
sudo systemctl disable --now alarmplus
sudo rm /etc/systemd/system/alarmplus.service && sudo systemctl daemon-reload
rm -rf ~/alarm+                            # project folder, including .venv, data and config.toml
rm -rf ~/.openclaw/workspace/skills/alarm-plus     # only if you installed the OpenClaw skill
```
