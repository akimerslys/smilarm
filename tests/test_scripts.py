"""install.sh, uninstall.sh and the `smilarm` command, run for real against a stub `docker` and a throw-away HOME."""
import os
import socket
import tarfile
from pathlib import Path

import pytest

from conftest import ROOT, copy_clone, needs_bash

pytestmark = needs_bash
INSTALL, UNINSTALL = ROOT / "install.sh", ROOT / "uninstall.sh"
POSIX = os.name != "nt"


def install(sb, *args, **env):
    return sb.run(INSTALL, "--plain", *args, **env)


def uninstall(sb, *args, **env):
    return sb.run(UNINSTALL, "--plain", *args, **env)


def cli(sb, *args, **env):
    return sb.run(sb.installed_cli(), *args, **env)


def env_file(sb) -> dict:
    out = {}
    for line in (sb.smilarm / "compose" / ".env").read_text().splitlines():
        k, _, v = line.partition("=")
        out[k] = v
    return out


def busy_port():
    s = socket.socket()
    s.bind(("127.0.0.1", 0))
    s.listen(1)
    return s, s.getsockname()[1]


# ======================================================================= docker install
def test_fresh_install_builds_from_the_clone_and_sets_up_the_home(sb):
    r, calls = install(sb, STUB_VOLUMES="")
    assert r.returncode == 0, r.stdout + r.stderr
    for step in ("Check Docker", "Get the image", "Start the container", "Wait until it is healthy"):
        assert step in r.stdout
    assert "35.6 MB downloaded in 1 file(s)" in r.stdout
    assert "no prebuilt image is published yet" in (sb.smilarm / "install.log").read_text()  # the details go to the install log
    assert any(c.startswith("build --progress=plain -t smilarm:local ") for c in calls) and not any(c.startswith("pull ") for c in calls)
    assert calls.index(next(c for c in calls if c.startswith("build "))) < calls.index("compose up -d --remove-orphans")
    h = sb.smilarm
    assert (h / "bin" / "smilarm").is_file() and (h / "lib" / "ui.sh").is_file() and (h / "lib" / "uninstall.sh").is_file()
    assert (h / "compose" / "docker-compose.yml").is_file() and (h / "share" / "SKILL.md").is_file() and (h / "mode").read_text().strip() == "docker"
    assert env_file(sb)["SMILARM_IMAGE"] == "smilarm:local" and env_file(sb)["COMPOSE_FILE"].startswith("docker-compose.yml")
    assert (sb.bindir / "smilarm").exists()
    assert "You may delete it" in r.stdout and "smilarm status" in r.stdout


def test_published_project_pulls_the_prebuilt_image(sb):
    r, calls = install(sb, SMILARM_REPO="acme/Smilarm", STUB_VOLUMES="")
    assert r.returncode == 0, r.stdout + r.stderr
    assert "pull ghcr.io/acme/smilarm:latest" in calls and not any(c.startswith("build ") for c in calls)  # the registry path is lower-cased
    assert env_file(sb)["SMILARM_IMAGE"] == "ghcr.io/acme/smilarm:latest"


def test_a_failed_pull_falls_back_to_building_and_build_flag_skips_the_pull(sb):
    r, calls = install(sb, "--build", SMILARM_REPO="acme/smilarm", STUB_VOLUMES="")
    assert r.returncode == 0 and not any(c.startswith("pull ") for c in calls) and env_file(sb)["SMILARM_IMAGE"] == "smilarm:local"
    r, calls = install(sb, SMILARM_REPO="acme/smilarm", STUB_PULL="fail", STUB_VOLUMES="")
    assert r.returncode == 0 and "could not pull" in (sb.smilarm / "install.log").read_text() and any(c.startswith("build ") for c in calls)
    assert env_file(sb)["SMILARM_IMAGE"] == "smilarm:local"


