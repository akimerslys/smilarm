"""Settings editable from the web UI: a validated allowlist, saved back into config.toml.

The file is edited in place (comments and untouched lines stay) and the change is applied live.
Anything that can run commands or reach other hosts (`sensitive`) is only editable when
`server.token` is set.
"""
import copy
import json
import logging
import os
import re
import shutil
from pathlib import Path

from . import clock
from . import config as config_mod
from .audio import SOUND_EXT

log = logging.getLogger("settings")

_HOST = re.compile(r"^[A-Za-z0-9._-]{1,100}$")
_DEVICE = re.compile(r"^[\w:/.,@=+\-]{0,80}$")
_URL = re.compile(r"^https?://[^\s]+$")
_ORIGIN = re.compile(r"^https?://[A-Za-z0-9._-]+(:[0-9]{1,5})?/?$")
_TOKEN = re.compile(r"^[!-~]{0,128}$")


def _f(key, type_, help_, **kw):
    return {"key": key, "type": type_, "help": help_, **kw}


SCHEMA = [
    _f("general.timezone", "str", "Your timezone, e.g. Europe/London or America/New_York. Alarm times use it. Empty = the device's own timezone (a container is usually UTC!)."),
    _f("server.host", "str", "Interface the web UI listens on (0.0.0.0 = the whole network). Needs a restart.", restart=True),
    _f("server.port", "int", "Web UI / API port. Needs a restart (in Docker with the default host networking this is the port directly).", min=1, max=65535, restart=True),
    _f("server.token", "str", "Access token. Empty = open to the whole network. Once set, the UI asks for it and every API call needs it (Bearer token or ?token=). Required to edit commands and the webhook. Printable characters, no spaces, max 128.", secret=True),
    _f("server.public_url", "str", "Address other devices (the moOde speaker) use to reach this app, e.g. http://192.168.1.20:8080. Empty = detected automatically (wrong inside Docker bridge networking)."),
    _f("smile.backend", "choice", "Smile detector: auto (MediaPipe, falling back to OpenCV Haar), mediapipe, or haar (less accurate, no model file).", choices=["auto", "mediapipe", "haar"]),
    _f("smile.model", "str", "MediaPipe face-landmarker model file. Normally leave as is."),
    _f("smile.threshold", "float", "Smile score needed to count as smiling (lower = easier).", min=0.1, max=0.95, step=0.01),
    _f("smile.hold_seconds", "float", "How long the smile must be held.", min=0.5, max=10, step=0.1),
    _f("smile.fps", "int", "Detections per second while ringing.", min=1, max=15),
    _f("camera.quality", "choice", "ESP32 resolution preset: low 320x240, medium 640x480, high 800x600.", choices=["low", "medium", "high"]),
    _f("camera.rotate", "choice", "Rotate the picture clockwise so faces are upright (sideways camera: try 90, else 270).", choices=[0, 90, 180, 270]),
    _f("camera.flashlight", "bool", "Switch the ESP32 flash LED on while the camera is in use (alarm ringing, Camera view, Smile test). Needs the current firmware."),
    _f("camera.flashlight_level", "int", "Flash LED brightness, percent. It is very bright and gets warm: stay low for long alarms.", min=1, max=100),
    _f("camera.width", "int", "Width frames are scaled to for detection; 0 = take it from the quality preset.", min=0, max=1920),
    _f("camera.source", "str", "ESP32 stream URL (http://IP:81/stream) or a local camera index like 0."),
    _f("audio.backend", "choice", "Where the alarm sound plays.", choices=["local", "moode", "both"]),
    _f("audio.volume_start", "int", "Starting volume, %.", min=0, max=100),
    _f("audio.volume_max", "int", "Final volume, %.", min=0, max=100),
    _f("audio.ramp_seconds", "int", "Seconds to ramp from start to max volume.", min=0, max=600),
    _f("audio.sound", "str", "Sound file path or http(s) URL. Empty = built-in tone."),
    _f("audio.sound_dir", "str", "Folder of sounds; one is picked at random per alarm. Overrides 'sound'."),
    _f("audio.device", "str", "mpv/aplay output device, e.g. alsa/plughw:1,0 (see docs/hardware.md). Empty = default."),
    _f("moode.host", "str", "Hostname or IP of the moOde device."),
    _f("moode.port", "int", "MPD port.", min=1, max=65535),
    _f("moode.url", "str", "URL moOde plays. Empty = this app serves the sound itself."),
    _f("moode.library_dir", "str", "Folder in moOde's music library (e.g. ALARM_TRACKS): a random file from it is played, nothing is sent from this device."),
    _f("moode.fallback_local", "bool", "Play on this device if moOde is unreachable."),
    _f("ring.max_minutes", "float", "Failsafe: stop ringing after this many minutes.", min=1, max=120, step=1),
    _f("ring.allow_manual_dismiss", "bool", "Allow dismissing without a smile (defeats the purpose)."),
    _f("ring.camera_fail_seconds", "int", "Camera silent this long = manual dismiss unlocked.", min=5, max=600),
    _f("logging.level", "choice", "Log detail for the web UI log and `docker compose logs` / journalctl: DEBUG adds per-second detection and camera numbers.", choices=["DEBUG", "INFO", "WARNING"]),
    _f("hooks.on_ring", "str", "Shell command run on this device when an alarm starts.", sensitive=True),
    _f("hooks.on_dismiss", "str", "Shell command run on this device when an alarm ends.", sensitive=True),
    _f("hooks.timeout_seconds", "int", "Kill a hook that runs longer than this.", min=1, max=600, sensitive=True),
    _f("events.webhook_url", "str", "POST ringing/dismissed events here (e.g. OpenClaw).", sensitive=True),
]
BY_KEY = {s["key"]: s for s in SCHEMA}


