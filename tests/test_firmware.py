"""The ESP32 sketch can't be compiled here, so guard the things the Pi side relies on."""
from pathlib import Path

ROOT = Path(__file__).resolve().parent.parent
FW = ROOT / "firmware" / "smilarm_cam"
INO = FW / "smilarm_cam.ino"


def test_there_is_exactly_one_copy_of_the_sketch():
    assert INO.is_file()
    # the old duplicate locations hold no files (an empty folder can linger on Windows while an IDE has it open)
    for old in (ROOT / "camera", ROOT / "firmware" / "esp32cam"):
        assert not [f for f in old.rglob("*") if f.is_file()] if old.exists() else True
    assert sorted(p.name for p in ROOT.rglob("*.ino")) == ["smilarm_cam.ino"]  # Arduino IDE needs folder name == sketch name
    assert INO.parent.name == INO.stem


def test_platformio_builds_the_same_sketch():
    ini = (FW / "platformio.ini").read_text(encoding="utf-8")
    assert "src_dir = ." in ini and "board = esp32cam" in ini and "BOARD_HAS_PSRAM" in ini
    assert "smilarm-cam" in (FW / "secrets.h.example").read_text(encoding="utf-8")  # default mDNS name
    assert "secrets.h" in (ROOT / ".gitignore").read_text(encoding="utf-8")  # Wi-Fi credentials never get committed


def test_sketch_speaks_the_protocol_the_pi_uses():
    t = INO.read_text(encoding="utf-8")
    for needle in ('"preset"', '"led"', '"for"', "/control", "/stream", "/health", "FRAMESIZE_QVGA", "FRAMESIZE_VGA", "FRAMESIZE_SVGA"):
        assert needle in t, needle
    assert "flash_off_at" in t and "flash_set(0)" in t  # the LED switches itself off if the Pi stops refreshing


def test_sketch_protects_the_weak_board():
    t = INO.read_text(encoding="utf-8")
    assert "STREAM_MIN_FRAME_MS" in t and "vTaskDelay" in t  # stream capped (~15 fps): 50+ fps loaded the Wi-Fi radio and the supply
    assert "Soft start" in t and "flash_level_now" in t  # LED ramps up instead of a full-power current spike
    assert t.count("{") == t.count("}")  # a cheap syntax sanity check, since nothing compiles this in CI