def test_the_clone_can_be_deleted_after_installing(sb):
    clone = copy_clone(sb.tmp / "clone")
    r, _ = sb.run(clone / "install.sh", "--plain", STUB_VOLUMES="")
    assert r.returncode == 0, r.stdout + r.stderr
    import shutil

    shutil.rmtree(clone)  # the user deletes the folder they cloned
    assert not clone.exists()
    for args in (("status",), ("version",), ("help",), ("url",), ("logs",), ("restart",), ("stop",), ("start",), ("clean",)):
        r, _ = cli(sb, *args)
        assert r.returncode == 0, (args, r.stdout, r.stderr)
    r, calls = cli(sb, "status")
    assert "docker (smilarm:local)" in r.stdout and "web UI:" in r.stdout
    r, _ = uninstall(sb, "--dry-run", "--docker")  # the repo's uninstall.sh is gone too: the installed copy does the job
    r, _ = sb.run(sb.smilarm / "lib" / "uninstall.sh", "--plain", "--dry-run")
    assert r.returncode == 0 and "Dry run finished" in r.stdout


def test_sound_file_only_with_sound_hardware_and_other_env_lines_survive(sb):
    snd = sb.tmp / "snd"
    snd.write_text("")  # stands in for /dev/snd
    (sb.smilarm / "compose").mkdir(parents=True)
    (sb.smilarm / "compose" / ".env").write_text("MY_SETTING=1\nCOMPOSE_FILE=old.yml\n")
    r, _ = install(sb, SMILARM_SND_DEVICE=snd.as_posix(), STUB_VOLUMES="")
    assert r.returncode == 0 and "No sound hardware" not in r.stdout
    assert env_file(sb)["COMPOSE_FILE"] == "docker-compose.yml:docker-compose.sound.yml" and env_file(sb)["MY_SETTING"] == "1"  # replaced, not duplicated
    r, _ = install(sb, SMILARM_SND_DEVICE=(sb.tmp / "missing").as_posix())
    assert r.returncode == 0 and "No sound hardware found" in r.stdout and "moOde" in r.stdout
    assert env_file(sb)["COMPOSE_FILE"] == "docker-compose.yml"  # the container is not asked for a missing device
    (sb.smilarm / "compose" / "docker-compose.local.yml").write_text("services: {}\n")  # the user's own additions
    r, _ = install(sb, SMILARM_SND_DEVICE=(sb.tmp / "missing").as_posix())
    assert env_file(sb)["COMPOSE_FILE"] == "docker-compose.yml:docker-compose.local.yml"


def test_first_install_on_a_busy_port_picks_a_free_one_but_updates_and_migrations_keep_theirs(sb):
    busy, base = busy_port()
    try:
        r, calls = install(sb, STUB_VOLUMES="", SMILARM_DEFAULT_PORT=str(base), STUB_PORT=str(base + 1))
        assert r.returncode == 0, r.stdout + r.stderr
        assert f"Port {base} is already in use" in r.stdout and f"up SMILARM_PORT={base + 1}" in calls and f":{base + 1}" in r.stdout.split("Open:")[1]
        r, calls = install(sb, STUB_VOLUMES="smilarm-state", SMILARM_DEFAULT_PORT=str(base))  # an update: the existing config keeps its port
        assert "already in use" not in r.stdout and "up SMILARM_PORT=" in calls
        r, _ = install(sb, STUB_VOLUMES="alarmplus-state", SMILARM_DEFAULT_PORT=str(base))  # migration: the old config brings its own port
        assert "already in use" not in r.stdout
    finally:
        busy.close()


def test_data_is_moved_over_from_an_old_alarm_plus_install(sb):
    r, calls = install(sb, STUB_VOLUMES="alarmplus-state", STUB_CONTAINERS="alarmplus")
    assert r.returncode == 0, r.stdout + r.stderr
    assert "Move your data from Alarm+" in r.stdout and "alarmplus-state to smilarm-state" in r.stdout
    assert "stop alarmplus" in calls and "rm alarmplus" in calls and "volume create smilarm-state" in calls
    copy = next(c for c in calls if c.startswith("run ") and "alarmplus-state:/from:ro" in c)
    assert "smilarm-state:/to" in copy and "cp -a /from/. /to/" in copy and "chown -R 1000:1000" in copy
    assert not any(c.startswith(("volume rm", "image rm")) for c in calls)  # the old volume is kept
    assert calls.index("volume create smilarm-state") < calls.index("compose up -d --remove-orphans")  # data first, then start


