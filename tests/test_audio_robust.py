"""A bad sound file, a wrong device or a dead player must never mean a silent alarm."""
import asyncio
import os
import sys
import time

import pytest

from alarmplus import audio, config


def make_audio(tmp_path, **kw):
    cfg = config.load(None)
    cfg._d["audio"].update(backend="local", ramp_seconds=1, **kw)
    return cfg, audio.Audio(cfg, tmp_path / "tone.wav")


def fake_player(monkeypatch, behave):
    """Replace the real player command: behave(sound, device) -> python code the 'player' runs."""
    def command(self, volume):
        return [sys.executable, "-c", behave(self.sound, self.device)]

    monkeypatch.setattr(audio.LocalPlayer, "_command", command)


SLEEP = "import time; time.sleep(30)"


def test_missing_sound_file_falls_back_to_builtin_tone(tmp_path, monkeypatch):
    cfg, a = make_audio(tmp_path, sound=str(tmp_path / "deleted.mp3"))
    assert a._pick_sound() == str(tmp_path / "tone.wav")


def test_unreadable_sound_dir_falls_back(tmp_path):
    cfg, a = make_audio(tmp_path, sound_dir=str(tmp_path / "gone"))
    assert a._pick_sound() == str(tmp_path / "tone.wav")


def test_player_that_dies_at_once_triggers_fallback_chain(tmp_path, monkeypatch):
    good = tmp_path / "good.mp3"
    good.write_bytes(b"x")
    cfg, a = make_audio(tmp_path, sound=str(good), device="alsa/broken")
    tried = []

    def behave(sound, device):
        tried.append((os.path.basename(sound), device))
        # only the built-in tone on the default device "works"
        if sound.endswith("tone.wav") and device == "":
            return SLEEP
        return "import sys; sys.stderr.write('cannot open audio device'); sys.exit(2)"

    fake_player(monkeypatch, behave)

    async def main():
        await a.start()
        n = len(a.players)
        await a.stop()
        return n

    assert asyncio.run(main()) == 1
    assert tried == [("good.mp3", "alsa/broken"), ("tone.wav", "alsa/broken"), ("tone.wav", "")]  # chosen -> tone -> default device


def test_all_players_failing_is_reported_not_raised(tmp_path, monkeypatch, caplog):
    cfg, a = make_audio(tmp_path)
    fake_player(monkeypatch, lambda s, d: "import sys; sys.exit(1)")

    async def main():
        await a.start()
        return a.players

    with caplog.at_level("WARNING", logger="audio"):
        assert asyncio.run(main()) == []
    assert "player exited at once" in caplog.text and "no audio backend started" in caplog.text


def test_error_text_from_the_player_is_in_the_log(tmp_path, monkeypatch, caplog):
    cfg, a = make_audio(tmp_path)
    fake_player(monkeypatch, lambda s, d: "import sys; sys.stderr.write('Failed to open audio device alsa/x'); sys.exit(1)")
    with caplog.at_level("WARNING", logger="audio"):
        asyncio.run(a.start())
    assert "Failed to open audio device alsa/x" in caplog.text


@pytest.mark.skipif(os.name != "posix", reason="process groups are POSIX")
def test_stop_kills_the_whole_process_group(tmp_path, monkeypatch):
    marker = tmp_path / "child.pid"
    # like the aplay fallback: a shell-ish parent with a long-running child
    code = (f"import subprocess, sys, time; c = subprocess.Popen([sys.executable, '-c', 'import time; time.sleep(30)']);"
            f"open(r'{marker}', 'w').write(str(c.pid)); time.sleep(30)")
    cfg, a = make_audio(tmp_path)
    fake_player(monkeypatch, lambda s, d: code)

    async def main():
        await a.start()
        await asyncio.sleep(0.3)
        pid = int(marker.read_text())
        await a.stop()
        await asyncio.sleep(0.3)
        return pid

    pid = asyncio.run(main())
    with pytest.raises(ProcessLookupError):
        os.kill(pid, 0)  # the grandchild is gone too


def test_one_failing_stop_does_not_leave_others_playing(tmp_path):
    cfg, a = make_audio(tmp_path)
    stopped = []

    class Bad:
        async def stop(self):
            raise RuntimeError("stuck")

    class Good:
        async def stop(self):
            stopped.append(1)

    a.players = [Bad(), Good()]
    asyncio.run(a.stop())
    assert stopped == [1] and a.players == []


def test_builtin_tone_write_is_atomic(tmp_path):
    p = tmp_path / "t.wav"
    audio.make_builtin_tone(p)
    assert p.exists() and not (tmp_path / "t.wav.tmp").exists()


def test_player_error_text_drops_jack_noise_but_keeps_the_reason():
    text = ("[ao/alsa] Playback open error: Unknown error 524\n"
            "[ao] Failed to initialize audio driver 'alsa'\n"
            "Cannot connect to server request channel\njack server is not running or cannot be started\n"
            "JackShmReadWritePtr::~JackShmReadWritePtr - Init not done for -1, skipping unlock\n"
            "[W][1.2] pw.conf | can't load config client.conf: No such file or directory\n")
    out = audio._useful_stderr(text)
    assert "Playback open error: Unknown error 524" in out and "Failed to initialize audio driver" in out
    assert "jack" not in out.lower() and "client.conf" not in out
    assert audio._useful_stderr("") == ""
