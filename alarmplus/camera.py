"""Reference-counted camera: opens the stream only while someone needs frames."""
import logging
import os
import threading
import time

os.environ.setdefault("OPENCV_FFMPEG_CAPTURE_OPTIONS", "fflags;nobuffer|flags;low_delay|probesize;32768|analyzeduration;0")
import cv2  # noqa: E402

log = logging.getLogger("camera")


class Camera:
    def __init__(self, source: str, width: int):
        self.source = int(source) if str(source).isdigit() else source
        self.width = width
        self._refs = 0
        self._lock = threading.Lock()
        self._stop: threading.Event | None = None
        self._frame = None
        self._ts = 0.0

    def acquire(self):
        with self._lock:
            self._refs += 1
            if self._refs == 1:
                self._stop = threading.Event()
                self._frame, self._ts = None, 0.0
                threading.Thread(target=self._run, args=(self._stop,), daemon=True).start()

    def release(self):
        with self._lock:
            self._refs = max(0, self._refs - 1)
            if self._refs == 0 and self._stop:
                self._stop.set()

    def latest(self):
        return self._frame, self._ts

    def _run(self, stop: threading.Event):
        cap, fails = None, 0
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
                    log.warning("camera read failed, reconnecting")
                    stop.wait(1.0)
                continue
            fails = 0
            h, w = f.shape[:2]
            if w > self.width:
                f = cv2.resize(f, (self.width, h * self.width // w), interpolation=cv2.INTER_AREA)
            self._frame, self._ts = f, time.monotonic()
        if cap:
            cap.release()