def test_no_migration_when_asked_or_when_the_new_volume_exists_or_nothing_old_is_there(sb):
    for extra, vols in ((("--no-migrate",), "alarmplus-state"), ((), "alarmplus-state smilarm-state"), ((), "")):
        r, calls = install(sb, *extra, STUB_VOLUMES=vols, STUB_CONTAINERS="")
        assert r.returncode == 0 and "Move your data" not in r.stdout and not any("volume create" in c for c in calls), (extra, vols)
    r, calls = install(sb, STUB_VOLUMES="alarmplus-state", STUB_CONTAINERS="", SMILARM_SKIP_MIGRATION="1")
    assert "Move your data" not in r.stdout


def test_a_failed_migration_never_starts_the_new_container(sb):
    r, calls = install(sb, STUB_VOLUMES="alarmplus-state", STUB_CONTAINERS="", STUB_RUN="fail")
    assert r.returncode == 1 and "FAILED" in r.stdout and "compose up -d --remove-orphans" not in calls


def test_no_start_installs_everything_but_does_not_start(sb):
    r, calls = install(sb, "--no-start", STUB_VOLUMES="")
    assert r.returncode == 0 and "smilarm start" in r.stdout and not any(c.startswith("compose up") for c in calls)
    assert (sb.smilarm / "bin" / "smilarm").is_file()


def test_install_failure_modes_are_reported(sb):
    r, calls = install(sb, STUB_DAEMON="down")
    assert r.returncode == 1 and "daemon cannot be reached" in r.stdout and not any(c.startswith(("build", "pull")) for c in calls)
    r, calls = install(sb, STUB_BUILD="fail", STUB_VOLUMES="")
    assert r.returncode == 1 and "FAILED" in r.stdout and not any(c.startswith("compose up") for c in calls)  # never starts a half-built image
    r, _ = install(sb, STUB_HEALTH="exited", STUB_VOLUMES="")
    assert r.returncode == 1 and "did not become healthy" in r.stdout


# ----------------------------------------------------------------------- cache cleaning
def test_install_never_cleans_by_itself_but_mentions_a_big_cache(sb):
    r, calls = install(sb, STUB_VOLUMES="")
    assert not any("prune" in c for c in calls)  # opt-in only
    assert "3.132GB of build cache" in r.stdout and "smilarm clean" in r.stdout
    for small in ("0B (0%)", "40.52MB (100%)", "499MB (90%)"):
        assert "build cache" not in install(sb, STUB_CACHE=small)[0].stdout, small  # no nagging for small amounts
    assert "600MB of build cache" in install(sb, STUB_CACHE="600MB (90%)")[0].stdout


def test_clean_cache_runs_after_the_install_and_touches_only_caches(sb):
    r, calls = install(sb, "--clean-cache", STUB_VOLUMES="")
    assert r.returncode == 0, r.stdout + r.stderr
    assert "Clean the Docker build cache" in r.stdout and "freed 3.132GB + 1.58GB" in r.stdout
    up = calls.index("compose up -d --remove-orphans")
    assert up < calls.index("builder prune -af") and up < calls.index("image prune -f --filter label=app=smilarm")  # never before the new container runs
    assert not any(w in " ".join(calls) for w in ("system prune", "volume prune", "volume rm", "--volumes", "container prune"))
    assert "build cache. Free it" not in r.stdout


