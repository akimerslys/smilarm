"""Everything configurable from the UI: timezone, server/network, detector, restart, setup checklist."""
import asyncio
from datetime import datetime, timedelta

import pytest
from aiohttp.test_utils import TestClient, TestServer

from smilarm import audio, clock, config, settings as st
from smilarm.app import ENGINE, RESTART, build


def app_for(tmp_path, token="", **over):
    cfg = config.load(None)
    cfg._d["data_dir"] = str(tmp_path)
    cfg._d["server"]["token"] = token
    for dotted, v in over.items():
        sect, k = dotted.split("__")
        cfg._d[sect][k] = v
    return cfg, build(cfg, str(tmp_path / "config.toml"))


def run(app, fn):
    async def main():
        async with TestClient(TestServer(app)) as cl:
            return await fn(cl)

    return asyncio.run(main())


# ---------------- timezone ----------------
def test_clock_follows_the_configured_timezone():
    cfg = config.load(None)
    cfg._d["general"]["timezone"] = "Pacific/Kiritimati"  # UTC+14
    east = clock.now(cfg)
    cfg._d["general"]["timezone"] = "Pacific/Pago_Pago"  # UTC-11
    west = clock.now(cfg)
    assert timedelta(hours=24) < east - west < timedelta(hours=26)
    cfg._d["general"]["timezone"] = ""
    assert abs((clock.now(cfg) - datetime.now()).total_seconds()) < 5  # empty = the system clock
    cfg._d["general"]["timezone"] = "Not/AZone"
    assert clock.now(cfg)  # a bad value never breaks the scheduler


def test_alarm_next_fire_uses_the_configured_timezone(tmp_path):
    cfg, app = app_for(tmp_path, general__timezone="Pacific/Kiritimati")

    async def go(cl):
        await cl.post("/api/alarms", json={"time": "07:00"})
        nxt = (await (await cl.get("/api/state")).json())["next"]["at"]
        assert nxt.endswith("07:00")
        assert 0 < (datetime.fromisoformat(nxt) - clock.now(cfg)).total_seconds() <= 86400  # measured on that zone's clock

    run(app, go)


def test_timezone_setting_validation():
    assert st.validate("general.timezone", "Europe/London") == "Europe/London"
    assert st.validate("general.timezone", "") == ""
    with pytest.raises(ValueError):
        st.validate("general.timezone", "Europe/Atlantis")


# ---------------- server / network settings ----------------
def test_new_validations():
    assert st.validate("server.port", 8080) == 8080 and st.validate("server.host", "0.0.0.0") == "0.0.0.0"
    for k, bad in [("server.port", 0), ("server.port", 70000), ("server.host", "a b"), ("server.host", ""),
                   ("server.token", "has space"), ("server.token", "x" * 129), ("server.token", "café"),
                   ("server.public_url", "http://x/path"), ("server.public_url", "ftp://x"), ("smile.model", "/no/such/model.task"),
                   ("camera.width", -1), ("camera.width", 4000)]:
        with pytest.raises(ValueError):
            st.validate(k, bad)
    assert st.validate("server.public_url", "http://192.168.1.20:8080") == "http://192.168.1.20:8080"
    assert st.validate("server.token", "abc-123_XYZ!") == "abc-123_XYZ!"
    assert st.validate("smile.model", "data/face_landmarker.task")  # the default is always accepted (image/installer provides it)


def test_restart_needed_tracks_only_changed_start_time_settings(tmp_path):
    cfg = config.load(None)
    s = st.Settings(cfg, tmp_path / "config.toml")
    assert s.restart_needed is False
    s.update({"smile.fps": 9})
    assert s.restart_needed is False  # applied live
    s.update({"server.port": 9000})
    assert s.restart_needed is True
    s.update({"server.port": 8080})  # changed back to what the process runs with
    assert s.restart_needed is False
    assert [d["key"] for d in s.describe() if d.get("restart")] == ["server.host", "server.port"]


def test_token_can_be_set_from_the_ui_and_then_protects_the_api(tmp_path):
    cfg, app = app_for(tmp_path)

    async def go(cl):
        assert (await cl.get("/api/state")).status == 200
        assert (await cl.put("/api/settings", json={"hooks.on_dismiss": "echo hi"})).status == 403  # locked without a token
        assert (await cl.put("/api/settings", json={"server.token": "my-token"})).status == 200
        assert (await cl.get("/api/state")).status == 401  # protected immediately
        h = {"Authorization": "Bearer my-token"}
        assert (await cl.put("/api/settings", json={"hooks.on_dismiss": "echo hi"}, headers=h)).status == 200  # now unlocked
        assert 'token = "my-token"' in (tmp_path / "config.toml").read_text()

    run(app, go)


