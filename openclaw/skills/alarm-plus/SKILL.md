---
name: alarm-plus
description: Manage the user's smile-to-dismiss alarm clock (Alarm+ on the Raspberry Pi 5): list, create, enable/disable, delete alarms and trigger a test ring.
---

# Alarm+

Base URL: `http://rpi5.local:8080` (change to your Pi's address). If the server has a token set, add
`-H "Authorization: Bearer $ALARM_TOKEN"` to every request.

Alarm fields: `time` ("HH:MM", 24h), `days` (list 0-6, Mon=0; empty = every day), `label`, `enabled`, `once`.

```bash
curl -s $BASE/api/state                                   # alarms, next fire time, ringing state
curl -s -X POST $BASE/api/alarms -H 'Content-Type: application/json' \
     -d '{"time":"07:00","days":[0,1,2,3,4],"label":"work"}'
curl -s -X PUT  $BASE/api/alarms/<id> -H 'Content-Type: application/json' -d '{"enabled":false}'
curl -s -X DELETE $BASE/api/alarms/<id>
curl -s -X POST $BASE/api/test                            # ring now (smile to stop)
```

Rules:
- The alarm can only be stopped by smiling at the camera. Never try to bypass this; `/api/dismiss`
  is intentionally refused unless the camera has failed.
- Confirm with the user before deleting alarms or changing several at once.
- Alarm+ can push events (`ringing`, `dismissed` with `reason` = smile|manual|timeout and `seconds`) to an
  OpenClaw hook URL configured in `[events] webhook_url`; use them for morning routines.
