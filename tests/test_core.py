import asyncio
from datetime import datetime

import pytest

from alarmplus.smile import SmileGate
from alarmplus.store import Store, clean, next_fire


def test_gate_requires_hold():
    g = SmileGate(0.5, 2.5)
    assert not g.update(0.9, 0.0)
    assert not g.update(0.9, 2.0)
    assert g.update(0.9, 2.6)


def test_gate_resets_after_grace_but_tolerates_blink():
    g = SmileGate(0.5, 2.0, grace=0.6)
    g.update(0.9, 0.0)
    g.update(None, 0.4)  # blink / missed frame: tolerated
    assert not g.update(0.9, 1.0)
    g.update(0.1, 1.2)
    g.update(0.1, 2.0)  # gap > grace: reset
    assert g.progress == 0.0
    assert not g.update(0.9, 2.1)


def test_next_fire_weekdays():
    a = clean({"time": "07:00", "days": [0, 1, 2, 3, 4]})
    sat = datetime(2026, 10, 24, 12, 0)  # Saturday
    assert next_fire(a, sat) == datetime(2026, 10, 26, 7, 0)  # Monday
    assert next_fire(a, datetime(2026, 10, 26, 6, 59)) == datetime(2026, 10, 26, 7, 0)
    assert next_fire(a, datetime(2026, 10, 26, 7, 0)) == datetime(2026, 10, 27, 7, 0)  # strictly future


def test_disabled_and_validation():
    assert next_fire(clean({"time": "07:00", "enabled": False}), datetime.now()) is None
    with pytest.raises(ValueError):
        clean({"time": "7:00"})
    with pytest.raises(ValueError):
        clean({"time": "07:00", "days": [7]})


def test_store_roundtrip(tmp_path):
    s = Store(tmp_path / "a.json")
    a = s.add({"time": "06:30", "label": "x"})
    s.update(a["id"], {"enabled": False})
    assert Store(tmp_path / "a.json").get(a["id"])["enabled"] is False
    assert s.delete(a["id"]) and not s.alarms


def test_mpd_client_against_fake_server():
    from alarmplus.audio import MoodePlayer

    got = []

    async def handler(r, w):
        w.write(b"OK MPD 0.23.5\n")
        while line := await r.readline():
            got.append(line.decode().strip())
            w.write(b"OK\n")
            await w.drain()

    async def main():
        srv = await asyncio.start_server(handler, "127.0.0.1", 0)
        port = srv.sockets[0].getsockname()[1]
        p = MoodePlayer("127.0.0.1", port, "http://h/alarm.wav")
        await p.start(10)
        await p.set_volume(50)
        await p.stop()
        srv.close()

    asyncio.run(main())
    assert got == ["stop", "clear", 'add "http://h/alarm.wav"', "repeat 1", "single 0", "setvol 10", "play", "setvol 50", "stop"]
