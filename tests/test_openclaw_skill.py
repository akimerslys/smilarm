"""The OpenClaw skill must only document endpoints the app really has."""
import re
from pathlib import Path

from smilarm import config
from smilarm.app import build

SKILL = Path(__file__).resolve().parent.parent / "openclaw" / "skills" / "smilarm" / "SKILL.md"


def test_frontmatter_is_valid():
    text = SKILL.read_text(encoding="utf-8")
    head = text.split("---")[1]
    fields = dict(line.split(": ", 1) for line in head.strip().splitlines())
    assert fields["name"] == "smilarm" and len(fields["description"]) > 40


def test_every_documented_endpoint_exists(tmp_path):
    cfg = config.load(None)
    cfg._d["data_dir"] = str(tmp_path)
    app = build(cfg, str(tmp_path / "config.toml"))
    routes = {(r.method, r.resource.canonical) for r in app.router.routes()}
    text = SKILL.read_text(encoding="utf-8")
    found = 0
    for line in text.splitlines():
        for m in re.finditer(r"(?:-X (GET|POST|PUT|DELETE) +)?(?:\$BASE|\"\$BASE)(/api/[A-Za-z0-9_./<>-]+)", line):
            method, path = m.group(1) or "GET", re.sub(r"<id>", "{id}", m.group(2))
            assert (method, path) in routes, f"SKILL.md documents {method} {path}, which the app does not serve"
            found += 1
    assert found >= 14  # the skill really lists the API, and the regex really found it
