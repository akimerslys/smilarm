"""Smile detection. Only runs while an alarm is ringing."""
import logging
from pathlib import Path

log = logging.getLogger("smile")


class SmileGate:
    """Passes once the score stays >= threshold for `hold` seconds.
    Short dropouts (blink, a missed frame) are tolerated up to `grace` seconds."""

    def __init__(self, threshold: float, hold: float, grace: float = 0.6):
        self.threshold, self.hold, self.grace = threshold, hold, grace
        self._since = self._last = None
        self.progress = 0.0

    def update(self, score: float | None, now: float) -> bool:
        if score is not None and score >= self.threshold:
            if self._since is None:
                self._since = now
            self._last = now
        elif self._since is not None and now - self._last > self.grace:
            self._since = None
        self.progress = 0.0 if self._since is None else min(1.0, (now - self._since) / self.hold)
        return self.progress >= 1.0


class MediaPipeDetector:
    """Face Landmarker blendshapes: robust to lighting/angle, ~10 ms/frame on Pi 5."""

    def __init__(self, model: str):
        import cv2
        import mediapipe as mp
        from mediapipe.tasks import python as mpp
        from mediapipe.tasks.python import vision

        self._cv2, self._mp = cv2, mp
        self._lm = vision.FaceLandmarker.create_from_options(
            vision.FaceLandmarkerOptions(
                base_options=mpp.BaseOptions(model_asset_path=model),
                output_face_blendshapes=True,
                num_faces=1,
            )
        )

    def score(self, bgr) -> float | None:
        rgb = self._cv2.cvtColor(bgr, self._cv2.COLOR_BGR2RGB)
        res = self._lm.detect(self._mp.Image(image_format=self._mp.ImageFormat.SRGB, data=rgb))
        if not res.face_blendshapes:
            return None
        d = {c.category_name: c.score for c in res.face_blendshapes[0]}
        return (d["mouthSmileLeft"] + d["mouthSmileRight"]) / 2


CASCADES = ("haarcascade_frontalface_default.xml", "haarcascade_smile.xml")


def find_cascades(dirs) -> Path | None:
    """First directory that holds both cascade files. (The OpenCV 5 wheels ship an empty cv2/data folder.)"""
    for d in dirs:
        p = Path(d)
        if all((p / name).is_file() for name in CASCADES):
            return p
    return None


def cascade_dirs() -> list[str]:
    import os

    dirs = []
    try:
        import cv2

        dirs.append(cv2.data.haarcascades)
    except Exception:
        pass
    # the Docker image and the installer put a copy next to the face model
    return dirs + [os.environ.get("SMILARM_CASCADES", ""), "data/cascades", "/opt/models"]


class HaarDetector:
    """Fallback (OpenCV cascades) for when MediaPipe cannot load. Less accurate, binary score."""

    def __init__(self, dirs=None):
        import cv2

        folder = find_cascades(cascade_dirs() if dirs is None else dirs)
        if folder is None:  # fail loudly: an empty classifier would just never see a face, and the alarm would never stop
            raise RuntimeError("the Haar cascade files (%s) were not found; reinstall, or use backend=mediapipe" % ", ".join(CASCADES))
        self._cv2 = cv2
        self._face = cv2.CascadeClassifier(str(folder / CASCADES[0]))
        self._smile = cv2.CascadeClassifier(str(folder / CASCADES[1]))
        if self._face.empty() or self._smile.empty():
            raise RuntimeError(f"could not load the Haar cascade files from {folder}")

    def score(self, bgr) -> float | None:
        g = self._cv2.cvtColor(bgr, self._cv2.COLOR_BGR2GRAY)
        faces = self._face.detectMultiScale(g, 1.2, 5, minSize=(60, 60))
        if len(faces) == 0:
            return None
        x, y, w, h = max(faces, key=lambda f: f[2] * f[3])
        mouth = g[y + h // 2 : y + h, x : x + w]
        return 1.0 if len(self._smile.detectMultiScale(mouth, 1.7, 22, minSize=(w // 4, h // 10))) else 0.0


def make_detector(backend: str, model: str):
    if backend in ("auto", "mediapipe") and Path(model).exists():
        try:
            d = MediaPipeDetector(model)
            log.info("smile backend: mediapipe")
            return d
        except Exception as e:  # missing wheel on this platform, etc.
            if backend == "mediapipe":
                raise
            log.warning("mediapipe unavailable (%s), falling back to haar", e)
    elif backend == "mediapipe":
        raise FileNotFoundError(model)
    log.info("smile backend: haar")
    return HaarDetector()
