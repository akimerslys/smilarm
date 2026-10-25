"""Installer UI: parsers for what is downloaded and how much, and a full --demo run."""
import os
import shutil
import subprocess
from pathlib import Path

import pytest

ROOT = Path(__file__).resolve().parent.parent
BASH = shutil.which("bash")
pytestmark = pytest.mark.skipif(not BASH or "system32" in BASH.lower(), reason="needs a real bash (not the WSL stub)")


def decoded(r):
    r.stdout, r.stderr = r.stdout.decode("utf-8", "replace"), r.stderr.decode("utf-8", "replace")  # bytes in, so carriage returns survive (no newline translation)
    return r


def sh(code: str, **env):
    r = subprocess.run([BASH, "-c", f'. deploy/ui.sh; ui_init /dev/null 1; {code}'], cwd=ROOT, capture_output=True,
                       env={**os.environ, **env}, timeout=60)
    return decoded(r)


PIP_LOG = """Collecting aiohttp>=3.9
  Downloading aiohttp-3.9.5-cp311-aarch64.whl (1.2 MB)
Collecting mediapipe>=0.10.14
  Downloading https://files.pythonhosted.org/packages/ab/cd/mediapipe-0.10.21-cp311-aarch64.whl (35.6 MB)
  Downloading six-1.16.0-py2.py3-none-any.whl (11 kB)
Using cached numpy-2.0.0-cp311-aarch64.whl (14.0 MB)
"""
APT_LOG = """Get:1 http://deb.debian.org/debian bookworm/main arm64 libgles2 arm64 1.6.0-1 [18.0 kB]
Get:2 http://deb.debian.org/debian bookworm/main arm64 mpv arm64 0.35.1-3 [1,266 kB]
Fetched 1,284 kB in 1s (1,100 kB/s)
Unpacking mpv:arm64 (0.35.1-3) ...
"""


def with_log(tmp_path, text, code):
    f = tmp_path / "log.txt"
    f.write_text(text)
    return sh(code.replace("LOG", f'"{f.as_posix()}"')).stdout.strip()


def test_human_sizes_and_bar():
    assert sh("ui_hsize 3758596; echo; ui_hsize 18000; echo; ui_hsize 35600000; echo; ui_hsize 512").stdout.split("\n")[:4] == ["3.8 MB", "18 kB", "35.6 MB", "512 B"]
    assert sh("ui_bar 50 10").stdout == "#####-----"  # C locale -> ASCII bar
    assert sh("ui_bar 250 4").stdout == "####"  # never overflows


def test_pip_download_sizes_are_summed(tmp_path):
    assert with_log(tmp_path, PIP_LOG, "ui_sum_pip LOG") == "36811000 3"  # cached files are not downloads
    assert with_log(tmp_path, PIP_LOG, "hint_pip final LOG") == "36.8 MB downloaded in 3 file(s)"
    live = with_log(tmp_path, "Collecting x\n  Downloading https://h/p/mediapipe-0.1-cp311.whl (35.6 MB)\n", "hint_pip live LOG")
    assert live == "downloading mediapipe-0.1 (35.6 MB) · 35.6 MB in 1 file(s)"
    assert with_log(tmp_path, "Requirement already satisfied: x\n", "hint_pip final LOG") == "nothing to download (cached or already installed)"


def test_apt_progress_hints(tmp_path):
    assert with_log(tmp_path, APT_LOG, "ui_sum_apt LOG") == "1284000 2"
    assert with_log(tmp_path, APT_LOG, "hint_apt final LOG") == "2 package(s), 1.3 MB downloaded"
    assert with_log(tmp_path, APT_LOG, "APT_PLAN_N=4 hint_apt live LOG") == "unpacking mpv:arm64"
    first = "Get:2 http://d/debian bookworm/main arm64 mpv arm64 0.35.1-3 [1,266 kB]\n"
    assert with_log(tmp_path, first, "APT_PLAN_N=4 hint_apt live LOG") == "downloading mpv (1,266 kB) · 1 of 4, 1.3 MB so far"


def run_demo(*flags, **env):
    return decoded(subprocess.run([BASH, "deploy/install.sh", "--demo", *flags], cwd=ROOT, capture_output=True, timeout=120,
                                  env={**os.environ, "DEMO_DELAY": "0.01", **env}))


def test_demo_plain_run_lists_every_step_and_download():
    r = run_demo("--plain", "--no-edit")
    assert r.returncode == 0, r.stdout + r.stderr
    for step in ("System packages", "Python environment", "Python libraries", "Face-detection model", "Configuration", "System service", "Starting Alarm+"):
        assert step in r.stdout
    assert "Will download:" in r.stdout and "4 package(s), 2.0 MB" in r.stdout
    assert "66.0 MB downloaded in 3 file(s)" in r.stdout and "3.8 MB downloaded" in r.stdout
    assert "Alarm+ is running" in r.stdout and "\x1b" not in r.stdout  # plain: no escape codes


