"""The log/debug views must show events and causes, not the UI's own polling."""
import asyncio
import logging
import socket

from aiohttp.test_utils import TestClient, TestServer

from alarmplus import config
from alarmplus.app import build
from alarmplus.camera import Camera, diagnose_source


def lines_of(tmp_path, fn, level="INFO"):
    cfg = config.load(None)
    cfg._d["data_dir"] = str(tmp_path)
    cfg._d["logging"]["level"] = level
    app = build(cfg, str(tmp_path / "config.toml"))

    async def main():
        async with TestClient(TestServer(app)) as cl:
            await fn(cl)
            return (await (await cl.get("/api/logs?n=200")).json())["lines"]

    return asyncio.run(main())


def test_polling_is_not_logged_but_changes_and_failures_are(tmp_path):
    async def go(cl):
        for _ in range(5):
            await cl.get("/api/system")
            await cl.get("/api/debug")
            await cl.get("/api/state")
        await cl.post("/api/alarms", json={"time": "07:00"})
        await cl.post("/api/alarms", json={"time": "99:99"})

    out = "\n".join(lines_of(tmp_path, go))
    assert "GET /api/system" not in out and "/api/logs" not in out  # no feedback loop from the log panel itself
    assert "POST /api/alarms -> 201" in out
    assert "POST /api/alarms -> 400" in out and "time must be HH:MM" in out
    assert "Alarm+ started:" in out  # startup banner with the effective setup


def test_debug_level_adds_request_details_and_can_change_live(tmp_path):
    async def go(cl):
        await cl.get("/api/state")
        assert (await cl.put("/api/settings", json={"logging.level": "WARNING"})).status == 200
        assert logging.getLogger().level == logging.WARNING
        assert (await cl.put("/api/settings", json={"logging.level": "DEBUG"})).status == 200
        assert logging.getLogger().level == logging.DEBUG
        await cl.get("/api/state")

    out = "\n".join(lines_of(tmp_path, go, level="INFO"))
    assert "settings saved to config.toml: logging.level='WARNING'" in out
    assert "GET /api/state -> 200" in out  # visible only once DEBUG was on


def test_camera_diagnosis_gives_a_cause():
    s = socket.socket()
    s.bind(("127.0.0.1", 0))
    port = s.getsockname()[1]
    s.close()  # nothing listens here any more
    assert "refused" in diagnose_source(f"http://127.0.0.1:{port}/stream")
    assert "cannot resolve" in diagnose_source("http://no-such-host.invalid:81/stream")
    assert "IP address" in diagnose_source("http://no-such-host.invalid:81/stream")
    assert "local camera #3" in diagnose_source(3)
    assert "not a valid camera URL" in diagnose_source("garbage")
    srv = socket.socket()
    srv.bind(("127.0.0.1", 0))
    srv.listen(1)
    assert "ONE stream" in diagnose_source(f"http://127.0.0.1:{srv.getsockname()[1]}/stream")  # reachable but silent
    srv.close()


def test_camera_failure_is_reported_in_stats_and_logged_once(tmp_path, caplog):
    s = socket.socket()
    s.bind(("127.0.0.1", 0))
    port = s.getsockname()[1]
    s.close()
    cam = Camera(f"http://127.0.0.1:{port}/stream")
    assert cam.stats()["cam_state"] == "off"
    with caplog.at_level(logging.INFO, logger="camera"):
        cam.acquire()
        import time

        for _ in range(300):  # generous: the full suite loads the machine
            if cam.stats()["cam_state"] == "error":
                break
            time.sleep(0.1)
        st = cam.stats()
        cam.release()
    assert st["cam_state"] == "error" and "refused" in st["cam_error"]
    assert any("refused" in r.getMessage() and r.levelno == logging.WARNING for r in caplog.records)
    assert cam.stats()["cam_state"] == "off"
