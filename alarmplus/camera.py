"""Reference-counted camera: opens the stream only while someone needs frames."""
import logging
import os
import socket
import threading
import time
import urllib.request
from urllib.parse import urlparse

# rw_timeout/timeout (us): a stalled TCP stream must fail and reconnect instead of blocking read() forever
os.environ.setdefault(
    "OPENCV_FFMPEG_CAPTURE_OPTIONS",
    "fflags;nobuffer|flags;low_delay|probesize;32768|analyzeduration;0|rw_timeout;4000000|timeout;4000000",
)
import cv2  # noqa: E402

log = logging.getLogger("camera")

# preset -> detection width (the ESP32 side resolution is chosen by the same name in firmware)
PRESETS = {"low": 320, "medium": 480, "high": 640}
ROTATIONS = {0: None, 90: cv2.ROTATE_90_CLOCKWISE, 180: cv2.ROTATE_180, 270: cv2.ROTATE_90_COUNTERCLOCKWISE}


def _control_base(source, port: int = 80) -> str | None:
    u = urlparse(str(source))
    if u.scheme not in ("http", "https") or not u.hostname:
        return None
    return f"http://{u.hostname}:{port}/control" if port != 80 else f"http://{u.hostname}/control"


def control_url(source, quality: str, port: int = 80) -> str | None:
    """ESP32-CAM /control endpoint for an http stream source; None for local cameras."""
    base = _control_base(source, port)
    return f"{base}?preset={quality}" if base else None


FLASH_TTL = 180  # seconds the ESP32 keeps the LED on by itself; we refresh well before that
FLASH_REFRESH = 60


def flash_url(source, percent: int, ttl: int = FLASH_TTL, port: int = 80) -> str | None:
    """ESP32-CAM flash LED: led=0..255 (PWM brightness), for=seconds until it switches itself off."""
    base = _control_base(source, port)
    return f"{base}?led={round(percent * 255 / 100)}&for={ttl}" if base else None


def diagnose_source(source, timeout: float = 3.0) -> str:
    """Why can't we read this camera? A human sentence, found by probing the network path step by step."""
    if isinstance(source, int):
        return f"cannot open local camera #{source} (not plugged in, or in use by another program)"
    u = urlparse(str(source))
    host, port = u.hostname, u.port or (443 if u.scheme == "https" else 80)
    if not host:
        return f"'{source}' is not a valid camera URL (expected http://IP:81/stream or a camera number)"
    try:
        socket.create_connection((host, port), timeout).close()
    except socket.gaierror:
        return f"cannot resolve '{host}' (use the camera's IP address instead of a .local name)"
    except (socket.timeout, TimeoutError):
        return f"no answer from {host}:{port} (camera is off, the IP is wrong, or it is not on this network)"
    except ConnectionRefusedError:
        return f"{host}:{port} refused the connection (device is up but nothing listens on that port: wrong port, or firmware not running)"
    except OSError as e:
        return f"cannot reach {host}:{port}: {e.strerror or e}"
    return (f"connected to {host}:{port} but no video arrived (wrong URL path? the ESP32 serves only ONE stream at a time, "
            "so close other viewers of the camera)")