def test_clean_only_installs_and_starts_nothing(sb):
    r, calls = install(sb, "--clean-only")
    assert r.returncode == 0 and "Docker caches cleaned" in r.stdout and "were not touched" in r.stdout
    assert "builder prune -af" in calls and not any(c.startswith(("build ", "pull ", "compose up", "inspect")) for c in calls)
    assert not (sb.smilarm / "bin").exists()  # not even the home is created
    assert install(sb, "--clean-cache", STUB_PRUNE="fail", STUB_VOLUMES="")[0].returncode == 1  # a failed prune is reported


# ----------------------------------------------------------------------- bootstrap (curl | bash)
def test_install_without_the_sources_next_to_it_explains_what_to_do(sb):
    lonely = sb.tmp / "lonely"
    lonely.mkdir()
    (lonely / "install.sh").write_text((ROOT / "install.sh").read_text(encoding="utf-8"), newline="\n")
    r, _ = sb.run(lonely / "install.sh", "--plain")  # SMILARM_REPO is the OWNER placeholder
    assert r.returncode == 1 and "needs the Smilarm sources" in r.stdout and "git clone" in r.stdout


def test_install_without_the_sources_downloads_them_when_a_source_url_is_set(sb):
    lonely = sb.tmp / "lonely"
    lonely.mkdir()
    (lonely / "install.sh").write_text((ROOT / "install.sh").read_text(encoding="utf-8"), newline="\n")
    clone = copy_clone(sb.tmp / "pack" / "smilarm-main")  # a GitHub-style tarball: one top-level folder
    tar = sb.tmp / "src.tar.gz"
    with tarfile.open(tar, "w:gz") as t:
        t.add(clone, arcname="smilarm-main")
    r, _ = sb.run(lonely / "install.sh", "--demo", "--plain", SMILARM_REPO="acme/smilarm", SMILARM_SOURCE_URL=tar.as_uri())
    assert r.returncode == 0, r.stdout + r.stderr
    assert "Downloading Smilarm" in r.stdout and "Smilarm is running" in r.stdout


# ======================================================================= the smilarm command
@pytest.fixture
def installed(sb):
    r, _ = install(sb, STUB_VOLUMES="")
    assert r.returncode == 0, r.stdout + r.stderr
    sb.log.write_text("")  # only the command's own calls from here on
    return sb


def test_command_controls_the_container(installed):
    sb = installed
    for verb, expected in (("start", "compose up -d --remove-orphans"), ("stop", "compose stop"), ("restart", "compose restart smilarm")):
        r, calls = cli(sb, verb)
        assert r.returncode == 0 and expected in calls, (verb, calls)
    r, calls = cli(sb, "logs")
    assert "logs --tail 100 smilarm" in calls
    r, calls = cli(sb, "logs", "-f")
    assert "logs -f --tail 100 smilarm" in calls


def test_command_status_help_version_and_unknown(installed):
    sb = installed
    r, _ = cli(sb, "status")
    assert "mode:     docker (smilarm:local)" in r.stdout and "home:" in r.stdout
    assert "smilarm update" in cli(sb, "help")[0].stdout and "uninstall" in cli(sb)[0].stdout  # no argument = help
    assert cli(sb, "version")[0].stdout.strip()
    r, _ = cli(sb, "frobnicate")
    assert r.returncode == 2 and "unknown command" in r.stderr


def test_update_pulls_a_prebuilt_image_and_recreates_the_container(sb):
    install(sb, SMILARM_REPO="acme/smilarm", STUB_VOLUMES="")
    sb.log.write_text("")
    r, calls = cli(sb, "update", SMILARM_REPO="acme/smilarm")
    assert r.returncode == 0, r.stdout + r.stderr
    assert calls.index("pull ghcr.io/acme/smilarm:latest") < calls.index("compose up -d --remove-orphans")
    assert "image prune -f --filter label=app=smilarm" in calls and "updated" in r.stdout
    assert not any(c.startswith("volume rm") for c in calls)  # your data is never touched
    r, _ = cli(sb, "update", SMILARM_REPO="acme/smilarm", STUB_HEALTH="exited")
    assert r.returncode == 1 and "did not become healthy" in r.stderr


