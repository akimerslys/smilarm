"""Optional ESP32 flash LED: on while the camera is in use, always switched off again."""
import asyncio
import threading
import time
from http.server import BaseHTTPRequestHandler, HTTPServer

import pytest

from alarmplus import camera as cam_mod
from alarmplus import config, settings as st
from alarmplus.camera import Camera, flash_url


class FakeEsp:
    """Stands in for the ESP32's port-80 control server and records every request."""

    def __init__(self, status=200):
        self.requests, self.status = [], status
        outer = self

        class H(BaseHTTPRequestHandler):
            def do_GET(self):
                outer.requests.append(self.path)
                self.send_response(outer.status)
                self.end_headers()
                self.wfile.write(b"ok")

            def log_message(self, *a):
                pass

        self.srv = HTTPServer(("127.0.0.1", 0), H)
        self.port = self.srv.server_address[1]
        threading.Thread(target=self.srv.serve_forever, daemon=True).start()

    @property
    def leds(self):  # brightness values in the order they were sent
        return [int(p.split("led=")[1].split("&")[0]) for p in self.requests if "led=" in p]

    def close(self):
        self.srv.shutdown()
        self.srv.server_close()


def wait_for(cond, timeout=5.0):
    end = time.time() + timeout
    while time.time() < end:
        if cond():
            return True
        time.sleep(0.05)
    return False


@pytest.fixture
def esp():
    e = FakeEsp()
    yield e
    e.close()


def make_cam(esp, **kw):
    c = Camera("http://127.0.0.1:81/stream", **kw)  # the video stream itself is irrelevant here
    c.control_port = esp.port
    return c


def test_url_maps_percent_to_pwm_and_has_a_failsafe_ttl():
    assert flash_url("http://10.0.0.5:81/stream", 40) == "http://10.0.0.5/control?led=102&for=180"
    assert flash_url("http://10.0.0.5:81/stream", 100, port=8080) == "http://10.0.0.5:8080/control?led=255&for=180"
    assert flash_url(0, 40) is None and flash_url("garbage", 40) is None  # local cameras have no flash here


def test_flash_is_off_by_default(esp):
    c = make_cam(esp)
    c.acquire()
    assert wait_for(lambda: any("preset=" in p for p in esp.requests))  # the camera did talk to the ESP32...
    c.release()
    time.sleep(0.3)
    assert esp.leds == [] and c.stats()["cam_flash"] == "off"  # ...but never touched the LED


def test_flash_on_with_camera_and_off_when_released(esp):
    c = make_cam(esp, flash=True, flash_level=40)
    c.acquire()
    assert wait_for(lambda: esp.leds[:1] == [102])
    assert wait_for(lambda: c.stats()["cam_flash"] == "on")
    c.release()
    assert wait_for(lambda: esp.leds[-1:] == [0])  # switched off again
    assert c.stats()["cam_flash"] == "off"


def test_flash_is_refreshed_while_camera_stays_open(esp, monkeypatch):
    monkeypatch.setattr(cam_mod, "FLASH_REFRESH", 0.2)
    c = make_cam(esp, flash=True, flash_level=40)
    c.acquire()
    assert wait_for(lambda: esp.leds.count(102) >= 3)  # keep-alive so the ESP32's own timeout never fires mid-alarm
    c.release()
    assert wait_for(lambda: esp.leds[-1:] == [0])


def test_quick_reopen_does_not_leave_the_light_off(esp, monkeypatch):
    monkeypatch.setattr(cam_mod, "FLASH_REFRESH", 0.2)
    c = make_cam(esp, flash=True, flash_level=40)
    c.acquire()
    assert wait_for(lambda: esp.leds[:1] == [102])
    c.release()
    c.acquire()  # e.g. Smile test closed, alarm starts
    time.sleep(1.0)
    assert esp.leds[-1] == 102 and c.stats()["cam_flash"] == "on"  # the old loop's "off" must not win
    c.release()
    assert wait_for(lambda: esp.leds[-1:] == [0])


def test_toggle_in_settings_applies_while_camera_is_open(esp):
    c = make_cam(esp)
    c.acquire()
    time.sleep(0.3)
    assert esp.leds == []
    c.reconfigure("http://127.0.0.1:81/stream", "low", flash=True, flash_level=50)
    assert wait_for(lambda: esp.leds[-1:] == [128])
    c.reconfigure("http://127.0.0.1:81/stream", "low", flash=False)
    assert wait_for(lambda: esp.leds[-1:] == [0])
    c.release()


def test_firmware_without_flash_support_is_reported_not_fatal(caplog):
    e = FakeEsp(status=400)  # an old sketch answers 400 to the unknown `led` parameter
    try:
        c = make_cam(e, flash=True)
        with caplog.at_level("WARNING", logger="camera"):
            c.acquire()
            assert wait_for(lambda: c.stats()["cam_flash"] == "error")
            c.release()
        assert "reflash" in c.stats()["cam_flash_error"]
        assert sum("flashlight:" in r.getMessage() for r in caplog.records) == 1  # warned once, not per retry
    finally:
        e.close()


def test_unreachable_esp32_does_not_crash(caplog):
    e = FakeEsp()
    port = e.port
    e.close()  # nothing listens any more
    c = Camera("http://127.0.0.1:81/stream", flash=True)
    c.control_port = port
    c.acquire()
    assert wait_for(lambda: c.stats()["cam_flash"] == "error")
    c.release()


def test_local_camera_has_no_flash():
    c = Camera("0", flash=True)
    c.acquire()
    time.sleep(0.3)
    assert c.stats()["cam_flash"] == "off"
    c.release()


def test_settings_and_defaults():
    cfg = config.load(None)
    assert cfg.camera.flashlight is False and cfg.camera.flashlight_level == 30  # opt-in
    assert st.validate("camera.flashlight", True) is True
    assert st.validate("camera.flashlight_level", 100) == 100
    for k, bad in [("camera.flashlight", "yes"), ("camera.flashlight_level", 0), ("camera.flashlight_level", 101), ("camera.flashlight_level", 2.5)]:
        with pytest.raises(ValueError):
            st.validate(k, bad)


def test_web_settings_toggle_reaches_the_camera(tmp_path):
    from aiohttp.test_utils import TestClient, TestServer

    from alarmplus.app import CAM, build

    cfg = config.load(None)
    cfg._d["data_dir"] = str(tmp_path)
    app = build(cfg, str(tmp_path / "config.toml"))

    async def main():
        async with TestClient(TestServer(app)) as cl:
            r = await cl.put("/api/settings", json={"camera.flashlight": True, "camera.flashlight_level": 55})
            assert r.status == 200
            assert app[CAM].flash is True and app[CAM].flash_level == 55
            assert "flashlight = true" in (tmp_path / "config.toml").read_text()
            d = await (await cl.get("/api/debug")).json()
            assert d["cam_flash"] == "off" and d["cam_flash_error"] == ""  # enabled, but the camera isn't in use

    asyncio.run(main())
