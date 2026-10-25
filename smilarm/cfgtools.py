"""config.toml helpers: generate a file with every default, and check a hand-edited one."""
import json
import re
import tomllib
from pathlib import Path

from . import config
from .settings import BY_KEY, validate

# hints for keys the web UI doesn't edit (the rest reuse the Settings help text)
EXTRA_HINTS = {
    "server.host": "interface to listen on (0.0.0.0 = whole network)",
    "server.port": "web UI / API port",
    "server.token": "set it if the network isn't trusted: every /api call then needs it (Bearer token or ?token=)",
    "camera.width": "detector frame width; 0 = take it from camera.quality",
    "smile.backend": "auto | mediapipe | haar",
    "smile.model": "MediaPipe face landmarker model (shipped in the Docker image, or fetched by the native installer)",
    "audio.sound": "file path or http(s) URL; empty = built-in tone",
    "data_dir": "alarms, the face model, logs and the built-in tone live here (relative to this file)",
}


def _toml(v) -> str:
    if isinstance(v, bool):
        return "true" if v else "false"
    if isinstance(v, (int, float)):
        return repr(v)
    return json.dumps(v, ensure_ascii=False)  # JSON strings are valid TOML basic strings


def render_defaults(port: int | None = None) -> str:
    """A complete config.toml with every default value, commented. `port` overrides server.port (first start on a busy 8080)."""
    text = _render_defaults()
    return set_values(text, {"server.port": port}) if port else text


def _render_defaults() -> str:
    d = config.DEFAULTS
    out = ["# Smilarm configuration (generated with all default values; edit what you need).",
           "# Most of these can also be changed live in the web UI > Settings, which saves them back into this file.", ""]
    for k, v in d.items():
        if not isinstance(v, dict):
            out += [f"# {EXTRA_HINTS.get(k, '')}".rstrip(), f"{k} = {_toml(v)}", ""]
    for sect, kv in d.items():
        if not isinstance(kv, dict):
            continue
        out.append(f"[{sect}]")
        for k, v in kv.items():
            hint = BY_KEY.get(f"{sect}.{k}", {}).get("help") or EXTRA_HINTS.get(f"{sect}.{k}", "")
            if hint:
                out.append(f"# {hint}")
            out.append(f"{k} = {_toml(v)}")
        out.append("")
    return "\n".join(out)


def check(path: str) -> list[str]:
    """Problems found in a config file (empty list = fine)."""
    try:
        user = tomllib.loads(Path(path).read_text(encoding="utf-8"))
    except OSError as e:
        return [f"cannot read {path}: {e}"]
    except tomllib.TOMLDecodeError as e:
        return [f"TOML syntax error: {e}"]
    problems = []
    for sect, kv in user.items():
        base = config.DEFAULTS.get(sect)
        if base is None:
            problems.append(f"unknown section/key: {sect}")
        elif isinstance(base, dict) != isinstance(kv, dict):
            problems.append(f"{sect}: expected {'a [section]' if isinstance(base, dict) else 'a single value'}")
        elif isinstance(kv, dict):
            for k, v in kv.items():
                if k not in base:
                    problems.append(f"unknown key: {sect}.{k}")
                elif f"{sect}.{k}" in BY_KEY:
                    try:
                        validate(f"{sect}.{k}", v)
                    except ValueError as e:
                        problems.append(f"{sect}.{k}: {e}")
    return problems


# ---------- in-place editing (keeps the user's comments, order and untouched lines) ----------
_HEADER = re.compile(r"^\s*\[([^\[\]]+)\]\s*(#.*)?$")


def _split_value(rest: str) -> tuple[str, str]:
    """Split the text after `key =` into (value, trailing inline comment incl. leading spaces)."""
    r = rest.rstrip()
    if r.startswith(('"', "'")):
        q, i = r[0], 1
        while i < len(r):
            if q == '"' and r[i] == chr(92):
                i += 2
                continue
            if r[i] == q:
                return r[: i + 1], r[i + 1 :]
            i += 1
        return r, ""
    cut = r.find("#")
    if cut < 0:
        return r, ""
    body = r[:cut].rstrip()
    return body, r[len(body) :]


def set_values(text: str, updates: dict) -> str:
    """Set `section.key = value` pairs in TOML text, editing lines in place.

    A key is replaced where it already is (inline comment kept); a missing key is appended to the end
    of its section; a missing section is appended to the file. The result is verified by parsing it.
    """
    lines = text.replace(chr(13) + chr(10), chr(10)).split(chr(10))
    if lines and lines[-1] == "":
        lines.pop()
    for dotted, value in updates.items():
        sect, key = dotted.split(".")
        start = end = None  # body range of [sect]
        for i, line in enumerate(lines):
            m = _HEADER.match(line)
            if m:
                if start is not None and end is None:
                    end = i
                if m.group(1).strip() == sect and start is None:
                    start = i + 1
        if start is not None and end is None:
            end = len(lines)
        new_val = _toml(value)
        if start is None:
            if lines and lines[-1].strip():
                lines.append("")
            lines += [f"[{sect}]", f"{key} = {new_val}"]
            continue
        pat = re.compile(r"^(\s*" + re.escape(key) + r"\s*=\s*)(.*)$")
        for i in range(start, end):
            m = pat.match(lines[i])
            if m:
                _, tail = _split_value(m.group(2))
                lines[i] = f"{key} = {new_val}{tail}"
                break
        else:
            at = end
            while at > start and not lines[at - 1].strip():
                at -= 1
            lines.insert(at, f"{key} = {new_val}")
    out = chr(10).join(lines) + chr(10)
    parsed = tomllib.loads(out)  # raises if we produced invalid TOML
    for dotted, value in updates.items():
        sect, key = dotted.split(".")
        if parsed.get(sect, {}).get(key) != value:
            raise ValueError(f"internal error: could not set {dotted}")
    return out
