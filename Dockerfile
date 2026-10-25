# syntax=docker/dockerfile:1
# Smilarm — the primary way to run it. Multi-arch friendly (arm64 for Raspberry Pi, amd64).
# Debian 13 (trixie): its alsa-lib (1.2.14) can drive the Raspberry Pi HDMI audio through the `plughw` converter; bookworm's 1.2.8 cannot
# ("Sample format non available"), which silenced the alarm in a bookworm-based image.
FROM python:3.13-slim-trixie

ENV PYTHONUNBUFFERED=1 \
    PYTHONDONTWRITEBYTECODE=1 \
    PIP_NO_CACHE_DIR=1 \
    PYTHONPATH=/app \
    SMILARM_DOCKER=1 \
    STATE_DIR=/state \
    SMILARM_CASCADES=/opt/models

# mpv + alsa-utils: sound. libgl1/libgles2/libegl1/libglib2.0-0: MediaPipe and OpenCV. openssh-client: hooks that `ssh` to
# other devices. curl: model download and health checks. tzdata: timezone database.
RUN apt-get update \
 && apt-get install -y --no-install-recommends mpv alsa-utils libgl1 libgles2 libegl1 libglib2.0-0t64 openssh-client nano curl ca-certificates tzdata \
 && rm -rf /var/lib/apt/lists/*

# Not root. uid 1000 matches the default user on Raspberry Pi OS; audio/video give access to /dev/snd and /dev/video*.
RUN useradd --uid 1000 --create-home --shell /bin/bash --groups audio,video smilarm \
 && mkdir -p /state /opt/models \
 && chown smilarm:smilarm /state

WORKDIR /app
COPY requirements.txt .
RUN pip install -r requirements.txt

# The face model ships inside the image, so the first start works offline.
RUN curl -fsSL -o /opt/models/face_landmarker.task \
    https://storage.googleapis.com/mediapipe-models/face_landmarker/face_landmarker/float16/1/face_landmarker.task

# Cascade files for the OpenCV fallback detector (the OpenCV 5 wheels no longer include them).
RUN for f in haarcascade_frontalface_default haarcascade_smile; do \
      curl -fsSL -o /opt/models/$f.xml https://raw.githubusercontent.com/opencv/opencv/4.x/data/haarcascades/$f.xml; done

COPY smilarm ./smilarm
COPY web ./web
COPY docker ./docker
RUN chmod +x /app/docker/entrypoint.sh

LABEL app=smilarm org.opencontainers.image.title="Smilarm"

USER smilarm
VOLUME /state
EXPOSE 8080

HEALTHCHECK --interval=30s --timeout=6s --start-period=60s --retries=3 CMD ["python", "/app/docker/healthcheck.py"]
ENTRYPOINT ["/app/docker/entrypoint.sh"]
