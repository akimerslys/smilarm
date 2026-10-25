# Uninstalling Alarm+

```bash
cd alarm+                         # the project folder
bash uninstall.sh --dry-run       # shows what would happen, changes nothing
bash uninstall.sh                 # interactive
```
`uninstall.sh` finds what is installed and removes it. If **both** a Docker and a native install exist it will not guess: it asks (or, with
`--yes`, refuses) and tells you to pick one with `--docker` or `--native`, so removing one never takes the other with it.

## Docker install (the normal case)
| Step | When | Details |
|---|---|---|
| Back up your data | only with `--purge` (or "yes" when asked) | writes `~/alarmplus-backup-<date>.tar.gz` (the whole `/state` volume: `config.toml`, alarms, logs). Skip with `--no-backup`. |
| Remove the container and image | always | `docker compose down`, removes the `alarmplus` container, its network and the `alarmplus:latest` image. |
| Delete the data volume | only with `--purge` (or "yes" when asked) | `docker volume rm alarmplus-state`. Never done if the backup failed. |

Without `--purge` the volume `alarmplus-state` stays, so **reinstalling brings back your settings and alarms**. With `--yes` there are
no questions and your data is still kept (unless you also pass `--purge`).

| Option | Meaning |
|---|---|
| `--dry-run` | print what would be done; change nothing |
| `--yes` | no questions; safe defaults |
| `--purge` | also delete the data volume (backup first) |
| `--no-backup` | with `--purge`: don't write the tarball |
| `--plain` | no animation (for logs) |

Restore a backup: see [docker.md](docker.md#backup-and-restore).

## Native install (optional, no Docker)
`bash uninstall.sh --native` does the following; it takes the same `--dry-run`, `--yes`, `--purge`, `--no-backup` and `--plain`, plus two more:

| Step | When | Details |
|---|---|---|
| Stop and remove the service | always (if installed) | `systemctl disable --now alarmplus`, deletes `/etc/systemd/system/alarmplus.service`, reloads systemd. Stopping the service also stops any `mpv` it started. |
| Remove the Python environment | always (if present) | deletes `.venv/` (usually several hundred MB). |
| Delete your data and config | only if you say yes, or with `--purge` | deletes `data/` (alarms, the face model, logs) and `config.toml` (+ `.bak`), after writing `~/alarmplus-backup-<date>.tar.gz`. |
| Remove the OpenClaw skill | only if you say yes, or with `--openclaw` | deletes `~/.openclaw/workspace/skills/alarm-plus` (or `$OPENCLAW_WORKSPACE/skills/alarm-plus`). |
| Remove system packages | only if you say yes, or with `--packages` | `apt-get remove mpv libgles2 libegl1 python3-venv`. `nano` and `libgl1` are left alone; other programs may use these, which is why it is opt-in. |

With `--yes` only the service and `.venv` are removed.

The OpenClaw skill is not part of the Docker image, so `bash uninstall.sh` leaves it; remove it with `rm -rf ~/.openclaw/workspace/skills/alarm-plus`
or `bash uninstall.sh --native --openclaw`.

The scripts are idempotent: running them again when nothing is left prints "Nothing to remove".

## What is never touched
- **The project folder.** Delete it yourself when you are done: `cd .. && rm -rf alarm+`.
- **Docker itself**, and any other containers or images.
- **The ESP32-CAM firmware.** It is harmless without Alarm+; unplug it or flash something else.
- **The second speaker (moOde).** Nothing was installed there; Alarm+ only sent MPD commands (and cleared its queue when it rang).
- Your router's DHCP reservations, SSH keys you created for hooks, and anything your own hook scripts changed.

## Manual uninstall (if the scripts are gone)
```bash
# Docker
docker compose down --rmi local             # from the project folder; add -v to also delete the data volume
docker volume rm alarmplus-state            # the data (config, alarms): only if you really want it gone

# native
sudo systemctl disable --now alarmplus
sudo rm /etc/systemd/system/alarmplus.service && sudo systemctl daemon-reload
rm -rf ~/alarm+                              # the project folder incl. .venv, data and config.toml
```
