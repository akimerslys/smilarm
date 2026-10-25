"""Web-UI settings are saved back into config.toml (edited in place) and applied live."""
import json

import pytest

from alarmplus import config, settings as st
from alarmplus.cfgtools import set_values


def test_save_edits_config_toml_in_place(tmp_path):
    f = tmp_path / "config.toml"
    f.write_text('# my notes\n[smile]\n# how easy\nthreshold = 0.5   # tuned at night\nfps = 6\n\n[camera]\nsource = "http://10.0.0.5:81/stream"\n')
    cfg = config.load(str(f))
    st.Settings(cfg, f).update({"smile.threshold": 0.65, "ring.max_minutes": 20})
    text = f.read_text()
    assert "# my notes" in text and "# how easy" in text and "threshold = 0.65   # tuned at night" in text
    assert 'source = "http://10.0.0.5:81/stream"' in text  # untouched lines stay
    assert "[ring]" in text and "max_minutes = 20.0" in text  # missing section is appended
    assert (tmp_path / "config.toml.bak").read_text().startswith("# my notes")
    assert config.load(str(f)).smile.threshold == 0.65 and cfg.smile.threshold == 0.65  # file and live agree


def test_save_creates_config_from_defaults_when_missing(tmp_path):
    f = tmp_path / "config.toml"
    cfg = config.load(str(f))
    st.Settings(cfg, f).update({"smile.fps": 9})
    assert config.load(str(f)).smile.fps == 9 and "[hooks]" in f.read_text()


def test_unwritable_config_changes_nothing(tmp_path):
    cfg = config.load(None)
    s = st.Settings(cfg, tmp_path)  # a directory: cannot be read or replaced as a file
    with pytest.raises(OSError):
        s.update({"smile.fps": 9})
    assert cfg.smile.fps == 6  # memory only changes after the file was saved


def test_invalid_update_does_not_touch_file(tmp_path):
    f = tmp_path / "config.toml"
    f.write_text("[smile]\nfps = 6\n")
    s = st.Settings(config.load(str(f)), f)
    with pytest.raises(ValueError):
        s.update({"smile.fps": 6.5, "smile.threshold": 0.7})
    assert f.read_text() == "[smile]\nfps = 6\n" and not (tmp_path / "config.toml.bak").exists()


def test_reload_picks_up_hand_edits_and_rejects_bad_values(tmp_path):
    f = tmp_path / "config.toml"
    cfg = config.load(str(f))
    s = st.Settings(cfg, f)
    s.update({"smile.fps": 7})
    f.write_text(f.read_text().replace("fps = 7", "fps = 9").replace("threshold = 0.5", "threshold = 7"))
    problems = s.reload()
    assert cfg.smile.fps == 9 and set(problems) == {"smile.threshold"} and cfg.smile.threshold == 0.5


def test_restore_defaults_skips_locked_keys(tmp_path):
    f = tmp_path / "config.toml"
    cfg = config.load(str(f))
    cfg._d["server"]["token"] = "t"
    s = st.Settings(cfg, f)
    s.update({"smile.fps": 12, "hooks.on_dismiss": "echo x"})
    s.restore_defaults()
    assert cfg.smile.fps == 6 and cfg.hooks.on_dismiss == ""
    cfg._d["server"]["token"] = ""  # now locked: a restore must not touch hooks
    cfg._d["hooks"]["on_dismiss"] = "echo kept"
    s.restore_defaults()
    assert cfg.hooks.on_dismiss == "echo kept"


def test_legacy_settings_json_is_migrated_once(tmp_path):
    f, old = tmp_path / "config.toml", tmp_path / "settings.json"
    old.write_text(json.dumps({"smile.threshold": 0.8, "smile.fps": 6}))
    cfg = config.load(str(f))
    st.Settings(cfg, f, old)
    assert not old.exists() and (tmp_path / "settings.json.migrated").exists()
    assert cfg.smile.threshold == 0.8 and config.load(str(f)).smile.threshold == 0.8


def test_set_values_edge_cases():
    text = '[audio]\nsound = "a # not a comment"  # real comment\nvolume_start = 15\n\n[moode]\nhost = "x"\n\n'
    out = set_values(text.replace("\n", "\r\n"), {"audio.sound": "b.mp3", "audio.device": "alsa/x", "audio.volume_start": 20})
    assert 'sound = "b.mp3"  # real comment' in out and "\r" not in out
    assert out.index("device") < out.index("[moode]")  # a new key lands inside its own section
    assert "volume_start = 20" in out
    out = set_values("[a]\nx = 1\n", {"ring.allow_manual_dismiss": True})
    assert "allow_manual_dismiss = true" in out and out.rstrip().endswith("true")
    assert "x = 1" in out  # unrelated sections survive
