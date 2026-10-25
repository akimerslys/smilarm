"""The documentation must not point at things that don't exist: links, anchors, scripts, settings."""
import re
import shutil
import subprocess
from pathlib import Path

import pytest

from smilarm import config

ROOT = Path(__file__).resolve().parent.parent
DOCS = [ROOT / "README.md", *sorted((ROOT / "docs").glob("*.md")), ROOT / "openclaw" / "skills" / "smilarm" / "SKILL.md"]


def slug(heading: str) -> str:  # GitHub's anchor rule, close enough for our headings
    h = re.sub(r"[`*_]", "", heading.strip().lower())
    return re.sub(r"\s+", "-", re.sub(r"[^\w\s-]", "", h))


def anchors(path: Path) -> set[str]:
    out = set()
    for line in path.read_text(encoding="utf-8").splitlines():
        m = re.match(r"^#{1,6}\s+(.*)", line)
        if m:
            out.add(slug(m.group(1)))
    return out


def test_the_docs_are_the_five_consolidated_pages():
    assert sorted(p.name for p in (ROOT / "docs").glob("*.md")) == ["hardware.md", "install.md", "integrations.md", "moode.md", "troubleshooting.md"]


@pytest.mark.parametrize("doc", DOCS, ids=lambda p: p.relative_to(ROOT).as_posix())
def test_links_and_anchors_resolve(doc):
    text = doc.read_text(encoding="utf-8")
    for target in re.findall(r"\]\(([^)\s]+)\)", text):
        if target.startswith(("http://", "https://", "mailto:")):
            continue
        path, _, frag = target.partition("#")
        dest = (doc.parent / path).resolve() if path else doc
        assert dest.exists(), f"{doc.name}: broken link {target}"
        if frag and dest.suffix == ".md":
            assert frag in anchors(dest), f"{doc.name}: no heading for #{frag} in {dest.name}"


@pytest.mark.parametrize("doc", DOCS, ids=lambda p: p.relative_to(ROOT).as_posix())
def test_scripts_named_in_docs_exist(doc):
    text = doc.read_text(encoding="utf-8")
    for script in re.findall(r"\bbash ((?:[\w./-]+/)?[\w-]+\.sh)\b", text):
        assert (ROOT / script).exists(), f"{doc.name}: `bash {script}` does not exist"


@pytest.mark.parametrize("doc", DOCS, ids=lambda p: p.relative_to(ROOT).as_posix())
def test_settings_named_in_docs_exist(doc):
    text = doc.read_text(encoding="utf-8")
    sections = {k for k, v in config.DEFAULTS.items() if isinstance(v, dict)}
    for sect, key in re.findall(r"`(\w+)\.(\w+)(?:[`|]|\s)", text):
        if sect in sections and key not in ("local",):  # `moode.local` is a hostname, not a setting
            assert key in config.DEFAULTS[sect], f"{doc.name}: `{sect}.{key}` is not a setting"


def test_every_script_has_valid_syntax_and_unix_line_endings():
    bash = shutil.which("bash")
    if not bash or "system32" in bash.lower():
        pytest.skip("needs a real bash")
    scripts = [*ROOT.glob("*.sh"), *(ROOT / "scripts").glob("*.sh"), *(ROOT / "scripts" / "lib").glob("*.sh"), ROOT / "scripts" / "smilarm",
               ROOT / "docker" / "entrypoint.sh"]
    assert len(scripts) >= 8
    for s in scripts:
        r = subprocess.run([bash, "-n", str(s)], capture_output=True, text=True)
        assert r.returncode == 0, f"{s.name}: {r.stderr}"
        assert "\r" not in s.read_text(encoding="utf-8"), f"{s.name} has CRLF line endings (breaks on Linux)"


def test_two_simple_scripts_and_the_docker_path_comes_first():
    top = sorted(p.name for p in ROOT.glob("*.sh"))
    assert top == ["install.sh", "uninstall.sh"]  # the only scripts a user ever runs
    readme = (ROOT / "README.md").read_text(encoding="utf-8")
    assert readme.index("bash install.sh ") < readme.index("bash uninstall.sh ") < readme.index("--native")  # quick start first, extras after
    assert "docs/install.md" in readme and "docs/hardware.md" in readme
    assert len(readme.splitlines()) < 100  # the README stays a quick start; the details live in docs/
