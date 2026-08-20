"""Comparison of the container images a host runs against the images it pins.

The compose file records which image each service should run. Docker records
which image each container actually runs. These diverge silently: a merged pin
bump changes nothing until the host applies it, and nothing about a running
container reveals that a newer pin exists. Drift is therefore the signal that
``metaseed-containers.timer`` has not applied a change -- either it has not
fired since the merge, or it failed.

The check reads both sides through injected callables rather than reaching for
Docker itself, so the admin route and the tests supply their own. When Docker
cannot be reached the result is *unknown*, never drift: a daemon that is down or
a permission the app lacks says nothing about whether the pins were applied, and
reporting that as a problem with the deployment would be wrong.

Run it with ``python -m metaseed_hub.container_drift``; see
``docs/container-updates.md`` for what to do when it reports drift.
"""

from __future__ import annotations

import argparse
import logging
import subprocess
import sys
from collections.abc import Callable, Mapping
from dataclasses import dataclass
from pathlib import Path

import yaml

logger = logging.getLogger("metaseed_hub.container_drift")

DEFAULT_COMPOSE_PATH = Path("/opt/metaseed-docker/docker-compose.yml")

#: Reads the compose file and returns ``{container_name: image}``.
PinReader = Callable[[], Mapping[str, str]]
#: Reads the running containers and returns ``{container_name: image}``.
RunningReader = Callable[[], Mapping[str, str]]


@dataclass(frozen=True)
class ContainerState:
    """What one container pins versus what it runs.

    Attributes:
        name: The container name, as it appears in both compose and Docker.
        pinned: The image named in the compose file.
        running: The image the container actually runs, or None if it is not up.
    """

    name: str
    pinned: str
    running: str | None

    @property
    def has_drifted(self) -> bool:
        """Whether the running image differs from the pin.

        A container that is not running has not drifted -- it is absent, which
        is a different problem and is reported as such rather than as a version
        mismatch.
        """
        return self.running is not None and self.running != self.pinned


@dataclass(frozen=True)
class DriftReport:
    """The outcome of one comparison.

    Attributes:
        containers: One entry per service in the compose file, empty if the
            check could not run.
        unavailable: Why Docker could not be read, or None if it was read.
    """

    containers: tuple[ContainerState, ...]
    unavailable: str | None = None

    @property
    def checked(self) -> bool:
        """Whether the comparison actually ran."""
        return self.unavailable is None

    @property
    def drifted(self) -> tuple[ContainerState, ...]:
        """Containers whose running image does not match their pin."""
        return tuple(c for c in self.containers if c.has_drifted)

    @property
    def missing(self) -> tuple[ContainerState, ...]:
        """Containers named in the compose file that are not running."""
        return tuple(c for c in self.containers if c.running is None)

    @property
    def summary(self) -> str:
        """One line describing the result, for the dashboard and the log."""
        if not self.checked:
            return f"Not checked: {self.unavailable}"
        if self.drifted:
            names = ", ".join(c.name for c in self.drifted)
            return f"{len(self.drifted)} container(s) behind their pins: {names}"
        if self.missing:
            names = ", ".join(c.name for c in self.missing)
            return f"{len(self.missing)} pinned container(s) not running: {names}"
        return f"All {len(self.containers)} containers match their pins"


def read_pins(compose_path: Path = DEFAULT_COMPOSE_PATH) -> Mapping[str, str]:
    """Read the pinned image of each service from a compose file.

    Args:
        compose_path: Path to the compose file.

    Returns:
        Mapping of container name to pinned image. Services are keyed by their
        ``container_name`` so the result lines up with what Docker reports.

    Raises:
        OSError: If the compose file cannot be read.
    """
    compose = yaml.safe_load(compose_path.read_text())
    pins: dict[str, str] = {}
    for service, spec in (compose.get("services") or {}).items():
        name = spec.get("container_name", service)
        image = spec.get("image")
        if image:
            pins[name] = image
    return pins


def read_running() -> Mapping[str, str]:
    """Read the image of each running container from Docker.

    Returns:
        Mapping of container name to the image it runs.

    Raises:
        OSError: If the Docker CLI is absent.
        subprocess.SubprocessError: If Docker cannot be queried.
    """
    result = subprocess.run(  # noqa: S603
        ["docker", "ps", "--format", "{{.Names}}\t{{.Image}}"],  # noqa: S607
        capture_output=True,
        text=True,
        check=True,
        timeout=15,
    )
    running: dict[str, str] = {}
    for line in result.stdout.splitlines():
        if "\t" in line:
            name, image = line.split("\t", 1)
            running[name.strip()] = image.strip()
    return running


def compare(pins: PinReader, running: RunningReader) -> DriftReport:
    """Compare pinned images against running ones.

    Args:
        pins: Returns the pinned image of each container.
        running: Returns the image each running container uses.

    Returns:
        The comparison. If either side cannot be read the report is marked
        unavailable rather than reporting drift, because an unreadable side is
        not evidence that the host is behind.
    """
    try:
        pinned_images = pins()
    except (OSError, yaml.YAMLError) as exc:
        return DriftReport(containers=(), unavailable=f"compose file unreadable ({exc})")

    try:
        running_images = running()
    except (OSError, subprocess.SubprocessError) as exc:
        return DriftReport(containers=(), unavailable=f"Docker unreachable ({exc})")

    states = tuple(
        ContainerState(name=name, pinned=image, running=running_images.get(name))
        for name, image in sorted(pinned_images.items())
    )
    return DriftReport(containers=states)


def check(compose_path: Path = DEFAULT_COMPOSE_PATH) -> DriftReport:
    """Compare the host's containers against a compose file.

    Args:
        compose_path: Path to the compose file holding the pins.

    Returns:
        The comparison.
    """
    return compare(pins=lambda: read_pins(compose_path), running=read_running)


def main(argv: list[str] | None = None) -> int:
    """Report container drift on the command line.

    Args:
        argv: Arguments to parse, or None to read from the command line.

    Returns:
        0 when every container matches its pin or the check could not run,
        1 when at least one container is behind or missing. An unreadable
        Docker is not a failure: someone else's outage must not read as a
        problem with this deployment.
    """
    parser = argparse.ArgumentParser(description="Compare running images against their pins.")
    parser.add_argument(
        "--compose",
        type=Path,
        default=DEFAULT_COMPOSE_PATH,
        help=f"Path to the compose file (default: {DEFAULT_COMPOSE_PATH})",
    )
    args = parser.parse_args(argv)

    logging.basicConfig(level=logging.INFO, format="%(message)s")
    report = check(args.compose)
    logger.info(report.summary)

    for container in report.containers:
        state = container.running or "not running"
        marker = "DRIFT" if container.has_drifted else "ok   "
        logger.info(
            "%s %-24s pinned %-24s running %s",
            marker,
            container.name,
            container.pinned,
            state,
        )

    if not report.checked:
        return 0
    return 1 if (report.drifted or report.missing) else 0


if __name__ == "__main__":
    sys.exit(main())