def test_update_of_a_build_from_a_clone_needs_a_download_address_or_uses_it(installed):
    sb = installed  # built from the clone: SMILARM_IMAGE=smilarm:local
    r, calls = cli(sb, "update")
    assert r.returncode == 1 and "git pull && bash install.sh" in r.stderr and not any(c.startswith("build") for c in calls)
    clone = copy_clone(sb.tmp / "pack" / "smilarm-main")
    tar = sb.tmp / "src.tar.gz"
    with tarfile.open(tar, "w:gz") as t:
        t.add(clone, arcname="smilarm-main")
    r, calls = cli(sb, "update", SMILARM_REPO="acme/smilarm", SMILARM_SOURCE_URL=tar.as_uri())
    assert r.returncode == 0, r.stdout + r.stderr
    assert any(c.startswith("build --progress=plain -t smilarm:local ") for c in calls) and "compose up -d --remove-orphans" in calls


def test_backup_and_restore(installed):
    sb = installed
    out = sb.tmp / "b" / "mine.tar.gz"
    out.parent.mkdir()
    r, calls = cli(sb, "backup", str(out))
    assert r.returncode == 0 and "backup saved" in r.stdout
    run = next(c for c in calls if c.startswith("run "))
    assert "smilarm-state:/state:ro" in run and "--entrypoint tar smilarm:local czf /backup/mine.tar.gz -C /state ." in run
    out.write_bytes(b"x")
    sb.log.write_text("")
    r, calls = cli(sb, "restore", str(out))
    assert r.returncode == 0 and "restored" in r.stdout
    assert calls.index("compose stop") < next(i for i, c in enumerate(calls) if c.startswith("run ")) < calls.index("compose up -d")  # stop, replace, start
    assert cli(sb, "restore", str(sb.tmp / "nope.tar.gz"))[0].returncode == 1
    assert cli(sb, "backup", str(out), STUB_RUN="fail")[0].returncode == 1


def test_command_cleans_caches_and_installs_the_openclaw_skill(installed):
    sb = installed
    r, calls = cli(sb, "clean")
    assert "builder prune -af" in calls and "image prune -f --filter label=app=smilarm" in calls
    ws = sb.tmp / "ws"
    r, _ = cli(sb, "openclaw", OPENCLAW_WORKSPACE=ws.as_posix())
    skill = ws / "skills" / "smilarm" / "SKILL.md"
    assert r.returncode == 0 and skill.is_file() and skill.read_text(encoding="utf-8").startswith("---\nname: smilarm")


def test_command_uninstall_hands_over_to_the_uninstaller(installed):
    r, calls = cli(installed, "uninstall", "--plain", "--dry-run")
    assert r.returncode == 0 and "Dry run finished" in r.stdout and not any(c.startswith(("rm", "volume rm", "image rm")) for c in calls)


# ======================================================================= uninstall
def test_uninstall_keeps_data_by_default_and_removes_the_program(installed):
    sb = installed
    r, calls = uninstall(sb, "--yes")
    assert r.returncode == 0, r.stdout + r.stderr
    assert "rm -f smilarm" in calls and "image rm -f smilarm:local" in calls
    assert not any(c.startswith(("volume rm", "run ")) for c in calls) and "Kept: your config and alarms" in r.stdout
    assert not (sb.smilarm / "bin").exists() and not (sb.smilarm / "compose" / ".env").exists()  # the command and its files are gone
    assert not sb.smilarm.exists() or not any(sb.smilarm.rglob("*.yml"))


def test_uninstall_removes_the_registry_image_too_and_keeps_the_users_local_compose_file(sb):
    install(sb, SMILARM_REPO="acme/smilarm", STUB_VOLUMES="")
    local = sb.smilarm / "compose" / "docker-compose.local.yml"
    local.write_text("services: {}\n")
    r, calls = uninstall(sb, "--yes", STUB_IMAGES="smilarm:local ghcr.io/acme/smilarm:latest")
    assert "image rm -f ghcr.io/acme/smilarm:latest" in calls and local.exists() and "kept your" in r.stdout