def _num(v, spec, cast):
    if isinstance(v, bool) or not isinstance(v, (int, float)):
        raise ValueError("must be a number")
    if cast is int and v != int(v):
        raise ValueError("must be a whole number")
    v = cast(v)
    if not spec["min"] <= v <= spec["max"]:
        raise ValueError(f"must be between {spec['min']} and {spec['max']}")
    return v


def validate(key: str, v):
    spec = BY_KEY.get(key)
    if not spec:
        raise ValueError("unknown setting")
    t = spec["type"]
    if t == "float":
        return _num(v, spec, float)
    if t == "int":
        return _num(v, spec, int)
    if t == "bool":
        if not isinstance(v, bool):
            raise ValueError("must be true or false")
        return v
    if t == "choice":
        if isinstance(v, bool) or v not in spec["choices"]:
            raise ValueError("must be one of " + ", ".join(map(str, spec["choices"])))
        return v
    if not isinstance(v, str) or "\0" in v:
        raise ValueError("must be text")
    v = v.strip()
    if key.startswith("hooks."):
        if len(v) > 500:
            raise ValueError("max 500 characters")
    elif key == "general.timezone":
        if v and not clock.valid_timezone(v):
            raise ValueError("unknown timezone; use an IANA name such as Europe/London or America/New_York, or leave empty")
    elif key == "server.host":
        if not _HOST.match(v):
            raise ValueError("must be an IP address or hostname, e.g. 0.0.0.0")
    elif key == "server.token":
        if not _TOKEN.match(v):
            raise ValueError("up to 128 printable characters, no spaces")
    elif key == "server.public_url":
        if v and not _ORIGIN.match(v):
            raise ValueError("must look like http://192.168.1.20:8080 (scheme, host, optional port, no path)")
    elif key == "smile.model":
        if v != config_mod.DEFAULTS["smile"]["model"] and not Path(v).expanduser().is_file():  # the default is fetched by the installer/image
            raise ValueError("model file not found on this device")
        if not v:
            raise ValueError("a path is required (the default is data/face_landmarker.task)")
    elif key == "camera.source":
        if not (v.isdigit() or _URL.match(v)):
            raise ValueError("must be a camera index (0, 1, ...) or an http(s) URL")
    elif key in ("moode.url", "events.webhook_url"):
        if v and not _URL.match(v):
            raise ValueError("must be empty or an http(s) URL")
    elif key == "moode.library_dir":
        if len(v) > 200 or v.startswith("/") or ".." in v.split("/"):
            raise ValueError("must be a relative MPD library path such as ALARM_TRACKS")
    elif key == "moode.host":
        if not _HOST.match(v):
            raise ValueError("must be a hostname or IP")
    elif key == "audio.device":
        if not _DEVICE.match(v):
            raise ValueError("invalid device name")
    elif key == "audio.sound":
        if v and not _URL.match(v):
            p = Path(v).expanduser()
            if not p.is_file() or p.suffix.lower() not in SOUND_EXT:
                raise ValueError("file not found on this device, or not one of " + "/".join(sorted(SOUND_EXT)))
    elif key == "audio.sound_dir":
        if v and not Path(v).expanduser().is_dir():
            raise ValueError("directory not found on this device")
    return v