def test_demo_animated_run_shows_progress_bar_and_spinner():
    r = run_demo("--no-edit", DEMO_DELAY="0.3", UI_FORCE_ANIM="1", TERM="xterm", LANG="en_US.UTF-8", COLUMNS="100")
    assert r.returncode == 0, r.stdout + r.stderr
    assert "\r" in r.stdout  # redraws in place
    assert "%" in r.stdout and "ETA" in r.stdout and "█" in r.stdout  # real progress bar for the model download
    import re

    assert re.search(r"downloading (aiohttp|opencv_python_headless|mediapipe)-[0-9.]+ \([0-9.]+ MB\)", r.stdout)  # live: what, and how big
    assert "✔" in r.stdout


# ---------------- uninstall.sh ----------------
def fake_project(tmp_path):
    """A throw-away copy of the scripts plus the things an install leaves behind."""
    proj = tmp_path / "proj"
    shutil.copytree(ROOT / "deploy", proj / "deploy")
    (proj / ".venv" / "bin").mkdir(parents=True)
    (proj / ".venv" / "bin" / "python").write_text("x")
    (proj / "data").mkdir()
    (proj / "data" / "alarms.json").write_text('[{"id": "a1", "time": "07:00"}, {"id": "b2", "time": "08:00"}]')
    (proj / "config.toml").write_text("[smile]\nthreshold = 0.4\n")
    (proj / "README.md").write_text("project files stay")
    return proj


def uninstall(proj, *flags, home=None, **env):
    e = {**os.environ, "ALARMPLUS_UNIT": str(proj / "no-such.service"), "HOME": str(home or proj.parent / "home")}
    e.update(env)
    return decoded(subprocess.run([BASH, str(proj / "deploy" / "uninstall.sh"), "--plain", *flags], cwd=proj, capture_output=True, timeout=60, env=e))


def test_uninstall_dry_run_changes_nothing(tmp_path):
    proj = fake_project(tmp_path)
    r = uninstall(proj, "--dry-run", "--purge", "--yes")
    assert r.returncode == 0, r.stdout + r.stderr
    assert "Dry run finished" in r.stdout and "(dry run, not done)" in r.stdout and "2 alarm(s)" in r.stdout
    assert (proj / ".venv").exists() and (proj / "data" / "alarms.json").exists() and (proj / "config.toml").exists()


def test_uninstall_default_keeps_data_and_project_files(tmp_path):
    proj = fake_project(tmp_path)
    r = uninstall(proj, "--yes")
    assert r.returncode == 0, r.stdout + r.stderr
    assert not (proj / ".venv").exists()  # the environment goes
    assert (proj / "data" / "alarms.json").exists() and (proj / "config.toml").exists()  # your data stays
    assert (proj / "README.md").exists() and (proj / "deploy" / "uninstall.sh").exists()  # the folder is never deleted
    assert "Alarm+ is uninstalled" in r.stdout and "rm -rf" in r.stdout and "kept" in r.stdout


def test_uninstall_purge_backs_up_then_deletes(tmp_path):
    import tarfile

    proj = fake_project(tmp_path)
    home = tmp_path / "home"
    home.mkdir()
    r = uninstall(proj, "--yes", "--purge", home=home)
    assert r.returncode == 0, r.stdout + r.stderr
    assert not (proj / "data").exists() and not (proj / "config.toml").exists() and not (proj / ".venv").exists()
    backups = list(home.glob("alarmplus-backup-*.tar.gz"))
    assert len(backups) == 1
    with tarfile.open(backups[0]) as t:
        names = t.getnames()
        assert "data/alarms.json" in names and "config.toml" in names
        assert b"b2" in t.extractfile("data/alarms.json").read()  # the alarms really are in the backup


def test_uninstall_purge_no_backup_and_idempotent(tmp_path):
    proj = fake_project(tmp_path)
    home = tmp_path / "home"
    home.mkdir()
    assert uninstall(proj, "--yes", "--purge", "--no-backup", home=home).returncode == 0
    assert not list(home.glob("alarmplus-backup-*"))
    again = uninstall(proj, "--yes", "--purge", home=home)  # nothing left: must not fail
    assert again.returncode == 0 and "Nothing to remove" in again.stdout


def test_uninstall_openclaw_skill_only_when_asked(tmp_path):
    proj = fake_project(tmp_path)
    ws = tmp_path / "ws"
    skill = ws / "skills" / "alarm-plus"
    skill.mkdir(parents=True)
    (skill / "SKILL.md").write_text("x")
    uninstall(proj, "--yes", OPENCLAW_WORKSPACE=str(ws))
    assert skill.exists()  # --yes alone never touches the skill
    uninstall(proj, "--yes", "--openclaw", OPENCLAW_WORKSPACE=str(ws))
    assert not skill.exists() and ws.exists()


def test_uninstall_rejects_unknown_options(tmp_path):
    r = uninstall(fake_project(tmp_path), "--nuke")
    assert r.returncode == 2 and "unknown option" in r.stdout
