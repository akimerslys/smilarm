# Integrations: hooks, webhook, REST API, OpenClaw

## Hooks
Smilarm can run a shell command when an alarm **starts** (`hooks.on_ring`) and when it **ends** (`hooks.on_dismiss`), set in **Settings → After-alarm commands**
(editable only when an access token is set; see Security). It runs in the background and never delays the alarm.

| Variable | Value |
|---|---|
| `ALARM_EVENT` | `ring` or `dismiss` |
| `ALARM_ID`, `ALARM_LABEL`, `ALARM_TIME` | the alarm that fired (`test` / `now` for a test ring) |
| `ALARM_REASON` | on dismiss: `smile`, `manual` or `timeout` |
| `ALARM_SECONDS` | on dismiss: how long it rang |

- The command goes through a shell (`~`, `&&`, quotes and pipes work), starts in the user's home directory and is killed after `hooks.timeout_seconds`.
- It runs on the device that runs Smilarm: **inside the container for Docker installs**, as your user for native installs. Use absolute paths for programs.
- Output (last 500 characters) and the exit code appear under **System & debug → Last hook runs**. A failing or timed-out hook does not affect the alarm.

**Example: start the morning radio on a second Pi when the user woke up.** The command reaches other devices with SSH, so create a key and authorise it once.
Docker (keys live in the state volume):
```bash
docker exec smilarm bash -c 'mkdir -p /state/ssh && ssh-keygen -t ed25519 -N "" -f /state/ssh/id_ed25519'
docker exec -it smilarm ssh-copy-id -i /state/ssh/id_ed25519.pub pi@rpi3.local        # asks the Pi's password once
```
Then set `hooks.on_dismiss` to:
```
[ "$ALARM_REASON" = smile ] && ssh -i /state/ssh/id_ed25519 -o BatchMode=yes -o StrictHostKeyChecking=accept-new -o UserKnownHostsFile=/state/ssh/known_hosts pi@rpi3.local 'mpc clear && mpc add http://stream.example.org/radio.mp3 && mpc play'
```
Native: the same without the `-i`/`UserKnownHostsFile` options, after `ssh-keygen` and `ssh-copy-id` as your user. Scripts of your own can live in `/state` (Docker) or anywhere (native).

**Security:** a hook is a command your device runs, so the UI lets you edit hooks (and the webhook URL) **only when `server.token` is set**, and every request then needs it.
Without a token these fields are read-only. You can always edit them in `config.toml` (`smilarm config`).

## Webhook
`events.webhook_url` receives JSON when an alarm starts and ends (background POST, 5 s timeout; a dead endpoint never delays the alarm):
```json
{"source": "smilarm", "event": "ringing", "at": "2026-10-25T07:00:00", "alarm": {"id": "…", "time": "07:00", "label": "work"}}
{"source": "smilarm", "event": "dismissed", "at": "…", "alarm": {…}, "reason": "smile", "seconds": 12}
```
`reason` is `smile`, `manual` (Stop button) or `timeout` (the failsafe ended it; the user may still be asleep).

## REST API
Everything in the UI is an API call (all under `/api`; with an access token add `Authorization: Bearer <token>` or `?token=`).

| | |
|---|---|
| `GET /api/state` | alarms, next alarm, ringing state |
| `POST /api/alarms`, `PUT/DELETE /api/alarms/<id>` | create / change / delete (`time` "HH:MM", `days` 0–6 Mon=0, `label`, `enabled`, `once`) |
| `POST /api/test`, `POST /api/dismiss` | test ring; stop a ring (a test ring always, a real alarm only if allowed in Settings or the camera failed) |
| `GET/PUT/DELETE /api/settings`, `POST /api/settings/reload` | every setting with its allowed range; partial update; restore defaults; re-read the file |
| `GET /api/setup`, `GET /api/system`, `GET /api/logs?n=`, `GET /api/debug`, `GET /api/audio-devices` | first-run checklist, host health, log, smile numbers, output devices |
| `POST /api/sound-test`, `POST /api/restart` | play the sound for 6 s; restart the service (never during a ring) |
| `GET /api/events` | server-sent events (state changes) |
| `GET /api/preview.mjpg`, `GET /api/smile-test.mjpg` | live video streams (one viewer at a time: the ESP32 serves one stream) |

## OpenClaw
`smilarm openclaw` installs the skill (`SKILL.md`) into `~/.openclaw/workspace/skills/smilarm`. Set `ALARM_URL` (and `ALARM_TOKEN` if you use a token) in
the OpenClaw environment. It documents the API above (alarms, settings, diagnostics) and the rules the agent must follow (never bypass the smile, never
set hooks it was not given). The skill folder and format were not verified against a real OpenClaw installation.
