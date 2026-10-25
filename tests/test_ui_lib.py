"""Installer UI library: what is downloading and how much, the progress bar, and full animated/plain --demo runs."""
import os
import re
import subprocess

import pytest

from conftest import BASH, ROOT, decoded, needs_bash

pytestmark = needs_bash


def sh(code: str, **env):
    r = subprocess.run([BASH, "-c", f". scripts/lib/ui.sh; ui_init /dev/null 1; {code}"], cwd=ROOT, capture_output=True,
                       env={**os.environ, "LC_ALL": "C", **env}, timeout=60)  # the C locale: ASCII bar, whatever the machine uses
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


def test_docker_build_output_is_understood(tmp_path):
    log = "#7 9.0 Collecting mediapipe\n#7 10.0   Downloading mediapipe-0.10.21-cp313-linux.whl (35.6 MB)\n#9 DONE\n"
    assert with_log(tmp_path, log, "hint_docker final LOG") == "35.6 MB downloaded in 1 file(s)"
    assert with_log(tmp_path, "#7 9.0 Collecting x\n#7 10.0   Downloading mediapipe-0.1-cp313.whl (35.6 MB)\n", "hint_docker live LOG").startswith("downloading mediapipe-0.1 (35.6 MB)")
    assert with_log(tmp_path, "#5 [2/9] RUN apt-get update\n", "hint_docker final LOG") == "built (cached layers reused, nothing new to download)"


def demo(tmp_path, *flags, **env):
    e = {**os.environ, "DEMO_DELAY": "0.01", "SMILARM_HOME": str(tmp_path / "h"), "HOME": str(tmp_path), "SMILARM_REPO": "OWNER/smilarm", **env}
    return decoded(subprocess.run([BASH, str(ROOT / "install.sh"), "--demo", *flags], cwd=tmp_path, capture_output=True, timeout=120, env=e))


def test_docker_demo_plain_run_lists_every_step(tmp_path):
    r = demo(tmp_path, "--plain")
    assert r.returncode == 0, r.stdout + r.stderr
    for step in ("Check Docker", "Get the image", "Start the container", "Wait until it is healthy"):
        assert step in r.stdout
    assert "106.7 MB downloaded in 4 file(s)" in r.stdout and "Smilarm is running" in r.stdout and "\x1b" not in r.stdout  # plain: no escape codes
    assert "smilarm status" in r.stdout and "You may delete it" in r.stdout


def test_native_demo_plain_run_lists_every_step_and_download(tmp_path):
    r = demo(tmp_path, "--native", "--plain", "--no-edit")
    assert r.returncode == 0, r.stdout + r.stderr
    for step in ("System packages", "App and Python environment", "Python libraries", "Face-detection model", "Configuration", "System service", "Starting Smilarm"):
        assert step in r.stdout
    assert "Will download:" in r.stdout and "4 package(s), 2.0 MB" in r.stdout
    assert "106.6 MB downloaded in 3 file(s)" in r.stdout and "3.8 MB downloaded" in r.stdout and "Smilarm is running" in r.stdout


def test_demo_animated_run_shows_progress_bar_and_spinner(tmp_path):
    r = demo(tmp_path, "--native", "--no-edit", DEMO_DELAY="0.6", UI_FORCE_ANIM="1", TERM="xterm", LANG="en_US.UTF-8", LC_ALL="en_US.UTF-8", COLUMNS="100")
    assert r.returncode == 0, r.stdout + r.stderr
    assert "\r" in r.stdout  # redraws in place
    assert "%" in r.stdout and "ETA" in r.stdout and "█" in r.stdout  # real progress bar for the model download
    assert re.search(r"downloading (aiohttp|opencv_contrib_python|mediapipe)-[0-9.]+ \([0-9.]+ MB\)", r.stdout)  # live: what, and how big
    assert "✔" in r.stdout


def test_native_demo_clean_cache_adds_a_last_step(tmp_path):
    r = demo(tmp_path, "--native", "--plain", "--no-edit", "--clean-cache")
    assert "[8/8] Clean the download caches" in r.stdout and "pip: files removed: 214, apt cache cleaned" in r.stdout
    assert "Clean the download caches" not in demo(tmp_path, "--native", "--plain", "--no-edit").stdout  # opt-in


def test_demo_with_cache_cleaning_and_migration_steps(tmp_path):
    r = demo(tmp_path, "--plain", "--clean-cache", SMILARM_DEMO_LEGACY="1")
    assert r.returncode == 0, r.stdout + r.stderr
    assert "[3/6] Move your data from Alarm+" in r.stdout and "[6/6] Clean the Docker build cache" in r.stdout and "freed 3.132GB + 1.58GB" in r.stdout


def test_unknown_option_is_rejected(tmp_path):
    r = demo(tmp_path, "--nuke")
    assert r.returncode == 2 and "unknown option" in r.stdout
