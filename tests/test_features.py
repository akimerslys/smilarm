"""Settings, hooks, sound picking, system info, and the endpoints that expose them."""
import asyncio
import json
import sys

import pytest
from aiohttp.test_utils import TestClient, TestServer

from smilarm import audio, config, settings as st, sysinfo
from smilarm.app import AUDIO, ENGINE, build
from smilarm.engine import Engine
from smilarm.store import Store
from test_engine import ALARM, FakeAudio, FakeCam, FakeDet


# ---------- settings ----------
def mk(tmp_path, token=""):
    cfg = config.load(None)
    cfg._d["server"]["token"] = token
    return cfg, st.Settings(cfg, tmp_path / "config.toml")


def test_validation_ranges_and_types(tmp_path):
    cfg, s = mk(tmp_path)
    s.update({"smile.threshold": 0.7, "smile.fps": 8, "camera.quality": "high", "ring.allow_manual_dismiss": True})
    assert cfg.smile.threshold == 0.7 and cfg.smile.fps == 8 and cfg.camera.quality == "high"
    bad = {"smile.threshold": 5, "smile.fps": 2.5, "camera.quality": "ultra", "ring.allow_manual_dismiss": "yes",
           "smile.hold_seconds": True, "nope.key": 1, "camera.source": "file:///etc/passwd", "moode.host": "a b"}
    with pytest.raises(ValueError) as e:
        s.update(bad)
    assert set(e.value.args[0]) == set(bad)
    assert cfg.smile.threshold == 0.7  # nothing from the failed batch was applied


def test_update_is_atomic_and_cross_checked(tmp_path):
    cfg, s = mk(tmp_path)
    with pytest.raises(ValueError):
        s.update({"smile.fps": 5, "audio.volume_start": 90, "audio.volume_max": 50})
    assert cfg.smile.fps == 6 and cfg.audio.volume_start == 15


def test_sensitive_settings_need_token(tmp_path):
    cfg, s = mk(tmp_path)  # no token
    assert all(d["locked"] for d in s.describe() if d["key"].startswith("hooks."))
    with pytest.raises(PermissionError):
        s.update({"hooks.on_dismiss": "~/x.sh", "smile.fps": 3})
    assert cfg.hooks.on_dismiss == "" and cfg.smile.fps == 6  # not even the harmless one
    cfg, s = mk(tmp_path, token="t")
    s.update({"hooks.on_dismiss": "~/x.sh", "events.webhook_url": "http://h/x"})
    assert cfg.hooks.on_dismiss == "~/x.sh"
    with pytest.raises(ValueError):
        s.update({"events.webhook_url": "ftp://x"})


def test_path_settings_are_checked_on_disk(tmp_path):
    cfg, s = mk(tmp_path)
    (tmp_path / "a.mp3").write_bytes(b"x")
    (tmp_path / "a.txt").write_bytes(b"x")
    s.update({"audio.sound": str(tmp_path / "a.mp3"), "audio.sound_dir": str(tmp_path), "audio.device": "alsa/plughw:1,0"})
    for k, v in [("audio.sound", str(tmp_path / "a.txt")), ("audio.sound", str(tmp_path / "missing.mp3")),
                 ("audio.sound_dir", str(tmp_path / "nodir")), ("audio.device", "x; rm -rf /")]:
        with pytest.raises(ValueError):
            s.update({k: v})
    s.update({"audio.sound": "https://example.org/a.mp3"})


def test_schema_covers_only_real_config_keys():
    cfg = config.load(None)
    for spec in st.SCHEMA:
        sect, name = spec["key"].split(".")
        assert name in cfg._d[sect], spec["key"]
        assert st.validate(spec["key"], cfg._d[sect][name]) == cfg._d[sect][name]  # defaults are valid


# ---------- sound picking ----------
def test_pick_sound_priority(tmp_path):
    cfg = config.load(None)
    a = audio.Audio(cfg, tmp_path / "b.wav")
    assert a._pick_sound() == str(tmp_path / "b.wav")
    cfg._d["audio"]["sound"] = "https://x/a.mp3"
    assert a._pick_sound() == "https://x/a.mp3"
    d = tmp_path / "snd"
    d.mkdir()
    (d / "one.ogg").write_bytes(b"x")
    (d / "note.txt").write_bytes(b"x")
    cfg._d["audio"]["sound_dir"] = str(d)
    assert a._pick_sound() == str(d / "one.ogg")  # dir beats sound; non-audio files skipped
    (d / "one.ogg").unlink()
    assert a._pick_sound() == "https://x/a.mp3"  # empty dir falls back


# ---------- hooks ----------
def hook_engine(tmp_path, **hooks):
    cfg = config.load(None)
    cfg._d["smile"].update(hold_seconds=0.2, fps=100)
    cfg._d["hooks"].update(hooks)
    eng = Engine(cfg, Store(tmp_path / "a.json"), FakeAudio(), FakeCam())
    eng.detector = FakeDet(0.9)
    return eng


