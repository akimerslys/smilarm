"""Config: TOML file merged over defaults, exposed with attribute access."""
import tomllib
from pathlib import Path

DEFAULTS = {
    "server": {"host": "0.0.0.0", "port": 8080, "token": ""},
    "camera": {
        # ESP32-CAM stream URL, or a local device index ("0") for a USB/Pi camera.
        "source": "http://alarmcam.local:81/stream",
        "width": 320,
    },
    "smile": {
        "backend": "auto",  # auto | mediapipe | haar
        "threshold": 0.5,  # mediapipe: mean mouthSmile blendshape, 0..1
        "hold_seconds": 2.5,
        "fps": 6,
        "model": "data/face_landmarker.task",
    },
    "audio": {
        "backend": "local",  # local | moode | both
        "sound": "",  # path/URL; empty = built-in generated alarm tone
        "volume_start": 15,
        "volume_max": 100,
        "ramp_seconds": 45,
    },
    "moode": {  # optional extra: Rpi3 + moOde Audio (MPD) with speakers
        "host": "moode.local",
        "port": 6600,
        "url": "",  # what MPD should play; empty = built-in tone served by this app
        "fallback_local": True,
    },
    "ring": {
        "max_minutes": 15,
        "allow_manual_dismiss": False,
        "camera_fail_seconds": 45,  # no frames this long -> manual dismiss unlocked
    },
    "events": {"webhook_url": ""},  # e.g. OpenClaw hook; receives JSON events
    "data_dir": "data",
}


class Cfg:
    def __init__(self, d: dict):
        self._d = d

    def __getattr__(self, k):
        v = self._d[k]
        return Cfg(v) if isinstance(v, dict) else v


def _merge(base: dict, over: dict) -> dict:
    out = dict(base)
    for k, v in over.items():
        out[k] = _merge(base[k], v) if isinstance(v, dict) and isinstance(base.get(k), dict) else v
    return out


def load(path: str | None) -> Cfg:
    user = {}
    if path and Path(path).exists():
        user = tomllib.loads(Path(path).read_text(encoding="utf-8"))
    return Cfg(_merge(DEFAULTS, user))
