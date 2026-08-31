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
_RENOVATE = _REPO / "renovate.json"
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


def _docker_rules() -> list[dict]:
    import json

    config = json.loads(_RENOVATE.read_text())
    return [
        rule for rule in config["packageRules"] if "docker-compose" in rule.get("matchManagers", [])
    ]


def test_renovate_watches_the_production_compose_file() -> None:
    """Renovate's docker-compose manager finds every compose file by default;
    the rule that groups the production images must name their directory, or
    a bump lands as an ungrouped PR nothing else expects."""
    rules = _docker_rules()
    assert rules, "no docker-compose rule; image pins are never bumped"
    compose_dir = _PROD_COMPOSE.parent.relative_to(_REPO).as_posix()
    patterns = [f for rule in rules for f in rule.get("matchFileNames", [])]
    assert any(pattern.rstrip("*").rstrip("/") == compose_dir for pattern in patterns), (
        f"renovate does not group images under {compose_dir}: {patterns}"
    )


@pytest.mark.parametrize("image", _MAJOR_LOCKED)
def test_renovate_never_bumps_a_database_major(image: str) -> None:
    """A major bump that auto-merged would leave the container exiting on an
    incompatible data directory, with the hub down until a restore."""
    disabled_major = [
        rule
        for rule in _docker_rules()
        if "major" in rule.get("matchUpdateTypes", []) and rule.get("enabled") is False
    ]
    assert disabled_major, f"docker major updates are not disabled, so {image} could jump"
    assert not any(
        "matchPackageNames" in rule and image not in rule["matchPackageNames"]
        for rule in disabled_major
    )


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


def test_a_matomo_image_bump_also_updates_its_application_files() -> None:
    """The matomo image keeps the application in a volume, and its entrypoint
    copies files only when ``matomo.php`` is absent -- so a recreated container
    served the OLD application from the volume while ``docker ps`` showed the
    new tag (5.12.0 files under a 5.13.0 image, found 260831). The update
    script must sync the image's files over the volume and run the schema
    update whenever the versions differ."""
    script = _UPDATE_SCRIPT.read_text()
    assert "/usr/src/matomo" in script, "matomo's application files are never refreshed"
    assert "core:update" in script, "matomo's schema update never runs"


def test_renovate_runs_from_this_repositorys_own_workflow() -> None:
    """The hosted app never ran here; a workflow we can see does the job.

    It reads the same config, runs on a schedule and on demand, pins the
    action to a commit, and uses a real token: a pull request opened with
    GITHUB_TOKEN triggers no other workflow, so the CI gate would never report
    on an update and auto-merge could never fire.
    """
    workflow = (_REPO / ".github" / "workflows" / "renovate.yml").read_text()
    assert "renovatebot/github-action@" in workflow
    assert "configurationFile: renovate.json" in workflow
    assert "schedule:" in workflow and "workflow_dispatch:" in workflow
    assert "token: ${{ secrets.RENOVATE_TOKEN }}" in workflow
    assert "GITHUB_TOKEN" not in workflow.split("token: ")[1].splitlines()[0]
    import re

    assert all(
        re.search(r"@[0-9a-f]{40} # v", line) for line in workflow.splitlines() if "uses:" in line
    ), "every action is pinned to a commit"


_DEPLOY_SCRIPT = _ROLE / "templates" / "deploy.sh.j2"


def test_a_release_deploy_applies_the_container_pins_afterwards() -> None:
    """A pin merged with a release used to wait for the next Monday timer --
    up to a week on an image with a published fix. The deploy now runs the
    update after the release is healthy, and a failure there is logged rather
    than turning a successful deploy into a failed one."""
    script = _DEPLOY_SCRIPT.read_text()
    success = script.index("Deploy successful")
    update = script.index("update-containers.sh", success)
    tail = script[update:]
    assert "exit 1" not in tail, "the container update must not fail the deploy"
    assert "log_error" in tail, "a failed update is logged"
