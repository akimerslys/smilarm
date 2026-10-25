"""The device's wall clock in the configured timezone (`general.timezone`).

Alarms are naive local times ("07:00"). In a container the system timezone is usually UTC, so the
timezone is a setting instead of something inherited from the host.
"""
from datetime import datetime
from zoneinfo import ZoneInfo


def valid_timezone(name: str) -> bool:
    try:
        ZoneInfo(name)
        return True
    except Exception:
        return False


def now(cfg) -> datetime:
    """Naive datetime on the wall clock of `general.timezone` (empty = the system timezone)."""
    name = cfg.general.timezone
    if name and valid_timezone(name):
        return datetime.now(ZoneInfo(name)).replace(tzinfo=None)
    return datetime.now()