class Camera:
    def __init__(self, source: str, quality: str = "low", width: int = 0, rotate: int = 0, flash: bool = False, flash_level: int = 30):
        if quality not in PRESETS:
            raise ValueError(f"camera.quality must be one of {sorted(PRESETS)}")
        self.source = int(source) if str(source).isdigit() else source
        self.quality = quality
        self.width = width or PRESETS[quality]
        self.rotate = self._check_rotate(rotate)
        self._refs = 0
        self._lock = threading.Lock()
        self._stop: threading.Event | None = None
        self._frame = None
        self._ts = 0.0
        self._n = self._reconnects = 0
        self._fps = 0.0
        self._t_open = 0.0
        self.state = "off"  # off | connecting | streaming | error
        self.error = ""
        self.flash, self.flash_level = bool(flash), flash_level  # optional ESP32 flash LED while the camera is in use
        self.flash_state, self.flash_error = "off", ""  # off | on | error
        self.control_port = 80  # the ESP32's control server (the stream itself is on 81)
        self._flash_gen = 0

    @staticmethod
    def _check_rotate(rotate: int) -> int:
        if rotate not in ROTATIONS:
            raise ValueError(f"camera.rotate must be one of {sorted(ROTATIONS)}")
        return rotate

    def reconfigure(self, source, quality: str, width: int = 0, rotate: int = 0, flash: bool | None = None, flash_level: int | None = None):
        """Apply new settings. Picture settings take effect the next time the camera is opened;
        the flash follows immediately, even while the camera is in use."""
        if quality not in PRESETS:
            raise ValueError(f"camera.quality must be one of {sorted(PRESETS)}")
        self.source = int(source) if str(source).isdigit() else source
        self.quality, self.width = quality, width or PRESETS[quality]
        self.rotate = self._check_rotate(rotate)
        if flash is not None or flash_level is not None:
            with self._lock:
                self.flash = self.flash if flash is None else bool(flash)
                self.flash_level = self.flash_level if flash_level is None else flash_level
                self._start_flash()

    def acquire(self):
        with self._lock:
            self._refs += 1
            if self._refs == 1:
                self._stop = threading.Event()
                self._frame, self._ts = None, 0.0
                self._n = self._reconnects = 0
                self._fps, self._t_open = 0.0, time.monotonic()
                self.state, self.error = "connecting", ""
                threading.Thread(target=self._run, args=(self._stop,), daemon=True).start()
                self._start_flash()

    def release(self):
        with self._lock:
            self._refs = max(0, self._refs - 1)
            if self._refs == 0 and self._stop:
                self._stop.set()
                self.state = "off"

    def latest(self):
        return self._frame, self._ts

    def stats(self) -> dict:
        """Camera-side health, independent of detection (for /api/debug)."""
        now = time.monotonic()
        return {
            "cam_fps": round(self._fps, 1),
            "cam_frame_age_ms": round((now - self._ts) * 1000) if self._ts else None,
            "cam_frames": self._n,
            "cam_reconnects": self._reconnects,
            "cam_state": self.state,
            "cam_error": self.error,
            "cam_flash": self.flash_state,
            "cam_flash_error": self.flash_error,
        }

    # ---- flash LED (optional): on while the camera is in use, i.e. ringing, camera view, smile test ----
    def _flash_call(self, level: int, gen: int):
        url = flash_url(self.source, level, port=self.control_port)
        if not url:
            return
        try:
            urllib.request.urlopen(url, timeout=2).read()
            if gen == self._flash_gen:
                self.flash_state, self.flash_error = ("on" if level > 0 else "off"), ""
        except Exception as e:
            msg = ("this firmware has no flash support (reflash the ESP32 with the current sketch)"
                   if getattr(e, "code", None) in (400, 404) else str(getattr(e, "reason", e)))
            if gen == self._flash_gen:
                if msg != self.flash_error:
                    log.warning("flashlight: %s", msg)
                self.flash_state, self.flash_error = "error", msg

    def _start_flash(self):
        """(Re)start the flash keep-alive for the current settings. Call with the lock held."""
        self._flash_gen += 1
        gen = self._flash_gen
        if self._refs > 0 and self.flash and control_url(self.source, "low"):
            threading.Thread(target=self._flash_loop, args=(gen, self._stop), daemon=True).start()
        elif self.flash_state == "on":  # switched off in Settings while the camera was open
            threading.Thread(target=self._flash_call, args=(0, gen), daemon=True).start()
        elif self.flash_state == "error":
            self.flash_state, self.flash_error = "off", ""

    def _flash_loop(self, gen: int, stop: threading.Event):
        level = self.flash_level
        self._flash_call(level, gen)
        while not stop.wait(FLASH_REFRESH) and gen == self._flash_gen:
            level = self.flash_level
            self._flash_call(level, gen)  # keep-alive: the ESP32 turns the LED off by itself if we stop talking
        if gen == self._flash_gen:  # a newer loop (quick re-open) owns the LED: leave it alone
            self._flash_call(0, gen)

    def _configure(self):
        url = control_url(self.source, self.quality, self.control_port)
        if not url:
            return
        try:
            urllib.request.urlopen(url, timeout=3).read()
        except Exception as e:  # old firmware without /control: stream still works at its default
            log.warning("could not set camera preset %s: %s", self.quality, e)

    def _run(self, stop: threading.Event):
        self._configure()
        cap, fails, backoff, last_msg = None, 0, 1.0, ""
        while not stop.is_set():
            if cap is None:
                cap = cv2.VideoCapture(self.source)
                cap.set(cv2.CAP_PROP_BUFFERSIZE, 1)
            ok, f = cap.read()
            if stop.is_set():  # released while blocked in read(): don't touch shared state any more
                break
            if not ok:
                fails += 1
                if fails >= 3:
                    cap.release()
                    cap, fails = None, 0
                    self._reconnects += 1
                    msg = diagnose_source(self.source)
                    self.state, self.error = "error", msg
                    if msg != last_msg:  # say it once per distinct cause, not every retry
                        log.warning("camera %s: %s (retrying every %.0f-10 s)", self.source, msg, backoff)
                        last_msg = msg
                    else:
                        log.debug("camera still failing: %s", msg)
                    stop.wait(backoff)
                    backoff = min(backoff * 2, 10.0)  # don't spam when the camera is off
                continue
            fails, backoff = 0, 1.0
            if self.state != "streaming":
                self.state, self.error, last_msg = "streaming", "", ""
                log.info("camera streaming from %s (%dx%d)", self.source, f.shape[1], f.shape[0])
            h, w = f.shape[:2]
            if w > self.width:
                f = cv2.resize(f, (self.width, h * self.width // w), interpolation=cv2.INTER_AREA)
            if ROTATIONS[self.rotate] is not None:
                f = cv2.rotate(f, ROTATIONS[self.rotate])
            now = time.monotonic()
            if self._ts:
                self._fps = 0.9 * self._fps + 0.1 / max(now - self._ts, 1e-3)
            self._frame, self._ts, self._n = f, now, self._n + 1
        if cap:
            cap.release()
