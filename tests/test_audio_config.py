import asyncio
import wave

import pytest

from alarmplus import audio, config
from alarmplus.smile import HaarDetector, make_detector


# ---- config ----
def test_config_defaults_and_deep_merge(tmp_path):
    p = tmp_path / "c.toml"
    p.write_text('[server]\nport = 9000\n[smile]\nthreshold = 0.7\n')
    c = config.load(str(p))
    assert c.server.port == 9000 and c.server.host == "0.0.0.0"  # sibling default kept
    assert c.smile.threshold == 0.7 and c.smile.hold_seconds == 2.5
    assert config.load(str(tmp_path / "missing.toml")).server.port == 8080
    assert config.load(None).ring.allow_manual_dismiss is False


# ---- audio ----
def test_builtin_tone_is_valid_wav(tmp_path):
    p = tmp_path / "sub" / "a.wav"
    audio.make_builtin_tone(p)
    with wave.open(str(p)) as w:
        assert w.getnchannels() == 1 and w.getframerate() == 22050
        assert 1.0 < w.getnframes() / w.getframerate() < 2.0
    mtime = p.stat().st_mtime_ns
    audio.make_builtin_tone(p)  # idempotent
    assert p.stat().st_mtime_ns == mtime


class _Local:
    started = 0

    async def start(self, v):
        _Local.started += 1

    async def stop(self):
        pass


def _audio(tmp_path, backend, moode_port=1, fallback=True):
    cfg = config.load(None)
    cfg._d["audio"].update(backend=backend, ramp_seconds=1)
    cfg._d["moode"].update(host="127.0.0.1", port=moode_port, fallback_local=fallback)
    return audio.Audio(cfg, tmp_path / "a.wav")


@pytest.fixture
def fake_local(monkeypatch):
    _Local.started = 0
    monkeypatch.setattr(audio, "LocalPlayer", lambda *_: _Local())


def test_moode_down_falls_back_to_local(tmp_path, fake_local):
    async def main():
        a = _audio(tmp_path, "moode")  # port 1: connection refused
        await a.start()
        n = [type(p).__name__ for p in a.players]
        await a.stop()
        return n

    assert asyncio.run(main()) == ["_Local"]


def test_moode_down_without_fallback_plays_nothing(tmp_path, fake_local):
    async def main():
        a = _audio(tmp_path, "moode", fallback=False)
        await a.start()
        n = len(a.players)
        await a.stop()
        return n

    assert asyncio.run(main()) == 0


def test_both_backends_use_builtin_url_and_ramp(tmp_path, fake_local):
    got = []

    async def handler(r, w):
        w.write(b"OK MPD 0.23.5\n")
        while line := await r.readline():
            got.append(line.decode().strip())
            w.write(b"OK\n")
            await w.drain()

    async def main():
        srv = await asyncio.start_server(handler, "127.0.0.1", 0)
        a = _audio(tmp_path, "both", srv.sockets[0].getsockname()[1])
        await a.start()
        names = sorted(type(p).__name__ for p in a.players)
        await asyncio.sleep(1.3)  # ramp finishes (1 s)
        await a.stop()
        srv.close()
        return names

    assert asyncio.run(main()) == ["MoodePlayer", "_Local"]
    adds = [c for c in got if c.startswith("add ")]
    assert adds and adds[0].endswith('/sound/current"')
    assert "setvol 100" in got and got[-1] == "stop"  # reached volume_max, then stopped


def test_mpd_ack_error_is_reported():
    async def handler(r, w):
        w.write(b"OK MPD 0.23.5\n")
        await r.readline()
        w.write(b"ACK [50@0] {add} No such song\n")
        await w.drain()

    async def main():
        srv = await asyncio.start_server(handler, "127.0.0.1", 0)
        p = audio.MoodePlayer("127.0.0.1", srv.sockets[0].getsockname()[1], "x")
        try:
            await p.start(10)
        finally:
            srv.close()

    with pytest.raises(RuntimeError, match="ACK"):
        asyncio.run(main())


# ---- detector selection ----
def test_detector_selection(tmp_path):
    missing = str(tmp_path / "nope.task")
    assert isinstance(make_detector("auto", missing), HaarDetector)
    assert isinstance(make_detector("haar", missing), HaarDetector)
    with pytest.raises(FileNotFoundError):
        make_detector("mediapipe", missing)


def test_haar_returns_none_without_face():
    import numpy as np

    assert HaarDetector().score(np.zeros((240, 320, 3), np.uint8)) is None


# ---- camera presets ----
def test_camera_presets():
    from alarmplus.camera import PRESETS, Camera, control_url

    assert PRESETS == {"low": 320, "medium": 480, "high": 640}
    assert Camera("0").width == 320 and Camera("0", "high").width == 640
    assert Camera("0", "high", 500).width == 500  # explicit width wins
    with pytest.raises(ValueError):
        Camera("0", "ultra")
    assert control_url("http://10.9.141.122:81/stream", "medium") == "http://10.9.141.122/control?preset=medium"
    assert control_url("0", "low") is None


# ---- moOde library folder (like `mpc listall DIR | shuf -n 1`) ----
def _fake_mpd(files, got):
    async def handler(r, w):
        w.write(b"OK MPD 0.23.5\n")
        while line := await r.readline():
            cmd = line.decode().strip()
            got.append(cmd)
            if cmd.startswith("listall"):
                w.write("".join(f"{x}\n" for x in files).encode())
            w.write(b"OK\n")
            await w.drain()

    return handler


def test_library_dir_picks_random_sound_from_moode_library():
    got = []
    files = ["directory: ALARM_TRACKS/sub", "file: ALARM_TRACKS/a.mp3", "file: ALARM_TRACKS/notes.txt", 'file: ALARM_TRACKS/we"ird.flac']

    async def main():
        srv = await asyncio.start_server(_fake_mpd(files, got), "127.0.0.1", 0)
        p = audio.MoodePlayer("127.0.0.1", srv.sockets[0].getsockname()[1], "", "ALARM_TRACKS")
        await p.start(40)
        srv.close()
        return p.picked

    picked = asyncio.run(main())
    assert picked in ("ALARM_TRACKS/a.mp3", 'ALARM_TRACKS/we"ird.flac')  # txt and directories skipped
    assert got[0] == 'stop' or got[0].startswith("listall")
    assert 'listall "ALARM_TRACKS"' in got
    assert any(c.startswith("add ") and "\\\"" in c for c in got) or any(c == 'add "ALARM_TRACKS/a.mp3"' for c in got)
    assert got[-1] == "play"


def test_library_dir_empty_raises_helpful_error():
    async def main():
        srv = await asyncio.start_server(_fake_mpd([], []), "127.0.0.1", 0)
        p = audio.MoodePlayer("127.0.0.1", srv.sockets[0].getsockname()[1], "", "ALARM_TRACKS")
        try:
            await p.start(40)
        finally:
            srv.close()

    with pytest.raises(RuntimeError, match="mpc update ALARM_TRACKS"):
        asyncio.run(main())


def test_library_dir_setting_validation(tmp_path):
    from alarmplus import settings as st

    assert st.validate("moode.library_dir", "ALARM_TRACKS") == "ALARM_TRACKS"
    assert st.validate("moode.library_dir", "") == ""
    for bad in ("/var/lib/mpd/music", "../etc"):
        with pytest.raises(ValueError):
            st.validate("moode.library_dir", bad)