def test_settings_api_reports_restart_flag(tmp_path):
    cfg, app = app_for(tmp_path)

    async def go(cl):
        r = await (await cl.put("/api/settings", json={"server.port": 9123})).json()
        assert r["restart_needed"] is True
        spec = {s["key"]: s for s in r["settings"]}
        assert spec["server.port"]["restart"] is True and spec["server.token"]["secret"] is True
        assert (await (await cl.get("/api/setup")).json())["restart_needed"] is True

    run(app, go)


def test_restart_endpoint(tmp_path):
    cfg, app = app_for(tmp_path)
    called = []
    app[RESTART] = lambda: called.append(1)

    async def go(cl):
        assert (await cl.post("/api/restart")).status == 200
        await asyncio.sleep(0.8)
        assert called == [1]
        app[ENGINE].ringing = True
        assert (await cl.post("/api/restart")).status == 409  # never in the middle of an alarm
        app[ENGINE].ringing = False

    run(app, go)


# ---------------- detector reload ----------------
def test_changing_the_detector_backend_reloads_it(tmp_path):
    cfg, app = app_for(tmp_path)

    async def go(cl):
        eng = app[ENGINE]
        eng.detector = object()
        assert (await cl.put("/api/settings", json={"smile.fps": 7})).status == 200
        assert eng.detector is not None  # an unrelated change keeps it
        assert (await cl.put("/api/settings", json={"smile.backend": "haar"})).status == 200
        assert eng.detector is None  # rebuilt on next use with the new backend

    run(app, go)


# ---------------- public URL for moOde ----------------
def test_moode_url_uses_public_url_when_set(tmp_path):
    got = []

    async def handler(r, w):
        w.write(b"OK MPD 0.23.5\n")
        while line := await r.readline():
            got.append(line.decode().strip())
            w.write(b"OK\n")
            await w.drain()

    async def main():
        srv = await asyncio.start_server(handler, "127.0.0.1", 0)
        cfg = config.load(None)
        cfg._d["audio"].update(backend="moode", ramp_seconds=1)
        cfg._d["moode"].update(host="127.0.0.1", port=srv.sockets[0].getsockname()[1])
        cfg._d["server"]["public_url"] = "http://192.168.1.20:8080/"
        a = audio.Audio(cfg, tmp_path / "a.wav")
        await a.start()
        await a.stop()
        srv.close()

    asyncio.run(main())
    assert 'add "http://192.168.1.20:8080/sound/current"' in got  # not the container's own address


# ---------------- first-run checklist ----------------
def test_setup_checklist_points_at_what_is_missing(tmp_path, monkeypatch):
    monkeypatch.setenv("SMILARM_DOCKER", "1")
    cfg, app = app_for(tmp_path)

    async def go(cl):
        d = await (await cl.get("/api/setup")).json()
        by = {c["id"]: c for c in d["checks"]}
        assert by["timezone"]["level"] == "warn" and by["timezone"]["key"] == "general.timezone"
        assert by["camera"]["level"] == "todo" and by["camera"]["key"] == "camera.source"  # still the placeholder
        assert by["token"]["level"] == "info" and d["attention"] >= 2 and d["in_docker"] is True
        await cl.put("/api/settings", json={"general.timezone": "Europe/London", "camera.source": "http://10.0.0.5:81/stream"})
        by = {c["id"]: c for c in (await (await cl.get("/api/setup")).json())["checks"]}
        assert by["timezone"]["level"] == "ok" and by["camera"]["level"] == "ok"

    run(app, go)


def test_system_info_includes_container_and_clock(tmp_path):
    cfg, app = app_for(tmp_path, general__timezone="Europe/London")

    async def go(cl):
        d = await (await cl.get("/api/system")).json()
        assert d["timezone"] == "Europe/London" and d["device_time"][:2] == "20" and "in_docker" in d

    run(app, go)


def test_every_config_parameter_is_reachable_from_the_ui():
    flat = {f"{s}.{k}" for s, kv in config.DEFAULTS.items() if isinstance(kv, dict) for k in kv}
    assert flat - {s["key"] for s in st.SCHEMA} == set(), "a config.toml parameter has no Settings field"
    cfg = config.load(None)
    for spec in st.SCHEMA:
        sect, name = spec["key"].split(".")
        assert name in cfg._d[sect], spec["key"]  # and no Settings field without a parameter
    # the only parameter that is deliberately not in the UI: where the state lives (fixed by the container's volume)
    assert {k for k, v in config.DEFAULTS.items() if not isinstance(v, dict)} == {"data_dir"}
