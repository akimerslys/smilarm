"""Shared fixtures: a sandbox for running install.sh / uninstall.sh / the `smilarm` command against a stub `docker`."""
import os
import shutil
import subprocess
from pathlib import Path

import pytest

ROOT = Path(__file__).resolve().parent.parent
BASH = shutil.which("bash")
needs_bash = pytest.mark.skipif(not BASH or "system32" in (BASH or "").lower(), reason="needs a real bash (not the WSL stub)")

# fake docker: logs every call to $STUB_LOG and answers just enough for the scripts. Behaviour is switched with STUB_* variables.
STUB = r'''#!/usr/bin/env bash
echo "$*" >> "$STUB_LOG"
has() { for w in $1; do [ "$w" = "$2" ] && return 0; done; return 1; }
case "$1" in
  info) [ "${STUB_DAEMON:-up}" = up ] ;;
  version) echo "27.3.1" ;;
  compose)
    case "$2" in
      version) echo "2.29.7" ;;
      up) echo "up SMILARM_PORT=${SMILARM_PORT:-}" >> "$STUB_LOG" ;;
      *) true ;;
    esac ;;
  pull) echo "latest: Pulling from stub"; [ "${STUB_PULL:-ok}" = ok ] ;;
  build) echo "#7 9.0 Collecting mediapipe"; echo "#7 10.0   Downloading mediapipe-0.10.21-cp313-linux_aarch64.whl (35.6 MB)"; echo "#9 DONE"; [ "${STUB_BUILD:-ok}" = ok ] ;;
  inspect) echo "${STUB_HEALTH:-healthy}" ;;
  image)
    case "$2" in
      inspect) has "${STUB_IMAGES-smilarm:local}" "$3" && echo "512000000" ;;
      rm) true ;;
      prune) echo "Total reclaimed space: 1.58GB" ;;
    esac ;;
  ps) for c in ${STUB_CONTAINERS-smilarm}; do echo "$c"; done ;;
  volume)
    case "$2" in
      ls) for v in ${STUB_VOLUMES-smilarm-state}; do echo "$v"; done ;;
      create | rm) true ;;
    esac ;;
  run) [ "${STUB_RUN:-ok}" = ok ] ;;
  stop | rm | logs | restart | start) true ;;
  exec) echo "${STUB_PORT:-8080}" ;;
  builder) [ "$2" = prune ] && { [ "${STUB_PRUNE:-ok}" = ok ] && echo "Total:  3.132GB"; [ "${STUB_PRUNE:-ok}" = ok ]; } ;;
  system) echo "Build Cache|${STUB_CACHE:-3.132GB (100%)}" ;;
esac
'''


def decoded(r):
    r.stdout, r.stderr = r.stdout.decode("utf-8", "replace"), r.stderr.decode("utf-8", "replace")  # bytes in: carriage returns survive
    return r


class Sandbox:
    """A throw-away HOME with a stub docker first in PATH. `run()` returns (process, docker calls)."""

    def __init__(self, tmp_path):
        self.tmp = tmp_path
        self.home = tmp_path / "home"
        self.smilarm = self.home / ".smilarm"
        self.bindir = self.home / ".local" / "bin"
        self.stubbin = tmp_path / "stubbin"
        self.log = tmp_path / "docker.log"
        self.home.mkdir()
        self.stubbin.mkdir()
        self.log.write_text("")
        (self.stubbin / "docker").write_text(STUB, newline="\n")
        (self.stubbin / "docker").chmod(0o755)  # without the exec bit bash skips the stub on Linux and runs the REAL docker
        # `sudo` resets PATH, so `sudo docker` would reach the REAL docker when the stub reports a dead daemon
        (self.stubbin / "sudo").write_text("#!/usr/bin/env bash" + chr(10) + '[ "$1" = "-v" ] && exit 0' + chr(10) + '[ "$1" = "-n" ] && shift' + chr(10) + 'exec "$@"' + chr(10), newline=chr(10))
        (self.stubbin / "sudo").chmod(0o755)
        for name, body in (("systemctl", 'echo "systemctl $*" >> "$STUB_LOG"; [ "$1" = is-active ] && echo active; exit 0' + chr(10)),
                           ("journalctl", 'echo "journalctl $*" >> "$STUB_LOG"; exit 0' + chr(10))):
            (self.stubbin / name).write_text("#!/usr/bin/env bash" + chr(10) + body, newline=chr(10))
            (self.stubbin / name).chmod(0o755)
        if os.name != "nt":
            assert shutil.which("docker", path=str(self.stubbin)) == str(self.stubbin / "docker")

    @property
    def env(self):
        return {**os.environ, "PATH": f"{self.stubbin}{os.pathsep}{os.environ['PATH']}", "STUB_LOG": str(self.log), "HOME": self.home.as_posix(),
                "SMILARM_HOME": self.smilarm.as_posix(), "SMILARM_BIN_DIR": self.bindir.as_posix(), "SMILARM_UNIT": (self.tmp / "no-such.service").as_posix(),
                "SMILARM_LEGACY_UNIT": (self.tmp / "no-such-legacy.service").as_posix(), "SMILARM_SND_DEVICE": (self.tmp / "no-snd").as_posix(),
                "SMILARM_DEFAULT_PORT": "18080", "DEMO_DELAY": "0.01", "LC_ALL": "C", "SMILARM_REPO": "OWNER/smilarm"}

    def calls(self):
        return [line for line in self.log.read_text().splitlines() if line]

    def run(self, script, *args, cwd=None, timeout=180, **extra):
        env = {**self.env, **extra}
        cmd = [BASH, str(script), *args]
        r = decoded(subprocess.run(cmd, cwd=cwd or self.tmp, capture_output=True, timeout=timeout, env=env))
        return r, self.calls()

    def installed_cli(self):
        return self.smilarm / "bin" / "smilarm"


@pytest.fixture
def sb(tmp_path):
    if not BASH:
        pytest.skip("needs bash")
    return Sandbox(tmp_path)


def copy_clone(dst: Path) -> Path:
    """A copy of the repository files an install needs (a stand-in for a user's clone that can then be deleted)."""
    keep = ["install.sh", "uninstall.sh", "scripts", "smilarm", "web", "openclaw", "docker", "docker-compose.yml", "docker-compose.sound.yml",
            "config.example.toml", "requirements.txt", "Dockerfile"]
    dst.mkdir(parents=True, exist_ok=True)
    for name in keep:
        src = ROOT / name
        if src.is_dir():
            shutil.copytree(src, dst / name, ignore=shutil.ignore_patterns("__pycache__", "*.pyc"))
        else:
            shutil.copy(src, dst / name)
    return dst
