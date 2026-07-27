"""Database dumps and their retention.

A systemd timer on the deployment host runs this module daily: it takes a
``pg_dump`` of the hub database, verifies the dump is readable, and only then
applies the retention policy. Ordering matters -- pruning before a verified new
dump exists would let a failing backup run destroy the good ones.

Retention is grandfather-father-son: several recent dumps, then the newest of
each recent day, week, and month. The tiers overlap, and a dump is removed only
when it falls outside all of them.

Run it with ``python -m metaseed_hub.backup --directory /var/backups/...``; see
``docs/backups.md`` for the operational procedure and how to restore.
"""

from __future__ import annotations

import argparse
import logging
import re
import subprocess
import sys
from collections.abc import Iterable, Sequence
from dataclasses import dataclass, fields
from datetime import UTC, datetime
from pathlib import Path

logger = logging.getLogger("metaseed_hub.backup")

DUMP_PREFIX = "metaseed_hub"
# Dates use YYMMDD, matching the project's convention elsewhere.
_DUMP_PATTERN = re.compile(rf"^{DUMP_PREFIX}-(\d{{6}})-(\d{{6}})\.dump$")
_TIME_FORMAT = "%y%m%d-%H%M%S"


@dataclass(frozen=True)
class RetentionPolicy:
    """How many dumps each tier keeps.

    Attributes:
        last: Most recent dumps kept regardless of when they were taken.
        daily: Distinct days whose newest dump is kept.
        weekly: Distinct ISO weeks whose newest dump is kept.
        monthly: Distinct calendar months whose newest dump is kept.
    """

    last: int = 3
    daily: int = 7
    weekly: int = 4
    monthly: int = 6

    def __post_init__(self) -> None:
        """Reject a negative tier, which would keep nothing rather than more."""
        for field in fields(self):
            if getattr(self, field.name) < 0:
                msg = f"RetentionPolicy.{field.name} must not be negative"
                raise ValueError(msg)


def dump_name(taken_at: datetime) -> str:
    """Return the file name for a dump taken at ``taken_at`` (UTC)."""
    return f"{DUMP_PREFIX}-{taken_at.astimezone(UTC).strftime(_TIME_FORMAT)}.dump"


def parse_dump_time(path: Path) -> datetime | None:
    """Return when a dump was taken, read from its name, or None.

    The name is authoritative rather than the file's mtime, so dumps stay
    correctly aged after being copied to another host. Anything that does not
    match the naming pattern returns None and is left alone by the pruner.
    """
    match = _DUMP_PATTERN.match(path.name)
    if not match:
        return None
    try:
        return datetime.strptime(f"{match.group(1)}-{match.group(2)}", _TIME_FORMAT).replace(
            tzinfo=UTC
        )
    except ValueError:
        return None


def _newest_per_bucket(
    dumps: Sequence[tuple[Path, datetime]],
    key: object,
    limit: int,
) -> set[Path]:
    """Keep the newest dump of each of the ``limit`` most recent buckets."""
    kept: set[Path] = set()
    seen: list[object] = []
    for path, taken_at in dumps:  # newest first
        bucket = key(taken_at)  # type: ignore[operator]
        if bucket in seen:
            continue
        if len(seen) >= limit:
            break
        seen.append(bucket)
        kept.add(path)
    return kept


def select_expired(
    paths: Iterable[Path],
    *,
    policy: RetentionPolicy,
    now: datetime | None = None,
) -> list[Path]:
    """Return the dumps that no tier of ``policy`` keeps.

    Args:
        paths: Candidate files; non-dumps are ignored, never returned.
        policy: The tiers to apply.
        now: Unused for bucketing (buckets come from the dumps themselves) but
            accepted so callers can pin a reference time in tests.

    Returns:
        Paths safe to delete, oldest first. The most recent dump is never
        included, so pruning cannot leave the host without a backup.
    """
    dumps = sorted(
        ((path, taken) for path in paths if (taken := parse_dump_time(path)) is not None),
        key=lambda item: item[1],
        reverse=True,
    )
    if not dumps:
        return []

    kept = {path for path, _ in dumps[: policy.last]}
    kept |= _newest_per_bucket(dumps, lambda t: t.date(), policy.daily)
    kept |= _newest_per_bucket(dumps, lambda t: t.isocalendar()[:2], policy.weekly)
    kept |= _newest_per_bucket(dumps, lambda t: (t.year, t.month), policy.monthly)
    kept.add(dumps[0][0])

    return [path for path, _ in reversed(dumps) if path not in kept]