def test_uninstall_purge_backs_up_before_deleting(installed):
    sb = installed
    r, calls = uninstall(sb, "--yes", "--purge")
    assert r.returncode == 0, r.stdout + r.stderr
    i_run = next(i for i, c in enumerate(calls) if c.startswith("run "))
    assert i_run < calls.index("rm -f smilarm") < calls.index("volume rm smilarm-state")  # backup first, data volume last
    assert "smilarm-backup-" in calls[i_run] and "--entrypoint tar" in calls[i_run]


def test_uninstall_never_deletes_data_after_a_failed_backup(installed):
    r, calls = uninstall(installed, "--yes", "--purge", STUB_RUN="fail")
    assert r.returncode == 1 and "Stopped because of an error" in r.stdout
    assert not any(c.startswith(("volume rm", "rm -f", "image rm")) for c in calls)
    assert (installed.smilarm / "bin" / "smilarm").is_file()  # and the command is still there to retry


def test_uninstall_dry_run_and_no_backup_and_nothing_installed(installed):
    sb = installed
    r, calls = uninstall(sb, "--dry-run", "--purge")
    assert r.returncode == 0 and "Dry run finished" in r.stdout and "(dry run, not done)" in r.stdout
    assert not any(c.startswith(("run ", "volume rm", "rm -f", "image rm")) for c in calls) and (sb.smilarm / "bin" / "smilarm").is_file()
    r, calls = uninstall(sb, "--yes", "--purge", "--no-backup")
    assert r.returncode == 0 and not any(c.startswith("run ") for c in calls) and "volume rm smilarm-state" in calls
    r, _ = uninstall(sb, "--yes", STUB_CONTAINERS="", STUB_IMAGES="", STUB_VOLUMES="")
    assert r.returncode == 0 and "No Smilarm installation found" in r.stdout


def test_uninstall_without_any_install_says_so_even_without_a_home(sb):
    r, _ = uninstall(sb, "--yes", STUB_CONTAINERS="", STUB_IMAGES="", STUB_VOLUMES="")
    assert r.returncode == 0 and "No Smilarm installation found" in r.stdout
    lonely = sb.tmp / "lonely"
    lonely.mkdir()
    (lonely / "uninstall.sh").write_text(UNINSTALL.read_text(encoding="utf-8"), newline="\n")
    r, _ = sb.run(lonely / "uninstall.sh", "--plain")
    assert r.returncode == 0 and "Nothing to uninstall" in r.stdout


def test_uninstall_refuses_to_guess_when_both_installs_exist(installed):
    sb = installed
    unit = sb.tmp / "smilarm.service"
    unit.write_text("[Service]\n")  # a native install exists too
    r, calls = uninstall(sb, "--yes", SMILARM_UNIT=unit.as_posix())
    assert r.returncode == 2 and "BOTH" in r.stdout and "--docker" in r.stdout
    assert not any(c.startswith(("rm -f", "volume rm", "image rm")) for c in calls) and unit.exists()  # nothing was removed
    r, calls = uninstall(sb, "--docker", "--yes", SMILARM_UNIT=unit.as_posix())  # an explicit choice: only Docker is touched
    assert r.returncode == 0 and "rm -f smilarm" in calls and unit.exists()


def test_uninstall_mentions_a_leftover_old_alarm_plus_volume(installed):
    r, _ = uninstall(installed, "--yes", STUB_VOLUMES="smilarm-state alarmplus-state")
    assert "docker volume rm alarmplus-state" in r.stdout


def test_uninstall_removes_the_openclaw_skill_only_when_asked(installed):
    sb = installed
    ws = sb.tmp / "ws"
    skill = ws / "skills" / "smilarm"
    skill.mkdir(parents=True)
    (skill / "SKILL.md").write_text("x")
    uninstall(sb, "--yes", OPENCLAW_WORKSPACE=ws.as_posix())
    assert skill.exists()  # --yes alone never touches it
    install(sb, STUB_VOLUMES="")
    uninstall(sb, "--yes", "--openclaw", OPENCLAW_WORKSPACE=ws.as_posix())
    assert not skill.exists() and ws.exists()


