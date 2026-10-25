"""The ESP32 sketch can't be compiled here, so guard the things the Pi side relies on."""
from pathlib import Path

ROOT = Path(__file__).resolve().parent.parent
CPP = ROOT / "firmware" / "esp32cam" / "src" / "main.cpp"
INO = ROOT / "camera" / "alarmcam" / "alarmcam.ino"


def test_the_two_copies_of_the_sketch_do_not_drift():
    assert CPP.read_text(encoding="utf-8") == INO.read_text(encoding="utf-8"), "main.cpp and alarmcam.ino must be identical"


def test_sketch_speaks_the_protocol_the_pi_uses():
    t = CPP.read_text(encoding="utf-8")
    for needle in ('"preset"', '"led"', '"for"', "/control", "/stream", "/health", "FRAMESIZE_QVGA", "FRAMESIZE_VGA", "FRAMESIZE_SVGA"):
        assert needle in t, needle
    assert "flash_off_at" in t and "flash_set(0)" in t  # the LED switches itself off if the Pi stops refreshing


def test_sketch_protects_the_weak_board():
    t = CPP.read_text(encoding="utf-8")
    assert "STREAM_MIN_FRAME_MS" in t and "vTaskDelay" in t  # stream capped (~15 fps): 50+ fps loaded the Wi-Fi radio and the supply
    assert "Soft start" in t and "flash_level_now" in t  # LED ramps up instead of a full-power current spike
    assert t.count("{") == t.count("}")  # a cheap syntax sanity check, since nothing compiles this in CI
