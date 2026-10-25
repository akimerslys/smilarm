"""Failure modes that must never silence an alarm or kill the scheduler."""
import asyncio
import time

from aiohttp import web
from aiohttp.test_utils import TestClient, TestServer

from smilarm import config
from smilarm.app import build
from smilarm.engine import Engine
from smilarm.store import Store
from test_engine import ALARM, FakeAudio, FakeCam, FakeDet, make


def test_scheduler_survives_a_failing_alarm(tmp_path):
    eng = make(tmp_path)
    calls = []

    async def boom(alarm):
        calls.append(alarm["id"])
        raise RuntimeError("audio exploded")

    eng.ring = boom
    from datetime import datetime, timedelta

    due = [(datetime.now() - timedelta(seconds=1), ALARM), (datetime.now() - timedelta(seconds=1), {**ALARM, "id": "t2"})]
    eng.store.next = lambda now: due.pop() if due else None

    async def main():
        t = asyncio.create_task(eng.run())
        await asyncio.sleep(2.5)
        assert not t.done()  # still alive after the first failure
        t.cancel()

    asyncio.run(main())
    assert calls == ["t2", "t1"]  # the second alarm was still serviced


def test_state_returns_to_idle_when_audio_fails(tmp_path):
    eng = make(tmp_path)

    async def bad_start():
        raise OSError("no sound card")

    eng.audio.start = bad_start

    async def main():
        try:
            await eng.ring(ALARM)
        except OSError:
            pass

    asyncio.run(main())
    assert not eng.ringing and eng.state["state"] == "idle"  # the UI is not stuck on "ringing"


class BrokenDetector:
    def score(self, _):
        raise RuntimeError("bad frame")


def test_detector_exception_does_not_end_the_alarm(tmp_path):
    eng = make(tmp_path)
    eng.detector = BrokenDetector()

    async def main():
        t = asyncio.create_task(eng.ring(ALARM))
        await asyncio.sleep(0.5)
        assert eng.ringing  # still ringing, audio untouched
        assert eng.audio.stopped == 0
        assert eng.state["degraded"] is True  # after repeated failures manual stop is unlocked
        assert eng.dismiss() is True
        await asyncio.wait_for(t, 2)

    asyncio.run(main())


def test_detector_that_cannot_load_still_rings_and_can_be_stopped(tmp_path):
    eng = make(tmp_path)
    eng.detector = None

    async def no_model():
        raise FileNotFoundError("face_landmarker.task")

    eng.preload = no_model

    async def main():
        t = asyncio.create_task(eng.ring(ALARM))
        await asyncio.sleep(0.4)
        assert eng.audio.started == 1 and eng.ringing  # the sound is on despite the broken detector
        assert eng.state["degraded"] is True and eng.dismiss() is True
        await asyncio.wait_for(t, 2)

    asyncio.run(main())
    assert eng.last["reason"] == "manual"


def test_slow_webhook_does_not_delay_the_sound(tmp_path):
    async def hang(req):
        await asyncio.sleep(30)
        return web.json_response({})

    async def main():
        app = web.Application()
        app.router.add_post("/h", hang)
        async with TestServer(app) as srv:
            eng = make(tmp_path, events={"webhook_url": str(srv.make_url("/h"))}, ring={"max_minutes": 0.01})
            started = []
            real = eng.audio.start

            async def start():
                started.append(time.monotonic())
                await real()

            eng.audio.start = start
            t0 = time.monotonic()
            await eng.ring(ALARM)
            assert started[0] - t0 < 0.5  # not held up by the 5 s webhook timeout
            await eng.close()

    asyncio.run(main())


def test_two_simultaneous_ring_requests_ring_once(tmp_path):
    eng = make(tmp_path, score=None)

    async def main():
        a = asyncio.create_task(eng.ring(ALARM))
        b = asyncio.create_task(eng.ring(ALARM))  # e.g. a double click on "Test ring"
        await asyncio.sleep(0.3)
        assert eng.audio.started == 1
        eng.cfg._d["ring"]["allow_manual_dismiss"] = True
        eng.dismiss()
        await asyncio.gather(a, b)

    asyncio.run(main())