def script(tmp_path, body):
    p = tmp_path / "hook.py"
    p.write_text(body)
    return f'"{sys.executable}" "{p}"'


def test_on_dismiss_hook_gets_env(tmp_path):
    out = tmp_path / "out.txt"
    cmd = script(tmp_path, "import os;open(%r,'w').write('|'.join(os.environ[k] for k in "
                           "('ALARM_EVENT','ALARM_ID','ALARM_LABEL','ALARM_REASON')))" % str(out))
    eng = hook_engine(tmp_path, on_dismiss=cmd)

    async def main():
        await eng.ring({"id": "a1", "time": "07:00", "label": "gym"})
        await eng.wait_background()

    asyncio.run(main())
    assert out.read_text() == "dismiss|a1|gym|smile"
    assert eng.hook_log[-1]["code"] == 0 and eng.last["reason"] == "smile"


def test_on_ring_hook_runs_and_failures_are_contained(tmp_path):
    cmd = script(tmp_path, "import sys;print('boom');sys.exit(3)")
    eng = hook_engine(tmp_path, on_ring=cmd)

    async def main():
        await eng.ring(ALARM)
        await eng.wait_background()

    asyncio.run(main())
    rec = eng.hook_log[-1]
    assert rec["hook"] == "on_ring" and rec["code"] == 3 and "boom" in rec["output"]
    assert not eng.ringing  # a failing hook never affects the alarm


def test_hook_timeout_kills_command(tmp_path):
    eng = hook_engine(tmp_path, on_dismiss=script(tmp_path, "import time;time.sleep(30)"), timeout_seconds=1)

    async def main():
        await eng.ring(ALARM)
        await asyncio.wait_for(eng.wait_background(), 10)

    asyncio.run(main())
    assert eng.hook_log[-1]["code"] is None and "killed" in eng.hook_log[-1]["output"]


def test_no_hook_configured_is_noop(tmp_path):
    eng = hook_engine(tmp_path)
    asyncio.run(eng.ring(ALARM))
    assert eng.hook_log == []


# ---------- sysinfo ----------
def test_parsers():
    assert sysinfo.parse_cpu_stat("cpu  100 0 50 800 50 0 0 0 0 0\ncpu0 1") == (150, 1000)
    assert sysinfo.parse_cpu_stat("garbage") is None
    m = sysinfo.parse_meminfo("MemTotal: 1000000 kB\nMemFree: 1 kB\nMemAvailable: 250000 kB\n")
    assert m == {"total_mb": 977, "used_mb": 732, "percent": 75.0}
    assert sysinfo.parse_meminfo("nothing") is None
    assert sysinfo.decode_throttled(0) == []
    assert sysinfo.decode_throttled(0x50005) == ["under-voltage now", "throttled now", "under-voltage since boot", "throttled since boot"]


def test_cpu_percent_between_samples(tmp_path, monkeypatch):
    s = sysinfo.Sampler(str(tmp_path))
    samples = iter(["cpu 0 0 0 100 0 0 0 0", "cpu 50 0 0 150 0 0 0 0"])
    monkeypatch.setattr(sysinfo, "_read", lambda p: next(samples))
    assert s.cpu_percent() is None  # needs two samples
    assert s.cpu_percent() == 50.0


def test_log_buffer_keeps_last_lines():
    import logging

    b = sysinfo.LogBuffer(n=3)
    lg = logging.getLogger("t-logbuf")
    lg.addHandler(b)
    lg.setLevel(logging.INFO)
    for i in range(5):
        lg.info("line %d", i)
    assert [x.split(": ")[-1] for x in b.lines] == ["line 2", "line 3", "line 4"]


# ---------- endpoints ----------
def api(fn, tmp_path, token="", patch=None):
    cfg = config.load(None)
    cfg._d["data_dir"] = str(tmp_path)
    cfg._d["server"]["token"] = token
    app = build(cfg, str(tmp_path / "config.toml"))
    if patch:
        patch(app)

    async def main():
        async with TestClient(TestServer(app)) as cl:
            return await fn(cl, app)

    return asyncio.run(main())


def test_settings_endpoints(tmp_path):
    async def go(cl, app):
        d = await (await cl.get("/api/settings")).json()
        assert d["locked"] is True and any(s["key"] == "smile.threshold" for s in d["settings"])
        r = await cl.put("/api/settings", json={"smile.threshold": 0.8, "camera.quality": "medium"})
        assert r.status == 200
        cam = app["cam"] if "cam" in app else None
        r = await cl.put("/api/settings", json={"smile.threshold": 7})
        assert r.status == 400 and "smile.threshold" in json.loads(await r.text())
        assert (await cl.put("/api/settings", json={"hooks.on_dismiss": "x"})).status == 403
        assert (await cl.put("/api/settings", data="nope")).status == 400
        r = await cl.delete("/api/settings")
        vals = {s["key"]: s["value"] for s in (await r.json())["settings"]}
        assert vals["smile.threshold"] == 0.5 and vals["camera.quality"] == "low"
        assert "threshold = 0.5" in (tmp_path / "config.toml").read_text()

    api(go, tmp_path)


