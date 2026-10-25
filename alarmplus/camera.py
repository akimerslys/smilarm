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


def control_url(source, quality: str) -> str | None:
    """ESP32-CAM /control endpoint for an http stream source; None for local cameras."""
    u = urlparse(str(source))
    if u.scheme not in ("http", "https") or not u.hostname:
        return None
    return f"http://{u.hostname}/control?preset={quality}"


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
    def __init__(self, source: str, quality: str = "low", width: int = 0, rotate: int = 0):
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

    @staticmethod
    def _check_rotate(rotate: int) -> int:
        if rotate not in ROTATIONS:
            raise ValueError(f"camera.rotate must be one of {sorted(ROTATIONS)}")
        return rotate

    def reconfigure(self, source, quality: str, width: int = 0, rotate: int = 0):
        """Apply new settings; they take effect the next time the camera is opened."""
        if quality not in PRESETS:
            raise ValueError(f"camera.quality must be one of {sorted(PRESETS)}")
        self.source = int(source) if str(source).isdigit() else source
        self.quality, self.width = quality, width or PRESETS[quality]
        self.rotate = self._check_rotate(rotate)

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
        }

    def _configure(self):
        url = control_url(self.source, self.quality)
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
