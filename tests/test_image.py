"""The Docker image definition: checked statically, and the entrypoint and health check are run for real (Docker itself is not needed)."""
import fnmatch
import os
import re
import shutil
import socket
import subprocess
import sys
import time
import urllib.request
from pathlib import Path

import pytest

from conftest import BASH, ROOT, needs_bash


# ---------------- static checks of the image definition ----------------
def test_dockerfile_is_sane():
    text = (ROOT / "Dockerfile").read_text()
    assert re.search(r"^FROM python:3\.\d+-slim", text, re.M)
    assert "USER smilarm" in text  # never root
    assert re.search(r"^HEALTHCHECK .*healthcheck\.py", text, re.M)
    assert 'ENTRYPOINT ["/app/docker/entrypoint.sh"]' in text
    assert "SMILARM_DOCKER=1" in text and "STATE_DIR=/state" in text
    assert "face_landmarker.task" in text and "/opt/models" in text  # model baked in: first start works offline
    assert "haarcascade_smile" in text and "SMILARM_CASCADES=/opt/models" in text  # OpenCV 5 wheels ship no cascade files
    for pkg in ("mpv", "openssh-client", "libgles2", "tzdata"):
        assert pkg in text, pkg
    # every COPY source exists, so the build can't fail on a missing path
    for src in re.findall(r"^COPY (?:--\S+ )*(\S+) ", text, re.M):
        assert (ROOT / src).exists(), f"COPY source missing: {src}"


def dockerignored(path: str, patterns) -> bool:
    """.dockerignore semantics: a pattern is anchored at the context root, so `*.sh` hides root-level scripts but not docker/entrypoint.sh,
    while a folder pattern hides everything under it."""
    first = path.split("/")[0]
    for p in patterns:
        p = p.rstrip("/")
        if "/" in p:
            if fnmatch.fnmatch(path, p) or path.startswith(p + "/"):
                return True
        elif fnmatch.fnmatch(first, p):  # a root-level pattern: matches the first path component (a file, or a folder that hides its contents)
            return True
    return False


def test_dockerignore_does_not_hide_what_the_image_needs():
    patterns = [l.strip() for l in (ROOT / ".dockerignore").read_text().splitlines() if l.strip() and not l.startswith(("#", "!"))]
    for needed in ("smilarm", "web", "docker", "requirements.txt", "smilarm/app.py", "docker/entrypoint.sh", "docker/healthcheck.py", "web/index.html"):
        assert not dockerignored(needed, patterns), f".dockerignore hides {needed}"
    for secret in ("config.toml", "data/alarms.json", ".git/config", ".venv/bin/python", ".env", "tests/test_api.py", "firmware/smilarm_cam/secrets.h"):
        assert dockerignored(secret, patterns), f"{secret} would end up in the image"


def test_compose_file_is_the_documented_setup():
    text = (ROOT / "docker-compose.yml").read_text()
    assert re.search(r"^name: smilarm$", text, re.M)
    assert "restart: unless-stopped" in text
    assert "network_mode: host" in text  # so the ESP32 and moOde can reach the app on the real LAN address
    assert re.search(r"- smilarm-state:/state", text) and re.search(r"^  smilarm-state:\n    name: smilarm-state", text, re.M)
    assert "/dev/snd" not in text.split("services:")[1]  # sound hardware is optional: it lives in its own file
    sound = (ROOT / "docker-compose.sound.yml").read_text()
    assert "/dev/snd:/dev/snd" in sound and "- audio" in sound and "smilarm:" in sound  # same service, merged in by install.sh
    assert "image: ${SMILARM_IMAGE:-smilarm:local}" in text and "build:" not in text  # the installer supplies the image: nothing depends on a clone


def test_requirements_are_runtime_only_and_complete():
    reqs = (ROOT / "requirements.txt").read_text()
    for pkg in ("aiohttp", "opencv-contrib-python", "mediapipe", "numpy", "tzdata"):
        assert pkg in reqs, pkg
    assert "pytest" not in reqs and "pytest" in (ROOT / "requirements-dev.txt").read_text()


# ---------------- entrypoint ----------------
@needs_bash
def run_entrypoint(tmp_path, **env):
    state = tmp_path / "state"
    model = tmp_path / "model.task"
    model.write_bytes(b"MODEL")
    e = {**os.environ, "STATE_DIR": str(state), "MODEL_SRC": str(model), "PYTHONPATH": str(ROOT),
         "SMILARM_EXEC": "echo STARTED", **env}
    r = subprocess.run([BASH, str(ROOT / "docker" / "entrypoint.sh")], capture_output=True, text=True, env=e, timeout=60, cwd=tmp_path)
    return r, state


@needs_bash
def test_entrypoint_first_start_creates_config_and_model(tmp_path):
    r, state = run_entrypoint(tmp_path)
    assert r.returncode == 0, r.stdout + r.stderr
    cfg = (state / "config.toml").read_text()
    assert "[general]" in cfg and "[server]" in cfg and 'timezone = ""' in cfg  # every default, ready for the UI to edit
    assert (state / "data" / "face_landmarker.task").read_bytes() == b"MODEL"
    assert f"STARTED --config {state.as_posix()}/config.toml" in r.stdout.replace(chr(92), "/")  # the app is started on the volume's config


@needs_bash
def test_entrypoint_never_overwrites_user_data(tmp_path):
    state = tmp_path / "state"
    (state / "data").mkdir(parents=True)
    (state / "config.toml").write_text("[smile]\nthreshold = 0.31\n")
    (state / "data" / "face_landmarker.task").write_bytes(b"MINE")
    r, _ = run_entrypoint(tmp_path)
    assert r.returncode == 0, r.stdout + r.stderr
    assert (state / "config.toml").read_text() == "[smile]\nthreshold = 0.31\n"
    assert (state / "data" / "face_landmarker.task").read_bytes() == b"MINE"


@needs_bash
def test_entrypoint_survives_a_missing_model(tmp_path):
    r, state = run_entrypoint(tmp_path, MODEL_SRC=str(tmp_path / "nope"))
    assert r.returncode == 0 and "no face model" in r.stdout and not (state / "data" / "face_landmarker.task").exists()


# ---------------- healthcheck ----------------
def free_port():
    s = socket.socket()
    s.bind(("127.0.0.1", 0))
    p = s.getsockname()[1]
    s.close()
    return p


def health(state_dir):
    return subprocess.run([sys.executable, str(ROOT / "docker" / "healthcheck.py")], env={**os.environ, "STATE_DIR": str(state_dir)},
                          capture_output=True, timeout=30).returncode


def test_healthcheck_follows_the_server_state(tmp_path):
    port = free_port()
    (tmp_path / "config.toml").write_text(f'data_dir = "data"\n[server]\nhost = "127.0.0.1"\nport = {port}\ntoken = "t"\n')
    assert health(tmp_path) == 1  # nothing listening yet: unhealthy
    p = subprocess.Popen([sys.executable, "-m", "smilarm", "--config", str(tmp_path / "config.toml")], cwd=tmp_path,
                         env={**os.environ, "PYTHONPATH": str(ROOT)}, stdout=subprocess.DEVNULL, stderr=subprocess.DEVNULL)
    try:
        for _ in range(100):
            try:
                urllib.request.urlopen(f"http://127.0.0.1:{port}/", timeout=1).read()
                break
            except OSError:
                time.sleep(0.2)
        else:
            raise AssertionError("server did not start")
        assert health(tmp_path) == 0  # up; a token is set, and the 401 still counts as healthy
    finally:
        p.kill()
        p.wait()
    assert health(tmp_path) == 1


