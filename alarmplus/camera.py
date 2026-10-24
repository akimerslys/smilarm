"""Reference-counted camera: opens the stream only while someone needs frames."""
import logging
import os
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


def control_url(source, quality: str) -> str | None:
    """ESP32-CAM /control endpoint for an http stream source; None for local cameras."""
    u = urlparse(str(source))
    if u.scheme not in ("http", "https") or not u.hostname:
        return None
    return f"http://{u.hostname}/control?preset={quality}"


class Camera:
    def __init__(self, source: str, quality: str = "low", width: int = 0):
        if quality not in PRESETS:
            raise ValueError(f"camera.quality must be one of {sorted(PRESETS)}")
        self.source = int(source) if str(source).isdigit() else source
        self.quality = quality
        self.width = width or PRESETS[quality]
        self._refs = 0
        self._lock = threading.Lock()
        self._stop: threading.Event | None = None
        self._frame = None
        self._ts = 0.0
        self._n = self._reconnects = 0
        self._fps = 0.0
        self._t_open = 0.0

    def reconfigure(self, source, quality: str, width: int = 0):
        """Apply new settings; they take effect the next time the camera is opened."""
        if quality not in PRESETS:
            raise ValueError(f"camera.quality must be one of {sorted(PRESETS)}")
        self.source = int(source) if str(source).isdigit() else source
        self.quality, self.width = quality, width or PRESETS[quality]

    def acquire(self):
        with self._lock:
            self._refs += 1
            if self._refs == 1:
                self._stop = threading.Event()
                self._frame, self._ts = None, 0.0
                self._n = self._reconnects = 0
                self._fps, self._t_open = 0.0, time.monotonic()
                threading.Thread(target=self._run, args=(self._stop,), daemon=True).start()

    def release(self):
        with self._lock:
            self._refs = max(0, self._refs - 1)
            if self._refs == 0 and self._stop:
                self._stop.set()

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
        cap, fails, backoff = None, 0, 1.0
        while not stop.is_set():
            if cap is None:
                cap = cv2.VideoCapture(self.source)
                cap.set(cv2.CAP_PROP_BUFFERSIZE, 1)
            ok, f = cap.read()
            if not ok:
                fails += 1
                if fails >= 3:
                    cap.release()
                    cap, fails = None, 0
                    self._reconnects += 1
                    log.warning("camera read failed, retrying in %.0fs", backoff)
                    stop.wait(backoff)
                    backoff = min(backoff * 2, 10.0)  # don't spam when the camera is off
                continue
            fails, backoff = 0, 1.0
            h, w = f.shape[:2]
            if w > self.width:
                f = cv2.resize(f, (self.width, h * self.width // w), interpolation=cv2.INTER_AREA)
            now = time.monotonic()
            if self._ts:
                self._fps = 0.9 * self._fps + 0.1 / max(now - self._ts, 1e-3)
            self._frame, self._ts, self._n = f, now, self._n + 1
        if cap:
            cap.release()
