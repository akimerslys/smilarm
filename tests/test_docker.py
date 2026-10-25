"""Docker is the primary install. Docker itself is not needed here: the image files are checked statically,
the entrypoint/healthcheck are run for real, and the install/uninstall scripts run against a stub `docker`."""
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

ROOT = Path(__file__).resolve().parent.parent
BASH = shutil.which("bash")
needs_bash = pytest.mark.skipif(not BASH or "system32" in BASH.lower(), reason="needs a real bash (not the WSL stub)")


# ---------------- static checks of the image definition ----------------
def test_dockerfile_is_sane():
    text = (ROOT / "Dockerfile").read_text()
    assert re.search(r"^FROM python:3\.\d+-slim", text, re.M)
    assert "USER alarmplus" in text  # never root
    assert re.search(r"^HEALTHCHECK .*healthcheck\.py", text, re.M)
    assert 'ENTRYPOINT ["/app/docker/entrypoint.sh"]' in text
    assert "ALARMPLUS_DOCKER=1" in text and "STATE_DIR=/state" in text
    assert "face_landmarker.task" in text and "/opt/models" in text  # model baked in: first start works offline
    assert "haarcascade_smile" in text and "ALARMPLUS_CASCADES=/opt/models" in text  # OpenCV 5 wheels ship no cascade files
    for pkg in ("mpv", "openssh-client", "libgles2", "tzdata"):
        assert pkg in text, pkg
    # every COPY source exists, so the build can't fail on a missing path
    for src in re.findall(r"^COPY (?:--\S+ )*(\S+) ", text, re.M):
        assert (ROOT / src).exists(), f"COPY source missing: {src}"


def test_dockerignore_does_not_hide_what_the_image_needs():
    patterns = [l.strip() for l in (ROOT / ".dockerignore").read_text().splitlines() if l.strip() and not l.startswith(("#", "!"))]
    for needed in ("alarmplus", "web", "docker", "requirements.txt", "alarmplus/app.py", "docker/entrypoint.sh", "web/index.html"):
        assert not any(fnmatch.fnmatch(needed, p) or needed.startswith(p.rstrip("/") + "/") for p in patterns), f".dockerignore hides {needed}"
    for secret in ("config.toml", "data", ".git", ".venv"):  # and keeps local state out of the image
        assert secret in patterns


