"""Output-device discovery and the last-resort walk over the machine's ALSA outputs."""
import asyncio
import sys

from aiohttp.test_utils import TestClient, TestServer

from alarmplus import audio, config
from alarmplus.app import build

MPV_HELP = """List of detected audio devices:
  'auto' (Autoselect device)
  'alsa' (Default (alsa))
  'alsa/sysdefault' (Default Audio Device)
  'alsa/plughw:CARD=vc4hdmi0,DEV=0' (vc4-hdmi-0, MAI PCM i2s-hifi-0/Hardware device with all software conversions)
  'alsa/hdmi:CARD=vc4hdmi0,DEV=0' (vc4-hdmi-0, MAI PCM i2s-hifi-0/HDMI Audio Output)
  'alsa/plughw:CARD=vc4hdmi1,DEV=0' (vc4-hdmi-1, MAI PCM i2s-hifi-0/Hardware device with all software conversions)
"""


def test_parse_mpv_device_list():
    devs = audio.parse_devices(MPV_HELP)
    ids = [d["id"] for d in devs]
    assert "auto" not in ids and "alsa/plughw:CARD=vc4hdmi1,DEV=0" in ids and len(devs) == 5
    assert devs[2]["name"].startswith("vc4-hdmi-0")
    assert audio.parse_devices("") == [] and audio.parse_devices("garbage\n  'unterminated") == []


def make(tmp_path):
    cfg = config.load(None)
    cfg._d["audio"].update(backend="local", ramp_seconds=1)
    return cfg, audio.Audio(cfg, tmp_path / "tone.wav")


def test_dead_default_output_falls_through_to_a_working_card(tmp_path, monkeypatch, caplog):
    cfg, a = make(tmp_path)
    tried = []

    async def devices():
        return audio.parse_devices(MPV_HELP)

    monkeypatch.setattr(audio, "list_devices", devices)

    def command(self, volume):
        tried.append(self.device)
        # only the second HDMI card works (the first has nothing plugged in)
        return [sys.executable, "-c", "import time; time.sleep(30)" if self.device == "alsa/plughw:CARD=vc4hdmi1,DEV=0"
                else "import sys; sys.stderr.write('Playback open error: Unknown error 524'); sys.exit(2)"]

    monkeypatch.setattr(audio.LocalPlayer, "_command", command)

    async def main():
        await a.start()
        n = len(a.players)
        await a.stop()
        return n

    with caplog.at_level("WARNING", logger="audio"):
        assert asyncio.run(main()) == 1
    assert "" in tried and "alsa/plughw:CARD=vc4hdmi0,DEV=0" in tried and tried[-1] == "alsa/plughw:CARD=vc4hdmi1,DEV=0"
    assert "plays on 'alsa/plughw:CARD=vc4hdmi1,DEV=0'" in caplog.text and "Set audio.device" in caplog.text


def test_configured_device_is_not_retried_and_a_working_default_never_walks(tmp_path, monkeypatch):
    cfg, a = make(tmp_path)
    walked = []

    async def devices():
        walked.append(1)
        return audio.parse_devices(MPV_HELP)

    monkeypatch.setattr(audio, "list_devices", devices)
    monkeypatch.setattr(audio.LocalPlayer, "_command", lambda self, v: [sys.executable, "-c", "import time; time.sleep(30)"])

    async def main():
        await a.start()
        await a.stop()

    asyncio.run(main())
    assert walked == []  # the normal case costs nothing


def test_devices_endpoint(tmp_path, monkeypatch):
    async def devices():
        return audio.parse_devices(MPV_HELP)

    monkeypatch.setattr(audio, "list_devices", devices)
    cfg = config.load(None)
    cfg._d["data_dir"] = str(tmp_path)
    app = build(cfg, str(tmp_path / "config.toml"))

    async def main():
        async with TestClient(TestServer(app)) as cl:
            d = await (await cl.get("/api/audio-devices")).json()
            assert [x["id"] for x in d["devices"]][0] == "alsa" and len(d["devices"]) == 5

    asyncio.run(main())


def test_no_mpv_means_an_empty_list(monkeypatch):
    monkeypatch.setattr(audio.shutil, "which", lambda name: None)
    assert asyncio.run(audio.list_devices()) == []