def test_ring_stops_a_leftover_sound_test_first(tmp_path):
    eng = make(tmp_path)
    eng.audio.players = ["leftover"]  # a sound test is still "playing"
    asyncio.run(eng.ring(ALARM))
    assert eng.audio.stopped == 2  # once to clear the leftover, once at the end


# ---- API input handling ----
def api(fn, tmp_path, token=""):
    cfg = config.load(None)
    cfg._d["data_dir"] = str(tmp_path)
    cfg._d["server"]["token"] = token
    app = build(cfg, str(tmp_path / "config.toml"))

    async def main():
        async with TestClient(TestServer(app)) as cl:
            return await fn(cl)

    return asyncio.run(main())


def test_non_object_json_and_odd_values_are_400_not_500(tmp_path):
    async def go(cl):
        for body in ([1, 2], "text", 5, None):
            assert (await cl.post("/api/alarms", json=body)).status == 400
            assert (await cl.put("/api/settings", json=body)).status == 400
        assert (await cl.post("/api/alarms", json={"time": "07:00", "days": [True]})).status == 400  # bool is not a weekday
        assert (await cl.post("/api/alarms", json={"time": 700})).status == 400
        assert (await cl.put("/api/settings", json={"smile.fps": "6"})).status == 400

    api(go, tmp_path)


def test_non_ascii_token_is_401_not_500(tmp_path):
    async def go(cl):
        assert (await cl.get("/api/state", headers={"Authorization": "Bearer café"})).status == 401
        assert (await cl.get("/api/state?token=%C3%A9")).status == 401
        assert (await cl.get("/api/state?token=s3cret")).status == 200

    api(go, tmp_path, token="s3cret")


def test_smile_test_reports_a_broken_detector(tmp_path):
    from smilarm.app import ENGINE

    cfg = config.load(None)
    cfg._d["data_dir"] = str(tmp_path)
    app = build(cfg, str(tmp_path / "config.toml"))

    async def no_model():
        raise FileNotFoundError("face_landmarker.task")

    app[ENGINE].preload = no_model

    async def main():
        async with TestClient(TestServer(app)) as cl:
            r = await cl.get("/api/smile-test.mjpg")
            assert r.status == 503 and "face_landmarker.task" in await r.text()
            assert (await (await cl.get("/api/debug")).json())["test_active"] is False  # not left "active"

    asyncio.run(main())


def test_relative_paths_in_config_follow_the_config_file_not_the_cwd(tmp_path):
    import os
    import socket
    import subprocess
    import sys
    import urllib.request
    from pathlib import Path

    root = Path(__file__).resolve().parent.parent
    proj, elsewhere = tmp_path / "proj", tmp_path / "elsewhere"
    proj.mkdir()
    elsewhere.mkdir()
    s = socket.socket()
    s.bind(("127.0.0.1", 0))
    port = s.getsockname()[1]
    s.close()
    (proj / "config.toml").write_text(f'data_dir = "data"\n[server]\nhost = "127.0.0.1"\nport = {port}\n')
    p = subprocess.Popen([sys.executable, "-m", "smilarm", "--config", str(proj / "config.toml")], cwd=elsewhere,
                         env={**os.environ, "PYTHONPATH": str(root)}, stdout=subprocess.DEVNULL, stderr=subprocess.DEVNULL)
    try:
        for _ in range(100):
            try:
                urllib.request.urlopen(f"http://127.0.0.1:{port}/api/state", timeout=1).read()
                break
            except OSError:
                time.sleep(0.2)
        else:
            raise AssertionError("server did not start")
    finally:
        p.kill()
        p.wait()
    assert (proj / "data" / "alarm.wav").exists() and not (elsewhere / "data").exists()