def test_uninstall_rejects_unknown_options(sb):
    r, _ = uninstall(sb, "--nuke")
    assert r.returncode == 2 and "unknown option" in r.stdout


# ======================================================================= native install, uninstall and command (no systemd needed)
def fake_native_home(sb):
    """What a native install leaves behind, built with the real install_home()."""
    import subprocess

    from conftest import BASH, decoded

    p = decoded(subprocess.run([BASH, "-c", f'. "{(ROOT / "scripts/lib/common.sh").as_posix()}"; install_home "{ROOT.as_posix()}" native'],
                               capture_output=True, env=sb.env, timeout=60))
    assert p.returncode == 0, p.stderr
    h = sb.smilarm
    for d in ("venv/bin", "app/smilarm", "data"):
        (h / d).mkdir(parents=True, exist_ok=True)
    (h / "venv" / "bin" / "python").write_text("x")
    (h / "data" / "alarms.json").write_text('[{"id": "a1"}, {"id": "b2"}]')
    (h / "config.toml").write_text("[server]\ntoken = \"secret\"\n")
    return h


def test_native_uninstall_keeps_config_and_alarms_unless_purged(sb):
    h = fake_native_home(sb)
    unit = sb.tmp / "unit"
    unit.write_text("[Service]\n")
    r, calls = uninstall(sb, "--yes", SMILARM_UNIT=unit.as_posix(), STUB_CONTAINERS="", STUB_VOLUMES="", STUB_IMAGES="")
    assert r.returncode == 0, r.stdout + r.stderr
    assert "systemctl disable --now smilarm" in calls and not unit.exists()  # the service is stopped and its unit removed
    assert not (h / "venv").exists() and not (h / "app").exists() and not (h / "bin").exists()
    assert (h / "config.toml").exists() and (h / "data" / "alarms.json").exists() and "Kept: your config and alarms" in r.stdout


def test_native_uninstall_purge_writes_an_owner_only_backup_first(sb):
    h = fake_native_home(sb)
    r, _ = uninstall(sb, "--yes", "--purge", STUB_CONTAINERS="", STUB_VOLUMES="", STUB_IMAGES="")
    assert r.returncode == 0, r.stdout + r.stderr
    backups = list(sb.home.glob("smilarm-backup-*.tar.gz"))
    assert len(backups) == 1
    with tarfile.open(backups[0]) as t:
        assert "config.toml" in t.getnames() and "data/alarms.json" in t.getnames()
        assert b"b2" in t.extractfile("data/alarms.json").read()
    if POSIX:
        assert backups[0].stat().st_mode & 0o077 == 0, "the backup holds the access token: owner-only"
    assert not (h / "config.toml").exists() and not (h / "data").exists()


def test_native_command_backup_restore_and_controls(sb):
    h = fake_native_home(sb)
    r, calls = cli(sb, "restart")
    assert r.returncode == 0 and "systemctl restart smilarm" in calls
    assert "native" in cli(sb, "status")[0].stdout
    out = sb.tmp / "n.tar.gz"
    r, _ = cli(sb, "backup", str(out))
    assert r.returncode == 0 and out.exists()
    (h / "config.toml").write_text("changed")
    (h / "data" / "alarms.json").unlink()
    r, calls = cli(sb, "restore", str(out))
    assert r.returncode == 0, r.stdout + r.stderr
    assert 'token = "secret"' in (h / "config.toml").read_text() and (h / "data" / "alarms.json").exists()
    assert "systemctl stop smilarm" in calls and "systemctl start smilarm" in calls
    if POSIX:
        assert out.stat().st_mode & 0o077 == 0


def test_native_update_without_a_download_address_points_at_the_clone(sb):
    fake_native_home(sb)
    r, _ = cli(sb, "update")
    assert r.returncode == 1 and "git pull && bash install.sh --native" in r.stderr
