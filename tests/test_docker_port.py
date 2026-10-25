"""Host networking shares the Pi's ports: a taken 8080 must not turn the first start into a crash loop."""
import socket

from alarmplus import config
from test_docker import free_port, needs_bash, run_entrypoint, stub  # noqa: F401  (stub is a fixture)


@needs_bash
def test_entrypoint_uses_alarmplus_port_for_the_first_config(tmp_path):
    r, state = run_entrypoint(tmp_path, ALARMPLUS_PORT="8123")
    assert r.returncode == 0, r.stdout + r.stderr
    cfg = config.load(str(state / "config.toml"))
    assert cfg.server.port == 8123 and cfg.moode.port == 6600  # only the web port changed
    assert "[general]" in (state / "config.toml").read_text()  # still the full defaults file


@needs_bash
def test_entrypoint_without_alarmplus_port_keeps_the_default(tmp_path):
    r, state = run_entrypoint(tmp_path, ALARMPLUS_PORT="")
    assert r.returncode == 0 and config.load(str(state / "config.toml")).server.port == 8080


@needs_bash
def test_install_picks_a_free_port_when_the_default_is_taken(stub):  # noqa: F811
    call, _ = stub
    busy = socket.socket()
    busy.bind(("127.0.0.1", 0))
    busy.listen(1)
    base = busy.getsockname()[1]
    try:
        r, calls = call("install.sh", STUB_VOLUME="no", ALARMPLUS_DEFAULT_PORT=str(base), STUB_PORT=str(base + 1))
    finally:
        busy.close()
    assert r.returncode == 0, r.stdout + r.stderr
    assert f"Port {base} is already in use" in r.stdout and f"port {base + 1}" in r.stdout
    assert f"up ALARMPLUS_PORT={base + 1}" in calls  # handed to the container for its first config
    assert f":{base + 1}" in r.stdout.split("Open:")[1]  # and printed in the URL


@needs_bash
def test_install_does_not_touch_the_port_on_updates_or_when_it_is_free(stub):  # noqa: F811
    call, _ = stub
    busy = socket.socket()
    busy.bind(("127.0.0.1", 0))
    busy.listen(1)
    base = busy.getsockname()[1]
    try:
        r, calls = call("install.sh", STUB_VOLUME="yes", ALARMPLUS_DEFAULT_PORT=str(base))  # existing volume = an update
    finally:
        busy.close()
    assert r.returncode == 0 and "already in use" not in r.stdout  # an existing volume keeps its own config and port
    assert "up ALARMPLUS_PORT=" in calls  # passed through empty: the container keeps the port it already has
    free = free_port()
    r, calls = call("install.sh", STUB_VOLUME="no", ALARMPLUS_DEFAULT_PORT=str(free))
    assert r.returncode == 0 and "already in use" not in r.stdout and "up ALARMPLUS_PORT=" in calls  # empty value: default kept


@needs_bash
def test_uninstall_refuses_to_guess_when_both_installs_exist(stub, tmp_path):  # noqa: F811
    call, calls_log = stub
    unit = tmp_path / "alarmplus.service"
    unit.write_text("[Service]\n")  # a native install exists too
    r, calls = call("uninstall.sh", "--yes", ALARMPLUS_UNIT=str(unit))
    assert r.returncode == 2 and "BOTH" in r.stdout and "--docker" in r.stdout
    assert not any(c.startswith(("compose down", "volume rm", "image rm")) for c in calls)  # nothing was removed
    assert unit.exists()
    r, calls = call("uninstall.sh", "--docker", "--yes", ALARMPLUS_UNIT=str(unit))  # explicit choice: only Docker is touched
    assert r.returncode == 0 and "compose down --remove-orphans" in calls and unit.exists()


@needs_bash
def test_install_adds_the_sound_file_only_when_the_device_has_sound(stub, tmp_path):  # noqa: F811
    call, _ = stub
    env = tmp_path / "compose.env"
    snd = tmp_path / "snd"
    snd.write_text("")  # stands in for /dev/snd
    r, _ = call("install.sh", ALARMPLUS_SND_DEVICE=str(snd))
    assert r.returncode == 0 and "No sound hardware" not in r.stdout
    assert env.read_text().strip().endswith("COMPOSE_FILE=docker-compose.yml:docker-compose.sound.yml")
    r, _ = call("install.sh", ALARMPLUS_SND_DEVICE=str(tmp_path / "missing"))
    assert r.returncode == 0 and "No sound hardware found" in r.stdout and "moOde" in r.stdout  # explains the remote-only mode
    assert env.read_text().strip() == "COMPOSE_FILE=docker-compose.yml"  # and the container is not asked for a missing device


@needs_bash
def test_install_keeps_other_env_lines_and_adds_the_users_local_file(stub, tmp_path):  # noqa: F811
    from test_docker import ROOT

    call, _ = stub
    env = tmp_path / "compose.env"
    env.write_text("MY_SETTING=1\nCOMPOSE_FILE=old.yml\n")
    local = ROOT / "docker-compose.local.yml"
    assert not local.exists()
    local.write_text("services: {}\n")
    try:
        r, _ = call("install.sh", ALARMPLUS_SND_DEVICE=str(tmp_path / "missing"))
    finally:
        local.unlink()
    assert r.returncode == 0
    lines = env.read_text().splitlines()
    assert "MY_SETTING=1" in lines and "COMPOSE_FILE=old.yml" not in lines  # replaced, not duplicated
    assert lines[-1] == "COMPOSE_FILE=docker-compose.yml:docker-compose.local.yml"


# ---------------- optional cache cleaning ----------------
@needs_bash
def test_install_never_cleans_by_itself_but_mentions_a_big_cache(stub):  # noqa: F811
    call, _ = stub
    r, calls = call("install.sh")
    assert r.returncode == 0
    assert not any("prune" in c for c in calls)  # opt-in only
    assert "3.132GB of build cache" in r.stdout and "--clean-only" in r.stdout  # but it says how to get the space back
    r, _ = call("install.sh", STUB_CACHE="0B (0%)")
    assert "build cache" not in r.stdout  # no nagging when there is nothing to free


@needs_bash
def test_clean_cache_runs_after_the_install_and_touches_only_caches(stub):  # noqa: F811
    call, _ = stub
    r, calls = call("install.sh", "--clean-cache")
    assert r.returncode == 0, r.stdout + r.stderr
    assert "[5/5] Clean the Docker build cache" in r.stdout and "freed 3.132GB + 1.58GB" in r.stdout
    up = calls.index("compose up -d --remove-orphans")
    assert up < calls.index("builder prune -af") and up < calls.index("image prune -f --filter label=app=alarmplus")  # never before the new container runs
    assert not any(w in " ".join(calls) for w in ("system prune", "volume prune", "volume rm", "--volumes", "container prune"))
    assert "build cache. Free it" not in r.stdout  # the tip is for people who did not clean


@needs_bash
def test_clean_only_builds_and_starts_nothing(stub):  # noqa: F811
    call, _ = stub
    r, calls = call("install.sh", "--clean-only")
    assert r.returncode == 0 and "Docker caches cleaned" in r.stdout and "were not touched" in r.stdout
    assert "builder prune -af" in calls
    assert not any(c.startswith(("compose --progress", "compose up", "inspect")) for c in calls)


@needs_bash
def test_a_failed_prune_is_reported(stub):  # noqa: F811
    call, _ = stub
    r, _ = call("install.sh", "--clean-cache", STUB_PRUNE="fail")
    assert r.returncode == 1 and "FAILED" in r.stdout
