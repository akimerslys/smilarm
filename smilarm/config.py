"""Config: TOML file merged over defaults, exposed with attribute access."""
import copy
import tomllib
from pathlib import Path

DEFAULTS = {
    "general": {"timezone": ""},  # IANA name, e.g. Europe/London; empty = the system timezone (UTC in a container!)
    "server": {"host": "0.0.0.0", "port": 8080, "token": "", "public_url": ""},
    "camera": {
        # ESP32-CAM stream URL, or a local device index ("0") for a USB/Pi camera.
        "source": "http://smilarm-cam.local:81/stream",
        # low = 320x240 (default, lightest) | medium = 640x480 | high = 800x600.
        # Sets the ESP32 resolution and the size frames are downscaled to for detection.
        "quality": "low",
        # Rotate frames clockwise by 0/90/180/270 degrees so the face is upright for the detector
        # (a mounted-sideways camera needs 90 or 270). Applies to the preview too.
        "rotate": 0,
        # ESP32 flash LED (GPIO 4): on while the camera is in use (alarm ringing, Camera view, Smile test).
        # Helps in a dark room; it is bright and warms up, so keep the level modest. Needs the current firmware.
        "flashlight": False,
        "flashlight_level": 30,  # percent of full brightness
        "width": 0,  # 0 = take it from the preset; set a number to override
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
        "sound_dir": "",  # folder of sounds; a random one is picked per alarm (overrides "sound")
        "device": "",  # mpv/aplay output device (e.g. alsa/plughw:1,0); empty = system default
        "volume_start": 15,
        "volume_max": 100,
        "ramp_seconds": 45,
    },
    "moode": {  # optional extra: Rpi3 + moOde Audio (MPD) with speakers
        "host": "moode.local",
        "port": 6600,
        "url": "",  # what MPD should play; empty = built-in tone served by this app
        "fallback_local": True,
        "library_dir": "",  # folder in moOde's MPD library (e.g. "ALARM_TRACKS"): random file, played from the Rpi3 itself
    },
    "ring": {
        "max_minutes": 15,
        "allow_manual_dismiss": False,
        "camera_fail_seconds": 45,  # no frames this long -> manual dismiss unlocked
    },
    "logging": {"level": "INFO"},  # DEBUG | INFO | WARNING
    "events": {"webhook_url": ""},  # e.g. OpenClaw hook; receives JSON events
    # Shell commands run ON THIS DEVICE (the one running smilarm). Env: ALARM_EVENT, ALARM_ID,
    # ALARM_LABEL, ALARM_TIME, ALARM_REASON (smile|manual|timeout), ALARM_SECONDS.
    "hooks": {"on_ring": "", "on_dismiss": "", "timeout_seconds": 120},
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
    return Cfg(_merge(copy.deepcopy(DEFAULTS), user))
