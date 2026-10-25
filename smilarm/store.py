"""Alarm storage (atomic JSON) and next-fire calculation."""
import json
import os
import re
import uuid
from datetime import datetime, timedelta
from pathlib import Path

_TIME = re.compile(r"^([01]\d|2[0-3]):[0-5]\d$")


def clean(raw: dict, old: dict | None = None) -> dict:
    """Validate/normalise an alarm dict; raises ValueError."""
    a = dict(old or {"id": uuid.uuid4().hex[:8], "enabled": True, "days": [], "once": False, "label": ""})
    t = raw.get("time", a.get("time"))
    if not isinstance(t, str) or not _TIME.match(t):
        raise ValueError("time must be HH:MM")
    a["time"] = t
    if "days" in raw:
        days = raw["days"]
        if not isinstance(days, list) or any(isinstance(d, bool) or not isinstance(d, int) or not 0 <= d <= 6 for d in days):
            raise ValueError("days: list of 0..6 (Mon=0); empty = every day")
        a["days"] = sorted(set(days))
    for k in ("enabled", "once"):
        if k in raw:
            a[k] = bool(raw[k])
    if "label" in raw:
        a["label"] = str(raw["label"])[:60]
    return a


def next_fire(alarm: dict, now: datetime) -> datetime | None:
    if not alarm.get("enabled"):
        return None
    h, m = map(int, alarm["time"].split(":"))
    days = alarm.get("days") or range(7)
    for add in range(8):
        d = (now + timedelta(days=add)).replace(hour=h, minute=m, second=0, microsecond=0)
        if d > now and d.weekday() in days:
            return d
    return None


class Store:
    def __init__(self, path: Path):
        self.path = path
        self.alarms: list[dict] = []
        if path.exists():
            self.alarms = json.loads(path.read_text(encoding="utf-8"))

    def save(self):
        self.path.parent.mkdir(parents=True, exist_ok=True)
        tmp = self.path.with_suffix(".tmp")
        tmp.write_text(json.dumps(self.alarms, ensure_ascii=False, indent=1), encoding="utf-8")
        os.replace(tmp, self.path)

    def get(self, id_: str) -> dict | None:
        return next((a for a in self.alarms if a["id"] == id_), None)

    def add(self, raw: dict) -> dict:
        a = clean(raw)
        self.alarms.append(a)
        self.save()
        return a

    def update(self, id_: str, raw: dict) -> dict | None:
        old = self.get(id_)
        if not old:
            return None
        new = clean(raw, old)
        old.update(new)
        self.save()
        return old

    def delete(self, id_: str) -> bool:
        n = len(self.alarms)
        self.alarms = [a for a in self.alarms if a["id"] != id_]
        if len(self.alarms) != n:
            self.save()
            return True
        return False

    def next(self, now: datetime):
        c = [(f, a) for a in self.alarms if (f := next_fire(a, now))]
        return min(c, key=lambda x: x[0]) if c else None
