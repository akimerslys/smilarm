import subprocess
import sys
import tomllib

from alarmplus import cfgtools, config


def test_generated_config_contains_every_default_and_round_trips():
    text = cfgtools.render_defaults()
    assert tomllib.loads(text) == config.DEFAULTS  # exact: nothing missing, nothing altered
    assert "# ESP32 resolution preset" in text and "[hooks]" in text


def test_generated_config_passes_its_own_check(tmp_path):
    p = tmp_path / "c.toml"
    p.write_text(cfgtools.render_defaults(), encoding="utf-8")
    assert cfgtools.check(str(p)) == []


def test_check_reports_problems(tmp_path):
    p = tmp_path / "c.toml"
    p.write_text('[smile]\nthreshold = 7\nbogus = 1\n[nothing]\nx = 1\n[camera]\nrotate = 45\n')
    out = "\n".join(cfgtools.check(str(p)))
    assert "smile.threshold" in out and "unknown key: smile.bogus" in out
    assert "unknown section/key: nothing" in out and "camera.rotate" in out
    p.write_text("[smile\n")
    assert "syntax" in cfgtools.check(str(p))[0]
    assert "cannot read" in cfgtools.check(str(tmp_path / "missing.toml"))[0]


def run(*args):
    return subprocess.run([sys.executable, "-m", "alarmplus", *args], capture_output=True, text=True, timeout=60)


def test_cli_write_and_check_config(tmp_path):
    p = tmp_path / "config.toml"
    assert run("--write-config", str(p)).returncode == 0 and "[server]" in p.read_text()
    again = run("--write-config", str(p))
    assert again.returncode != 0 and "not overwriting" in again.stderr  # never clobbers user edits
    assert run("--check-config", str(p)).returncode == 0
    p.write_text("[smile]\nfps = 99\n")
    bad = run("--check-config", str(p))
    assert bad.returncode == 1 and "smile.fps" in bad.stdout


def test_config_example_lists_every_parameter_and_is_up_to_date():
    from pathlib import Path

    example = (Path(__file__).resolve().parent.parent / "config.example.toml").read_text(encoding="utf-8")
    # regenerate with:  python -m alarmplus --write-config config.example.toml --force
    assert example == cfgtools.render_defaults(), "config.example.toml is stale"
    parsed = tomllib.loads(example)
    for key, value in config.DEFAULTS.items():
        if isinstance(value, dict):
            assert set(parsed[key]) == set(value), f"[{key}] differs from the program's parameters"
        else:
            assert key in parsed
    assert cfgtools.check(str(Path(__file__).resolve().parent.parent / "config.example.toml")) == []


def test_every_parameter_has_a_comment():
    lines = cfgtools.render_defaults().split("\n")
    for i, line in enumerate(lines):
        if line and not line.startswith(("#", "[")) and "=" in line:
            assert lines[i - 1].startswith("# ") and len(lines[i - 1]) > 3, f"no description above: {line}"
