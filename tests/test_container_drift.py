"""The comparison between pinned and running container images.

The interesting cases are the ones where the check must *not* claim a problem:
a container that is simply not up, and a Docker daemon that cannot be reached
at all. Reporting either as drift would send an administrator looking for an
unapplied update that does not exist.
"""

from __future__ import annotations

import subprocess
from pathlib import Path

import pytest
import yaml

from metaseed_hub.container_drift import ContainerState, check, compare, main, read_pins

_PINS = {"metaseed-postgres": "postgres:16.15", "metaseed-redis": "redis:7.4.10-alpine"}


def _compose(tmp_path: Path, services: dict[str, dict[str, str]]) -> Path:
    path = tmp_path / "docker-compose.yml"
    path.write_text(yaml.safe_dump({"services": services}))
    return path


def test_matching_images_are_not_drift() -> None:
    report = compare(pins=lambda: _PINS, running=lambda: dict(_PINS))
    assert report.checked
    assert report.drifted == ()
    assert report.summary == "All 2 containers match their pins"


def test_an_older_running_image_is_drift() -> None:
    running = {"metaseed-postgres": "postgres:16.13", "metaseed-redis": "redis:7.4.10-alpine"}
    report = compare(pins=lambda: _PINS, running=lambda: running)
    assert [c.name for c in report.drifted] == ["metaseed-postgres"]
    assert "metaseed-postgres" in report.summary


def test_a_container_that_is_not_running_is_reported_separately() -> None:
    """Absent is a different problem from behind, and needs a different fix."""
    running = {"metaseed-redis": "redis:7.4.10-alpine"}
    report = compare(pins=lambda: _PINS, running=lambda: running)
    assert report.drifted == ()
    assert [c.name for c in report.missing] == ["metaseed-postgres"]


def test_unreachable_docker_reports_not_checked_rather_than_drift() -> None:
    """The daemon being down says nothing about whether the pins were applied."""

    def unreachable() -> dict[str, str]:
        raise FileNotFoundError("docker: command not found")

    report = compare(pins=lambda: _PINS, running=unreachable)
    assert not report.checked
    assert report.drifted == ()
    assert report.containers == ()
    assert report.summary.startswith("Not checked:")


def test_a_docker_failure_reports_not_checked() -> None:
    def failing() -> dict[str, str]:
        raise subprocess.CalledProcessError(1, "docker ps")

    report = compare(pins=lambda: _PINS, running=failing)
    assert not report.checked


def test_an_unreadable_compose_file_reports_not_checked(tmp_path: Path) -> None:
    report = check(tmp_path / "absent.yml")
    assert not report.checked
    assert "compose file unreadable" in report.summary


def test_pins_are_keyed_by_container_name(tmp_path: Path) -> None:
    """Docker reports container names, so the pins must line up with those and
    not with the compose service names."""
    path = _compose(
        tmp_path,
        {"postgres": {"image": "postgres:16.15", "container_name": "metaseed-postgres"}},
    )
    assert read_pins(path) == {"metaseed-postgres": "postgres:16.15"}


def test_a_service_without_a_container_name_falls_back_to_the_service_name(
    tmp_path: Path,
) -> None:
    path = _compose(tmp_path, {"redis": {"image": "redis:7.4.10-alpine"}})
    assert read_pins(path) == {"redis": "redis:7.4.10-alpine"}


@pytest.mark.parametrize(
    ("running", "expected"),
    [("postgres:16.15", False), ("postgres:16.13", True)],
)
def test_container_state_knows_whether_it_drifted(running: str, expected: bool) -> None:
    state = ContainerState(name="metaseed-postgres", pinned="postgres:16.15", running=running)
    assert state.has_drifted is expected


def test_a_container_that_is_down_has_not_drifted() -> None:
    state = ContainerState(name="metaseed-postgres", pinned="postgres:16.15", running=None)
    assert state.has_drifted is False


def test_the_command_fails_on_drift(tmp_path: Path, monkeypatch: pytest.MonkeyPatch) -> None:
    path = _compose(
        tmp_path,
        {"postgres": {"image": "postgres:16.15", "container_name": "metaseed-postgres"}},
    )
    monkeypatch.setattr(
        "metaseed_hub.container_drift.read_running",
        lambda: {"metaseed-postgres": "postgres:16.13"},
    )
    assert main(["--compose", str(path)]) == 1


def test_the_command_succeeds_when_docker_cannot_be_reached(tmp_path: Path) -> None:
    """Someone else's outage must not read as a failure of this deployment."""
    path = _compose(
        tmp_path,
        {"postgres": {"image": "postgres:16.15", "container_name": "metaseed-postgres"}},
    )

    def unreachable() -> dict[str, str]:
        raise FileNotFoundError("docker: command not found")

    import metaseed_hub.container_drift as module

    original = module.read_running
    module.read_running = unreachable  # type: ignore[assignment]
    try:
        assert main(["--compose", str(path)]) == 0
    finally:
        module.read_running = original  # type: ignore[assignment]
