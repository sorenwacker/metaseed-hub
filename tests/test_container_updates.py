"""Gates on the container update path.

Container images drift by calendar rather than by commit: upstream keeps
releasing, the host keeps whatever it pulled once, and a scanner reports a
rising advisory count against a deployment nobody touched. Three rules keep
that from recurring, and none of them is observable at runtime, so each is
asserted against the deployment files here.

Pins must name a full version, so the deployed version is a fact in the repo
rather than a function of when the host last pulled. Major bumps must never
arrive automatically, because PostgreSQL and MariaDB cannot open a data
directory written by a newer major. And something on the host must actually
apply a merged pin -- a bump that only lands in `main` changes nothing.
"""

import re
import subprocess
from pathlib import Path

import pytest
import yaml

_REPO = Path(__file__).resolve().parent.parent
_ROLE = _REPO / "ansible" / "roles" / "metaseed-hub"
_PROD_COMPOSE = _ROLE / "files" / "docker-compose.yml"
_DEPENDABOT = _REPO / ".github" / "dependabot.yml"
_UPDATE_SCRIPT = _ROLE / "templates" / "update-containers.sh.j2"

# A pin must name more than the major. Three components for most images, but
# PostgreSQL 10 and later version as MAJOR.MINOR only -- 16.15 is a complete
# version, not a truncated one -- so the third component is optional. An
# optional variant suffix (-alpine, -apache) may follow.
_FULL_PIN = re.compile(r"^[a-z0-9][a-z0-9./_-]*:\d+\.\d+(\.\d+)?(-[a-z0-9.]+)?$")

# Databases that cannot open a data directory written by a newer major.
_MAJOR_LOCKED = ["postgres", "mariadb"]


def _prod_images() -> dict[str, str]:
    compose = yaml.safe_load(_PROD_COMPOSE.read_text())
    return {name: svc["image"] for name, svc in compose["services"].items()}


def test_production_compose_is_tracked_by_git() -> None:
    """A `docker-compose.yml` ignore rule predates this file and matches at any
    depth. Untracked, the pins exist only on one laptop: Dependabot cannot read
    them, CI cannot check them, and ansible cannot ship them."""
    tracked = subprocess.run(
        ["git", "ls-files", _PROD_COMPOSE.relative_to(_REPO).as_posix()],
        capture_output=True,
        text=True,
        check=False,
        cwd=_REPO,
    )
    assert tracked.stdout.strip(), "the production compose file is not tracked by git"


def test_production_compose_is_a_static_file() -> None:
    """Dependabot parses `docker-compose.yml`, not a jinja template. Kept as a
    template, the production pins are invisible to it and never get bumped."""
    assert _PROD_COMPOSE.exists(), "production compose must be a plain file Dependabot can read"
    assert not (_ROLE / "templates" / "docker-compose.yml.j2").exists(), (
        "the jinja copy still exists; two compose files will diverge"
    )


def test_production_compose_holds_no_credentials() -> None:
    """It is world-readable in the repo now, so secrets must come from the
    host's .env instead of being interpolated into the file."""
    text = _PROD_COMPOSE.read_text()
    assert "{{" not in text, "jinja interpolation left in a static file"
    for line in text.splitlines():
        if "PASSWORD" in line:
            assert "${" in line, f"password is not read from the environment: {line.strip()}"


@pytest.mark.parametrize("service", ["postgres", "redis", "matomo-db", "matomo"])
def test_production_image_names_a_full_version(service: str) -> None:
    image = _prod_images()[service]
    assert _FULL_PIN.match(image), (
        f"{service} is pinned as {image!r}; a floating tag makes the running "
        "version depend on when the host last pulled"
    )


def test_dependabot_watches_the_production_compose_file() -> None:
    config = yaml.safe_load(_DEPENDABOT.read_text())
    docker = [u for u in config["updates"] if u["package-ecosystem"] == "docker"]
    assert docker, "no docker ecosystem; image pins are never bumped"
    watched = {u["directory"].strip("/") for u in docker}
    expected = _PROD_COMPOSE.parent.relative_to(_REPO).as_posix()
    assert expected in watched, f"dependabot does not watch {expected}"


@pytest.mark.parametrize("image", _MAJOR_LOCKED)
def test_dependabot_never_bumps_a_database_major(image: str) -> None:
    """A major bump that auto-merged would leave the container exiting on an
    incompatible data directory, with the hub down until a restore."""
    config = yaml.safe_load(_DEPENDABOT.read_text())
    docker = [u for u in config["updates"] if u["package-ecosystem"] == "docker"]
    ignored = [rule for u in docker for rule in u.get("ignore", [])]
    match = [r for r in ignored if r["dependency-name"] == image]
    assert match, f"{image} major updates are not ignored"
    assert "version-update:semver-major" in match[0]["update-types"]


def test_the_timer_is_installed_and_enabled() -> None:
    """Installing the units without enabling the timer applies nothing, which
    is invisible until a scanner reports the drift months later."""
    tasks = yaml.safe_load((_ROLE / "tasks" / "main.yml").read_text())
    installed = {
        task["ansible.builtin.template"]["src"]
        for task in tasks
        if "ansible.builtin.template" in task
    }
    assert "metaseed-containers.service.j2" in installed
    assert "metaseed-containers.timer.j2" in installed

    enabled = [
        task["ansible.builtin.systemd"]
        for task in tasks
        if "ansible.builtin.systemd" in task
        and "metaseed-containers.timer" in str(task["ansible.builtin.systemd"].get("name", ""))
    ]
    assert enabled, "the container timer is never enabled"
    assert enabled[0]["enabled"] is True, "the timer is installed but not enabled"
    assert enabled[0]["state"] == "started"


def test_the_update_runs_in_a_quiet_window_and_not_on_boot() -> None:
    timer = (_ROLE / "templates" / "metaseed-containers.timer.j2").read_text()
    assert "OnCalendar=" in timer, "the timer has no fixed window"
    assert "OnBootSec=" not in timer, "recreating the database on every boot is not the intent"


def test_the_update_refuses_without_a_recent_backup() -> None:
    """The recreate restarts PostgreSQL unattended; without a fresh dump there
    is nothing to fall back on if it fails to come back."""
    script = _UPDATE_SCRIPT.read_text()
    assert "backup" in script.lower(), "no backup freshness check before the recreate"


def test_the_update_health_checks_afterwards() -> None:
    """A failed update must be loud in the journal, not a quiet week."""
    script = _UPDATE_SCRIPT.read_text()
    assert "/api/health" in script, "the update never confirms the hub came back"


def test_the_host_uses_compose_v2() -> None:
    """Compose 1.29.2 stops the old container and then fails to recreate it
    against the installed engine, taking the service down and leaving it down."""
    tasks = (_ROLE / "tasks" / "main.yml").read_text()
    assert "docker-compose-v2" in tasks, "the v2 plugin is never installed"
    script = _UPDATE_SCRIPT.read_text()
    assert "docker-compose " not in script, "the update script still calls compose v1"
    assert "docker compose " in script


def test_drift_is_reported() -> None:
    """Pins in the repo prove nothing about what is running; the comparison has
    to exist, and has to be reachable without shell access."""
    module = _REPO / "src" / "metaseed_hub" / "container_drift.py"
    assert module.exists(), "nothing compares running images against the pins"
    dashboard = (
        _REPO / "src" / "metaseed_hub" / "ui" / "templates" / "admin" / "dashboard.html"
    ).read_text()
    assert "container_drift" in dashboard, "drift is not visible on the admin dashboard"