class Settings:
    def __init__(self, cfg, path: Path, legacy_json: Path | None = None):
        self.cfg, self.path = cfg, Path(path)
        self.defaults = copy.deepcopy(config_mod.DEFAULTS)
        self.listeners = []
        self._boot = {k: self._get(k) for k, s in BY_KEY.items() if s.get("restart")}  # values the running process started with
        if legacy_json and legacy_json.exists():
            self._migrate(legacy_json)

    # -- helpers --
    def _get(self, key, d=None):
        s, n = key.split(".")
        return (d or self.cfg._d)[s][n]

    def _set(self, key, v):
        s, n = key.split(".")
        self.cfg._d[s][n] = v

    @property
    def locked(self) -> bool:
        """Sensitive settings (commands, webhook) need an API token so the LAN can't set them."""
        return not self.cfg.server.token

    @property
    def restart_needed(self) -> bool:
        """True when a setting that is only read at start was changed since this process began."""
        return any(self._get(k) != v for k, v in self._boot.items())

    def _notify(self):
        for fn in self.listeners:
            fn()

    def _write(self, clean: dict):
        """Edit config.toml in place (creating it from the defaults if missing); keep one .bak."""
        from .cfgtools import render_defaults, set_values

        text = self.path.read_text(encoding="utf-8") if self.path.exists() else render_defaults()
        new = set_values(text, clean)
        self.path.parent.mkdir(parents=True, exist_ok=True)
        if self.path.exists():
            shutil.copyfile(self.path, self.path.with_name(self.path.name + ".bak"))
        tmp = self.path.with_name(self.path.name + ".tmp")
        tmp.write_text(new, encoding="utf-8", newline="\n")
        os.replace(tmp, self.path)

    def _migrate(self, legacy: Path):
        """Older versions kept web edits in data/settings.json: fold them into config.toml once."""
        try:
            old = {k: validate(k, v) for k, v in json.loads(legacy.read_text(encoding="utf-8")).items() if k in BY_KEY}
            if old:
                self._write(old)
                for k, v in old.items():
                    self._set(k, v)
            legacy.rename(legacy.with_name(legacy.name + ".migrated"))
            log.info("moved %d saved setting(s) from %s into %s", len(old), legacy.name, self.path.name)
        except (OSError, ValueError) as e:
            log.warning("could not migrate %s: %s", legacy, e)

    # -- API --
    def describe(self):
        return [
            {**s, "value": self._get(s["key"]), "default": self._get(s["key"], self.defaults),
             "overridden": self._get(s["key"]) != self._get(s["key"], self.defaults),
             "locked": bool(s.get("sensitive")) and self.locked}
            for s in SCHEMA
        ]

    def update(self, updates: dict):
        """Validate everything, write config.toml, then apply live.
        Raises ValueError({key: msg}), PermissionError, or OSError (file not writable)."""
        locked = [k for k in updates if BY_KEY.get(k, {}).get("sensitive") and self.locked]
        if locked:
            raise PermissionError("set server.token in config.toml to edit: " + ", ".join(locked))
        errors, clean = {}, {}
        for k, v in updates.items():
            try:
                clean[k] = validate(k, v)
            except ValueError as e:
                errors[k] = str(e)
        trial = {k: clean.get(k, self._get(k)) for k in ("audio.volume_start", "audio.volume_max")}
        if trial["audio.volume_max"] < trial["audio.volume_start"]:
            errors["audio.volume_max"] = "must be >= volume_start"
        if errors:
            raise ValueError(errors)
        if not clean:
            return
        log.info("settings saved to %s: %s", self.path.name, ", ".join(f"{k}={v!r}" for k, v in clean.items() if not k.startswith("hooks.") and k != "events.webhook_url") or "(commands/webhook changed)")
        self._write(clean)  # file first: if it can't be saved, nothing changes in memory either
        for k, v in clean.items():
            self._set(k, v)
        self._notify()

    def reload(self) -> dict:
        """Re-read config.toml (e.g. after editing it by hand). Returns {key: problem} for rejected values."""
        loaded = config_mod.load(str(self.path))
        problems = {}
        for s in SCHEMA:
            k = s["key"]
            try:
                self._set(k, validate(k, self._get(k, loaded._d)))
            except ValueError as e:
                problems[k] = str(e)
        self._notify()
        return problems

    def restore_defaults(self):
        """Write the built-in default of every editable setting (that isn't locked) into config.toml."""
        vals = {s["key"]: self._get(s["key"], self.defaults) for s in SCHEMA if not (s.get("sensitive") and self.locked)}
        self._write(vals)
        for k, v in vals.items():
            self._set(k, v)
        self._notify()
