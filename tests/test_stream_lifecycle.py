"""Camera views must not get stuck: a stalled camera, a closed tab or a second tab used to leave the smile test 'already running'."""
import asyncio
import logging

import numpy as np
from aiohttp.test_utils import TestClient, TestServer

from smilarm import config
from smilarm.app import CAM, ENGINE, build


class Det:
    def score(self, _):
        return None


def make_app(tmp_path, frames=True):
    cfg = config.load(None)
    cfg._d["data_dir"] = str(tmp_path)
    app = build(cfg, str(tmp_path / "config.toml"))

    async def preload():
        app[ENGINE].detector = Det()

    app[ENGINE].preload = preload
    refs = {"open": 0}
    cam = app[CAM]
    n = [0]

    def latest():
        if not frames:
            return None, 0.0  # a camera that stopped sending
        n[0] += 1
        return np.zeros((240, 320, 3), np.uint8), float(n[0]) * 1e9

    cam.acquire = lambda: refs.__setitem__("open", refs["open"] + 1)
    cam.release = lambda: refs.__setitem__("open", refs["open"] - 1)
    cam.latest = latest
    return app, refs


def run(app, fn):
    async def main():
        async with TestClient(TestServer(app)) as cl:
            return await fn(cl)

    return asyncio.run(main())


async def active(cl):
    return (await (await cl.get("/api/debug")).json())["test_active"]


async def wait_until(pred, timeout=5.0):
    end = asyncio.get_event_loop().time() + timeout
    while asyncio.get_event_loop().time() < end:
        if await pred():
            return True
        await asyncio.sleep(0.05)
    return False


def test_a_closed_tab_frees_the_smile_test_even_when_the_camera_is_silent(tmp_path):
    app, refs = make_app(tmp_path, frames=False)

    async def go(cl):
        r = await cl.get("/api/smile-test.mjpg")
        assert r.status == 200 and await active(cl)
        assert refs["open"] == 1  # the camera (and flashlight) are in use...
        r.close()  # ...and the viewer goes away while nothing is being written
        assert await wait_until(lambda: _not(active(cl)))
        assert refs["open"] == 0  # released, so the flashlight goes off too
        r2 = await cl.get("/api/smile-test.mjpg")
        assert r2.status == 200  # no "already running" lock-out
        r2.close()

    run(app, go)


async def _not(coro):
    return not await coro


def test_a_newer_request_takes_over_from_a_stale_one(tmp_path):
    app, refs = make_app(tmp_path, frames=False)

    async def go(cl):
        r1 = await cl.get("/api/smile-test.mjpg")
        assert r1.status == 200
        r2 = await cl.get("/api/smile-test.mjpg")  # e.g. the user re-opened the page while the first stream is stuck
        assert r2.status == 200
        assert refs["open"] == 1  # never two users of the camera at once
        r1.close()
        r2.close()
        assert await wait_until(lambda: _not(active(cl)))
        assert refs["open"] == 0

    run(app, go)


def test_an_alarm_still_wins_over_the_smile_test(tmp_path):
    app, refs = make_app(tmp_path)

    async def go(cl):
        app[ENGINE].ringing = True
        r = await cl.get("/api/smile-test.mjpg")
        assert r.status == 409 and "alarm is ringing" in await r.text()
        app[ENGINE].ringing = False

    run(app, go)


def test_plain_camera_view_also_releases_the_camera_when_closed(tmp_path):
    app, refs = make_app(tmp_path, frames=False)

    async def go(cl):
        r = await cl.get("/api/preview.mjpg")
        assert r.status == 200
        await asyncio.sleep(0.3)
        assert refs["open"] == 1
        r.close()
        end = asyncio.get_event_loop().time() + 5
        while refs["open"] and asyncio.get_event_loop().time() < end:
            await asyncio.sleep(0.05)
        assert refs["open"] == 0

    run(app, go)


def test_favicon_is_quiet_and_noise_loggers_are_muted(tmp_path, caplog):
    app, _ = make_app(tmp_path)

    async def go(cl):
        with caplog.at_level(logging.WARNING):
            r = await cl.get("/favicon.ico")
            assert r.status == 204
        assert not [x for x in caplog.records if "/favicon.ico" in x.getMessage()]  # no "GET /favicon.ico -> 404" warning
        assert "rel=\"icon\"" in await (await cl.get("/")).text()
        assert logging.getLogger("matplotlib").level >= logging.WARNING

    run(app, go)