def prune(
    directory: Path,
    *,
    policy: RetentionPolicy,
    now: datetime | None = None,
    dry_run: bool = False,
) -> list[Path]:
    """Delete the dumps in ``directory`` that ``policy`` no longer keeps.

    Returns:
        The dumps deleted, or that would be deleted when ``dry_run``.
    """
    expired = select_expired(directory.iterdir(), policy=policy, now=now)
    for path in expired:
        if dry_run:
            logger.info("would delete %s", path.name)
            continue
        path.unlink()
        logger.info("deleted %s", path.name)
    return expired


def take_dump(
    directory: Path,
    *,
    container: str,
    database: str,
    user: str,
    now: datetime | None = None,
) -> Path:
    """Write a verified ``pg_dump`` of the database into ``directory``.

    The dump is written to a temporary name and renamed into place only after it
    reads back cleanly, so a partial file is never mistaken for a backup and an
    off-host copier never picks one up mid-write.

    Raises:
        RuntimeError: If the dump command fails or the result is unreadable.
    """
    directory.mkdir(parents=True, exist_ok=True)
    target = directory / dump_name(now or datetime.now(UTC))
    partial = target.with_suffix(".dump.partial")

    with partial.open("wb") as handle:
        result = subprocess.run(  # noqa: S603 - fixed argv, no shell
            [
                "docker",
                "exec",
                container,
                "pg_dump",
                "--username",
                user,
                "--dbname",
                database,
                "--format=custom",
            ],
            stdout=handle,
            stderr=subprocess.PIPE,
            check=False,
        )
    if result.returncode != 0:
        partial.unlink(missing_ok=True)
        msg = f"pg_dump failed: {result.stderr.decode(errors='replace').strip()}"
        raise RuntimeError(msg)

    listing = subprocess.run(  # noqa: S603 - fixed argv, no shell
        ["pg_restore", "--list", str(partial)],  # noqa: S607 - on PATH by deployment
        capture_output=True,
        check=False,
    )
    if listing.returncode != 0 or not listing.stdout.strip():
        partial.unlink(missing_ok=True)
        msg = "the dump is not readable by pg_restore; keeping the previous backups"
        raise RuntimeError(msg)

    partial.rename(target)
    logger.info("wrote %s (%d bytes)", target.name, target.stat().st_size)
    return target


def _parse_args(argv: Sequence[str] | None) -> argparse.Namespace:
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument("--directory", type=Path, required=True, help="where dumps are stored")
    parser.add_argument("--container", default="metaseed-postgres", help="postgres container name")
    parser.add_argument("--database", default="metaseed_hub")
    parser.add_argument("--user", default="metaseed")
    parser.add_argument("--keep-last", type=int, default=RetentionPolicy.last)
    parser.add_argument("--keep-daily", type=int, default=RetentionPolicy.daily)
    parser.add_argument("--keep-weekly", type=int, default=RetentionPolicy.weekly)
    parser.add_argument("--keep-monthly", type=int, default=RetentionPolicy.monthly)
    parser.add_argument(
        "--prune-only",
        action="store_true",
        help="apply retention without taking a new dump",
    )
    parser.add_argument(
        "--dry-run",
        action="store_true",
        help="report what retention would delete, and delete nothing",
    )
    return parser.parse_args(argv)


def main(argv: Sequence[str] | None = None) -> int:
    """Take a dump (unless ``--prune-only``) and then apply retention."""
    args = _parse_args(argv)
    logging.basicConfig(level=logging.INFO, format="%(message)s")
    policy = RetentionPolicy(
        last=args.keep_last,
        daily=args.keep_daily,
        weekly=args.keep_weekly,
        monthly=args.keep_monthly,
    )

    if not args.prune_only:
        try:
            take_dump(
                args.directory,
                container=args.container,
                database=args.database,
                user=args.user,
            )
        except RuntimeError as exc:
            # Abort before pruning: a failed run must not cost existing dumps.
            logger.error("backup failed: %s", exc)
            return 1

    prune(args.directory, policy=policy, dry_run=args.dry_run)
    return 0


if __name__ == "__main__":
    sys.exit(main())