def test_compose_file_is_the_documented_setup():
    text = (ROOT / "docker-compose.yml").read_text()
    assert re.search(r"^name: alarmplus$", text, re.M)
    assert "restart: unless-stopped" in text
    assert "network_mode: host" in text  # so the ESP32 and moOde can reach the app on the real LAN address
    assert re.search(r"- alarmplus-state:/state", text) and re.search(r"^  alarmplus-state:\n    name: alarmplus-state", text, re.M)
    assert "/dev/snd" not in text.split("services:")[1]  # sound hardware is optional: it lives in its own file
    sound = (ROOT / "docker-compose.sound.yml").read_text()
    assert "/dev/snd:/dev/snd" in sound and "- audio" in sound and "alarmplus:" in sound  # same service, merged in by install.sh
    assert "build: ." in text


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
         "ALARMPLUS_EXEC": "echo STARTED", **env}
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
    p = subprocess.Popen([sys.executable, "-m", "alarmplus", "--config", str(tmp_path / "config.toml")], cwd=tmp_path,
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


# ---------------- a stub `docker` for the scripts ----------------
STUB = r'''#!/usr/bin/env bash
# fake docker: logs every call to $STUB_LOG and answers just enough for the scripts
echo "$*" >> "$STUB_LOG"
case "$1" in
  info) [ "${STUB_DAEMON:-up}" = up ] ;;
  version) echo "27.3.1" ;;
  compose)
    case "$2" in
      version) echo "2.29.7" ;;
      --progress=plain) [ "$3" = build ] && { echo "#7 9.0 Collecting mediapipe"; echo "#7 10.0   Downloading mediapipe-0.10.21-cp313-linux_aarch64.whl (35.6 MB)"; echo "#9 DONE"; [ "${STUB_BUILD:-ok}" = ok ]; } ;;
      up) echo "up ALARMPLUS_PORT=${ALARMPLUS_PORT:-}" >> "$STUB_LOG" ;;
      down) true ;;
    esac ;;
  inspect) echo "${STUB_HEALTH:-healthy}" ;;
  image) case "$2" in inspect) [ "${STUB_IMAGE:-yes}" = yes ] && echo "512000000" ;; rm) true ;; prune) echo "Total reclaimed space: 1.58GB" ;; esac ;;
  builder) [ "$2" = prune ] && { [ "${STUB_PRUNE:-ok}" = ok ] && echo "Total:  3.132GB"; [ "${STUB_PRUNE:-ok}" = ok ]; } ;;
  system) echo "Build Cache|${STUB_CACHE:-3.132GB (100%)}" ;;
  ps) [ "${STUB_CONTAINER:-yes}" = yes ] && echo "alarmplus" ;;
  volume) case "$2" in ls) [ "${STUB_VOLUME:-yes}" = yes ] && echo "alarmplus-state" ;; rm) true ;; esac ;;
  run) [ "${STUB_BACKUP:-ok}" = ok ] ;;
  exec) echo "${STUB_PORT:-8080}" ;;
  rm|logs) true ;;
esac
'''


@pytest.fixture
def stub(tmp_path):
    if not BASH:
        pytest.skip("needs bash")
    d = tmp_path / "bin"
    d.mkdir()
    (d / "docker").write_text(STUB, newline="\n")
    (d / "docker").chmod(0o755)  # without the exec bit bash skips the stub on Linux and runs the REAL docker
    # `sudo` resets PATH, so `sudo docker` would reach the REAL docker when the stub reports a dead daemon (that once started a real build
    # on a machine with Docker). A pass-through sudo keeps every call inside the stub.
    (d / "sudo").write_text('#!/usr/bin/env bash\n[ "$1" = "-n" ] && shift\nexec "$@"\n', newline="\n")
    (d / "sudo").chmod(0o755)
    if os.name != "nt":
        assert shutil.which("docker", path=str(d)) == str(d / "docker")
    log = tmp_path / "docker.log"
    log.write_text("")
    env = {**os.environ, "PATH": f"{d}{os.pathsep}{os.environ['PATH']}", "STUB_LOG": str(log), "HOME": str(tmp_path / "home"), "DEMO_DELAY": "0.01",
           "ALARMPLUS_ENV_FILE": str(tmp_path / "compose.env")}  # never write the repo's own .env from a test
    (tmp_path / "home").mkdir()

    def call(script, *args, **extra):
        r = subprocess.run([BASH, str(ROOT / script), "--plain", *args], cwd=ROOT, capture_output=True, text=True, timeout=120, env={**env, **extra})
        return r, [l for l in log.read_text().splitlines() if l]

    return call, log


@needs_bash
def test_install_builds_starts_and_waits_for_health(stub):
    call, _ = stub
    r, calls = call("install.sh")
    assert r.returncode == 0, r.stdout + r.stderr
    for step in ("Check Docker", "Build the image", "Start the container", "Wait until it is healthy"):
        assert step in r.stdout
    assert "35.6 MB downloaded in 1 file(s)" in r.stdout  # what was downloaded during the build
    assert "Alarm+ is running" in r.stdout and ":8080" in r.stdout and "Settings" in r.stdout
    assert calls.index("compose --progress=plain build") < calls.index("compose up -d --remove-orphans")  # build, then start


@needs_bash
def test_install_no_start_only_builds(stub):
    call, _ = stub
    r, calls = call("install.sh", "--no-start")
    assert r.returncode == 0 and "is built" in r.stdout
    assert not any(c.startswith("compose up") for c in calls)


@needs_bash
def test_install_reports_a_dead_daemon_and_a_failed_build_and_an_unhealthy_container(stub):
    call, _ = stub
    r, calls = call("install.sh", STUB_DAEMON="down")
    assert r.returncode == 1 and "daemon cannot be reached" in r.stdout and not any("build" in c for c in calls)
    r, calls = call("install.sh", STUB_BUILD="fail")
    assert r.returncode == 1 and "FAILED" in r.stdout and not any(c.startswith("compose up") for c in calls)  # never starts a half-built image
    r, _ = call("install.sh", STUB_HEALTH="exited")
    assert r.returncode == 1 and "did not become healthy" in r.stdout


@needs_bash
def test_install_demo_and_native_dispatch():
    r = subprocess.run([BASH, str(ROOT / "install.sh"), "--demo", "--plain"], cwd=ROOT, capture_output=True, text=True, timeout=120,
                       env={**os.environ, "DEMO_DELAY": "0.01"})
    assert r.returncode == 0 and "installer (Docker)" in r.stdout and "Alarm+ is running" in r.stdout
    r = subprocess.run([BASH, str(ROOT / "install.sh"), "--native", "--demo", "--plain"], cwd=ROOT, capture_output=True, text=True, timeout=120,
                       env={**os.environ, "DEMO_DELAY": "0.01"})
    assert r.returncode == 0 and "System packages" in r.stdout and "Docker" not in r.stdout.split("Alarm+ installer")[1][:40]  # the optional non-Docker path


@needs_bash
def test_uninstall_keeps_data_by_default(stub):
    call, _ = stub
    r, calls = call("deploy/docker-uninstall.sh", "--yes")
    assert r.returncode == 0, r.stdout + r.stderr
    assert "compose down --remove-orphans" in calls and "image rm -f alarmplus:latest" in calls
    assert not any(c.startswith("volume rm") for c in calls) and not any(c.startswith("run ") for c in calls)
    assert "Kept: your data volume" in r.stdout


@needs_bash
def test_uninstall_purge_backs_up_before_deleting(stub):
    call, _ = stub
    r, calls = call("deploy/docker-uninstall.sh", "--yes", "--purge")
    assert r.returncode == 0, r.stdout + r.stderr
    i_run = next(i for i, c in enumerate(calls) if c.startswith("run "))
    i_down = calls.index("compose down --remove-orphans")
    i_vol = calls.index("volume rm alarmplus-state")
    assert i_run < i_down < i_vol  # backup first, data volume last
    assert "alarmplus-backup-" in calls[i_run] and "--entrypoint tar" in calls[i_run]


@needs_bash
def test_uninstall_never_deletes_data_after_a_failed_backup(stub):
    call, _ = stub
    r, calls = call("deploy/docker-uninstall.sh", "--yes", "--purge", STUB_BACKUP="fail")
    assert r.returncode == 1 and "Stopped because of an error" in r.stdout
    assert not any(c.startswith("volume rm") or c.startswith("compose down") for c in calls)


@needs_bash
def test_uninstall_dry_run_and_no_backup(stub):
    call, _ = stub
    r, calls = call("deploy/docker-uninstall.sh", "--dry-run", "--purge")
    assert r.returncode == 0 and "Dry run finished" in r.stdout
    assert not any(c.split()[0] in ("run", "volume", "rm") and c != "volume ls -q" for c in calls) and "compose down --remove-orphans" not in calls
    r, calls = call("deploy/docker-uninstall.sh", "--yes", "--purge", "--no-backup")
    assert r.returncode == 0 and not any(c.startswith("run ") for c in calls) and "volume rm alarmplus-state" in calls


@needs_bash
def test_uninstall_nothing_installed(stub):
    call, _ = stub
    r, _ = call("deploy/docker-uninstall.sh", "--yes", STUB_CONTAINER="no", STUB_IMAGE="no", STUB_VOLUME="no")
    assert r.returncode == 0 and "Nothing to remove" in r.stdout


@needs_bash
def test_root_uninstall_finds_the_docker_install(stub, tmp_path):
    call, _ = stub
    r, calls = call("uninstall.sh", "--docker", "--yes", "--dry-run", ALARMPLUS_UNIT=str(tmp_path / "none.service"))
    assert r.returncode == 0 and "uninstaller (Docker)" in r.stdout
    r, _ = call("uninstall.sh", "--yes", "--dry-run", STUB_CONTAINER="no", STUB_IMAGE="no", STUB_VOLUME="no", ALARMPLUS_UNIT=str(tmp_path / "none.service"))
    assert "uninstaller (Docker)" not in r.stdout  # auto-detect: no Docker install found, so the Docker path is skipped


def test_only_one_opencv_package_is_requested():
    """mediapipe depends on opencv-contrib-python; asking for a second OpenCV flavour installs two copies of cv2 (~150 MB, and
    whichever was installed last silently wins)."""
    names = [re.split(r"[<>=~! ;#\[]", l.strip())[0].lower() for l in (ROOT / "requirements.txt").read_text().splitlines()
             if l.strip() and not l.startswith("#")]
    assert [n for n in names if n.startswith("opencv")] == ["opencv-contrib-python"]
