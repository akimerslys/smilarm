# Running your own script when an alarm starts or ends

> **Docker (the default install):** the command runs **inside the container**, not on the host. `ssh` is installed there; keep the keys and
> your scripts in the `/state` volume. The step-by-step (key generation, `ssh-copy-id`, an example hook line) is in
> [docker.md](docker.md#hooks-that-ssh-to-other-devices). The text below applies to both installs; "the Rpi5" means the device running Alarm+.

Alarm+ can run a shell command when an alarm **starts** (`hooks.on_ring`) and when it **ends**
(`hooks.on_dismiss`). The command runs **on the device that runs Alarm+** (the Rpi5), as the service user,
in that user's home directory. It never blocks the alarm: it is started in the background.

```toml
[hooks]
on_dismiss = "~/after_alarm.sh"
timeout_seconds = 120        # the command is killed after this
```

## Environment variables
| Variable | Value |
|---|---|
| `ALARM_EVENT` | `ring` or `dismiss` |
| `ALARM_ID`, `ALARM_LABEL`, `ALARM_TIME` | the alarm that fired (`test` / `now` for a test ring) |
| `ALARM_REASON` | on dismiss: `smile`, `manual` or `timeout` |
| `ALARM_SECONDS` | on dismiss: how long it rang |

## Example: run something on the Rpi3 (or any other device)
The script runs on the Rpi5, so to act on another device it logs in there with SSH. Set up key-based login once,
as the user that runs the service:
```bash
ssh-keygen -t ed25519            # if you have no key yet
ssh-copy-id pi@rpi3.local        # or the device's IP
ssh -o BatchMode=yes pi@rpi3.local true && echo "passwordless ssh works"
```

Then `~/after_alarm.sh` on the **Rpi5**:
```bash
#!/usr/bin/env bash
# Runs on the Rpi5 after every alarm.
[ "$ALARM_REASON" = "smile" ] || exit 0          # only when the user actually woke up
ssh -o BatchMode=yes -o ConnectTimeout=5 pi@rpi3.local 'bash ~/after_alarm.sh'
```
```bash
chmod +x ~/after_alarm.sh
```
and your `~/after_alarm.sh` on the Rpi3 can do anything, e.g. start a playlist or morning radio:
```bash
#!/usr/bin/env bash
mpc clear && mpc add "http://stream.example.org/radio.mp3" && mpc play
```
You can also skip the wrapper and put the SSH call directly into the setting:
`on_dismiss = "ssh -o BatchMode=yes pi@rpi3.local 'bash ~/after_alarm.sh'"`.

## Notes
- The command goes through a shell, so `~`, `&&`, quotes and pipes work.
- Under systemd the `PATH` is minimal; use absolute paths for programs, and make scripts executable.
- Hooks run for every alarm and also for **Test ring**. Output (last 500 characters) and the exit code
  are logged, and shown under **System & debug → Last hook runs** in the web UI.
- A hook that fails or times out doesn't affect the alarm.

## Security
A hook is a command your Rpi5 runs on request, so changing it must not be open to the whole network.
- Edit hooks in `config.toml` any time.
- The web UI **Settings** panel lets you edit hooks (and the webhook URL) **only if `server.token` is set**,
  and every request then needs the token. Without a token these fields are read-only in the UI.
