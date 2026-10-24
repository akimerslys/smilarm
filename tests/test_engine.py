import asyncio
from datetime import datetime, timedelta

from aiohttp import web
from aiohttp.test_utils import TestServer

from alarmplus import config
from alarmplus.engine import Engine
from alarmplus.store import Store


class FakeCam:
    def __init__(self, frames=True):
        self.frames, self.n, self.acq, self.rel = frames, 0, 0, 0

    def acquire(self):
        self.acq += 1

    def release(self):
        self.rel += 1

    def latest(self):  # a "new" frame on every call
        self.n += 1
        return (object() if self.frames else None), float(self.n)


class FakeDet:
    def __init__(self, score):
        self._s = score

    def score(self, _):
        return self._s


class FakeAudio:
    def __init__(self):
        self.started = self.stopped = 0

    async def start(self):
        self.started += 1

    async def stop(self):
        self.stopped += 1


def make(tmp_path, score=0.9, frames=True, **over):
    cfg = config.load(None)
    cfg._d["smile"].update(hold_seconds=0.2, fps=100)
    for sect, kv in over.items():
        cfg._d[sect].update(kv)
    store = Store(tmp_path / "a.json")
    eng = Engine(cfg, store, FakeAudio(), FakeCam(frames))
    eng.detector = FakeDet(score)
    return eng


ALARM = {"id": "t1", "time": "07:00", "label": "x"}


def test_smile_dismisses_and_cleans_up(tmp_path):
    eng = make(tmp_path)
    a = eng.store.add({"time": "07:00", "once": True})
    asyncio.run(asyncio.wait_for(eng.ring(a), 5))
    assert eng.state["state"] == "idle" and not eng.ringing
    assert (eng.audio.started, eng.audio.stopped) == (1, 1)
    assert eng.cam.acq == eng.cam.rel == 1
    assert eng.store.get(a["id"])["enabled"] is False  # `once` alarm disables itself


def test_recurring_alarm_stays_enabled(tmp_path):
    eng = make(tmp_path)
    a = eng.store.add({"time": "07:00"})
    asyncio.run(asyncio.wait_for(eng.ring(a), 5))
    assert eng.store.get(a["id"])["enabled"] is True


def test_no_face_keeps_ringing_and_manual_dismiss_is_gated(tmp_path):
    eng = make(tmp_path, score=None)

    async def main():
        t = asyncio.create_task(eng.ring(ALARM))
        await asyncio.sleep(0.5)
        assert eng.ringing and eng.state["score"] is None
        assert eng.dismiss() is False and eng.ringing  # disabled by default
        eng.cfg._d["ring"]["allow_manual_dismiss"] = True
        assert eng.dismiss() is True
        await asyncio.wait_for(t, 2)

    asyncio.run(main())
    assert not eng.ringing and eng.audio.stopped == 1


def test_weak_smile_never_passes(tmp_path):
    eng = make(tmp_path, score=0.3)

    async def main():
        t = asyncio.create_task(eng.ring(ALARM))
        await asyncio.sleep(0.6)  # > hold_seconds, still ringing
        assert eng.ringing and eng.state["progress"] == 0.0
        eng.cfg._d["ring"]["allow_manual_dismiss"] = True
        eng.dismiss()
        await asyncio.wait_for(t, 2)

    asyncio.run(main())


def test_dead_camera_unlocks_manual_dismiss(tmp_path):
    eng = make(tmp_path, frames=False, ring={"camera_fail_seconds": 0.2})

    async def main():
        t = asyncio.create_task(eng.ring(ALARM))
        await asyncio.sleep(0.5)
        assert eng.state["degraded"] is True
        assert eng.dismiss() is True
        await asyncio.wait_for(t, 2)

    asyncio.run(main())
    assert eng.state["degraded"] is False  # reset after the ring


def test_max_minutes_failsafe(tmp_path):
    eng = make(tmp_path, score=None, ring={"max_minutes": 0.01})  # 0.6 s
    asyncio.run(asyncio.wait_for(eng.ring(ALARM), 5))
    assert not eng.ringing and eng.audio.stopped == 1


def test_second_ring_is_ignored_while_ringing(tmp_path):
    eng = make(tmp_path, score=None)

    async def main():
        t = asyncio.create_task(eng.ring(ALARM))
        await asyncio.sleep(0.2)
        await eng.ring(ALARM)  # returns immediately
        assert eng.audio.started == 1
        eng.cfg._d["ring"]["allow_manual_dismiss"] = True
        eng.dismiss()
        await t

    asyncio.run(main())


def test_dismiss_when_idle_is_refused(tmp_path):
    assert make(tmp_path).dismiss() is False


def test_webhook_events(tmp_path):
    got = []

    async def hook(req):
        got.append(await req.json())
        return web.json_response({})

    async def main():
        app = web.Application()
        app.router.add_post("/h", hook)
        async with TestServer(app) as srv:
            eng = make(tmp_path, events={"webhook_url": str(srv.make_url("/h"))})
            await eng.ring(ALARM)
            await eng.close()

    asyncio.run(main())
    assert [e["event"] for e in got] == ["ringing", "dismissed"]
    assert got[1]["reason"] == "smile" and got[0]["source"] == "alarm-plus"


def test_broken_webhook_does_not_break_ring(tmp_path):
    eng = make(tmp_path, events={"webhook_url": "http://127.0.0.1:1/x"})

    async def main():
        await asyncio.wait_for(eng.ring(ALARM), 10)
        await eng.close()

    asyncio.run(main())
    assert not eng.ringing


def test_sse_subscribers_get_progress(tmp_path):
    eng = make(tmp_path)

    async def main():
        q = eng.subscribe()
        await eng.ring(ALARM)
        msgs = []
        while not q.empty():
            msgs.append(q.get_nowait())
        return msgs

    msgs = asyncio.run(main())
    assert msgs[0]["state"] == "ringing" and msgs[-1]["state"] == "idle"
    assert any(m.get("progress") == 1.0 for m in msgs)


def test_run_loop_fires_due_alarm(tmp_path):
    eng = make(tmp_path)
    fired = []

    async def fake_ring(a):
        fired.append(a["id"])

    eng.ring = fake_ring
    due = [(datetime.now() - timedelta(seconds=1), ALARM)]
    eng.store.next = lambda now: due.pop() if due else None

    async def main():
        t = asyncio.create_task(eng.run())
        await asyncio.sleep(0.2)
        t.cancel()

    asyncio.run(main())
    assert fired == ["t1"]


def test_run_loop_wakes_on_change(tmp_path):
    eng = make(tmp_path)
    calls = []
    eng.store.next = lambda now: calls.append(1)  # no alarms

    async def main():
        t = asyncio.create_task(eng.run())
        await asyncio.sleep(0.05)
        eng.changed()
        await asyncio.sleep(0.05)
        t.cancel()

    asyncio.run(main())
    assert len(calls) >= 2  # recomputed right after changed(), not after 60 s