def test_camera_quality_change_applies_to_camera(tmp_path):
    from smilarm.app import CAM

    async def go(cl, app):
        await cl.put("/api/settings", json={"camera.quality": "high"})
        assert app[CAM].quality == "high" and app[CAM].width == 640

    api(go, tmp_path)


def test_settings_require_token_when_set(tmp_path):
    async def go(cl, app):
        assert (await cl.get("/api/settings")).status == 401
        h = {"Authorization": "Bearer t"}
        assert (await cl.put("/api/settings", json={"hooks.on_dismiss": "echo hi"}, headers=h)).status == 200
        assert app[ENGINE].cfg.hooks.on_dismiss == "echo hi"

    api(go, tmp_path, token="t")


def test_system_and_logs_endpoints(tmp_path):
    async def go(cl, app):
        d = await (await cl.get("/api/system")).json()
        for k in ("cpu_percent", "mem", "cpu_temp_c", "load", "service_uptime_s", "hostname", "python", "network", "versions", "hooks", "audio"):
            assert k in d
        assert d["python"].startswith("3.") and "aiohttp" in d["versions"]
        import logging

        logging.getLogger("probe-test").info("hello-from-test")
        lines = (await (await cl.get("/api/logs?n=50")).json())["lines"]
        assert any("hello-from-test" in x for x in lines)

    api(go, tmp_path)


def test_sound_test_endpoint_and_current_sound(tmp_path):
    calls = []

    def patch(app):
        a = app[AUDIO]

        async def start():
            calls.append("start")
            a.current = str(tmp_path / "x.wav")

        async def stop():
            calls.append("stop")

        a.start, a.stop = start, stop
        a.builtin = tmp_path / "x.wav"
        (tmp_path / "x.wav").write_bytes(b"RIFFdata")

    async def go(cl, app):
        assert (await cl.post("/api/sound-test")).status == 200
        await asyncio.sleep(0.1)
        assert (await cl.post("/api/sound-test")).status == 409  # one at a time
        app[AUDIO].current = str(tmp_path / "x.wav")
        r = await cl.get("/sound/current")
        assert r.status == 200 and (await r.read()) == b"RIFFdata"
        app[AUDIO].current = str(tmp_path / "gone.wav")
        assert (await cl.get("/sound/current")).status == 404

    api(go, tmp_path, patch=patch)
    assert calls[0] == "start"


# ---------- manual stop from the web UI ----------
def test_test_ring_can_always_be_stopped(tmp_path):
    from test_engine import make

    eng = make(tmp_path, score=None)  # nobody smiling, manual dismiss disabled by default

    async def main():
        t = asyncio.create_task(eng.ring({"id": "test", "time": "now", "label": "test"}))
        await asyncio.sleep(0.2)
        assert eng.state["can_stop"] is True and eng.dismiss() is True
        await asyncio.wait_for(t, 2)

    asyncio.run(main())
    assert eng.last["reason"] == "manual" and eng.state["can_stop"] is False


def test_real_alarm_stop_follows_setting_live(tmp_path):
    from test_engine import make

    eng = make(tmp_path, score=None)

    async def main():
        t = asyncio.create_task(eng.ring(ALARM))
        await asyncio.sleep(0.2)
        assert eng.state["can_stop"] is False and eng.dismiss() is False
        eng.cfg._d["ring"]["allow_manual_dismiss"] = True  # e.g. toggled in the web UI Settings
        await asyncio.sleep(0.2)
        assert eng.state["can_stop"] is True
        assert eng.dismiss() is True
        await asyncio.wait_for(t, 2)

    asyncio.run(main())


def test_dismiss_endpoint_message_when_disabled(tmp_path):
    async def go(cl, app):
        r = await cl.post("/api/dismiss")
        assert r.status == 403 and "allow_manual_dismiss" in await r.text()

    api(go, tmp_path)


def test_web_save_writes_config_file_and_reload_endpoint(tmp_path):
    async def go(cl, app):
        assert (await cl.put("/api/settings", json={"smile.threshold": 0.72})).status == 200
        f = tmp_path / "config.toml"
        assert "threshold = 0.72" in f.read_text()
        f.write_text(f.read_text().replace("0.72", "0.61"))  # hand edit on the device
        r = await (await cl.post("/api/settings/reload")).json()
        assert r["problems"] == {} and app[ENGINE].cfg.smile.threshold == 0.61

    api(go, tmp_path)


def test_web_save_writes_config_file_and_reload_endpoint(tmp_path):
    async def go(cl, app):
        assert (await cl.put("/api/settings", json={"smile.threshold": 0.72})).status == 200
        f = tmp_path / "config.toml"
        assert "threshold = 0.72" in f.read_text()
        f.write_text(f.read_text().replace("0.72", "0.61"))  # hand edit on the device
        r = await (await cl.post("/api/settings/reload")).json()
        assert r["problems"] == {} and app[ENGINE].cfg.smile.threshold == 0.61

    api(go, tmp_path)
