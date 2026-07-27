"""The backup retention policy decides which dumps survive a prune.

Every assertion here is about *which files remain*, because the failure mode
that matters is a policy that quietly deletes the wrong dump. A test that only
counted survivors would pass while keeping an arbitrary subset.
"""

from __future__ import annotations

from datetime import UTC, datetime, timedelta
from pathlib import Path

import pytest

from metaseed_hub.backup import (
    RetentionPolicy,
    dump_name,
    parse_dump_time,
    prune,
    select_expired,
)

NOW = datetime(2026, 7, 27, 3, 0, tzinfo=UTC)


def _at(*, days: int = 0, hours: int = 0) -> datetime:
    return NOW - timedelta(days=days, hours=hours)


def _paths(directory: Path, times: list[datetime]) -> list[Path]:
    for taken in times:
        (directory / dump_name(taken)).write_bytes(b"PGDMP-stub")
    return sorted(directory.iterdir())


def test_dump_name_round_trips_through_the_parser() -> None:
    """The filename is the authoritative timestamp, so it must survive a round
    trip; mtime is not consulted anywhere."""
    taken = datetime(2026, 7, 27, 2, 30, 15, tzinfo=UTC)
    assert dump_name(taken) == "metaseed_hub-260727-023015.dump"
    assert parse_dump_time(Path(dump_name(taken))) == taken


def test_files_that_are_not_dumps_are_ignored() -> None:
    assert parse_dump_time(Path("notes.txt")) is None
    assert parse_dump_time(Path("metaseed_hub-nonsense.dump")) is None


def test_recent_dumps_are_all_kept(tmp_path: Path) -> None:
    """Several dumps on the same day: the keep-last tier holds the newest few."""
    times = [_at(hours=h) for h in (1, 2, 3, 4, 5)]
    paths = _paths(tmp_path, times)
    policy = RetentionPolicy(last=3, daily=0, weekly=0, monthly=0)

    expired = select_expired(paths, policy=policy, now=NOW)

    assert {p.name for p in expired} == {dump_name(times[3]), dump_name(times[4])}


def _on_day(days_back: int, hour: int) -> datetime:
    """A dump taken at ``hour`` on the calendar day ``days_back`` days ago."""
    day = (NOW - timedelta(days=days_back)).date()
    return datetime(day.year, day.month, day.day, hour, tzinfo=UTC)


def test_one_dump_per_day_survives_within_the_daily_horizon(tmp_path: Path) -> None:
    """Two dumps a day for ten days: the newest of each of the last 7 calendar
    days stays, older days go."""
    times = [_on_day(d, hour) for d in range(10) for hour in (1, 13)]
    paths = _paths(tmp_path, times)
    policy = RetentionPolicy(last=1, daily=7, weekly=0, monthly=0)

    kept = {p.name for p in paths} - {p.name for p in select_expired(paths, policy=policy, now=NOW)}

    # 13:00 is the newest dump of each day, so that is the one each day keeps.
    assert kept == {dump_name(_on_day(d, 13)) for d in range(7)}


def test_weekly_and_monthly_tiers_keep_older_dumps_alive(tmp_path: Path) -> None:
    """A daily dump for a year, pruned: recent days, then weeks, then months."""
    times = [_at(days=d) for d in range(365)]
    paths = _paths(tmp_path, times)
    policy = RetentionPolicy(last=3, daily=7, weekly=4, monthly=6)

    kept = sorted(
        (p for p in paths if p not in set(select_expired(paths, policy=policy, now=NOW))),
        key=lambda p: parse_dump_time(p),  # type: ignore[arg-type,return-value]
        reverse=True,
    )
    kept_times = [parse_dump_time(p) for p in kept]

    # Daily tier: every one of the last 7 days.
    assert kept_times[:7] == [_at(days=d) for d in range(7)]
    # Weekly and monthly tiers reach back further without keeping everything.
    assert len(kept) < 25
    oldest = min(t for t in kept_times if t is not None)
    assert (NOW - oldest).days > 120, "the monthly tier must reach months back"
    # Distinct months are represented rather than a single old cluster.
    assert len({(t.year, t.month) for t in kept_times if t is not None}) >= 6


def test_the_newest_dump_is_never_expired(tmp_path: Path) -> None:
    """Even a policy that keeps nothing must not leave the host with no backup."""
    times = [_at(days=d) for d in range(5)]
    paths = _paths(tmp_path, times)
    policy = RetentionPolicy(last=0, daily=0, weekly=0, monthly=0)

    expired = select_expired(paths, policy=policy, now=NOW)

    assert dump_name(times[0]) not in {p.name for p in expired}


def test_prune_deletes_only_expired_files_and_leaves_strangers(tmp_path: Path) -> None:
    times = [_at(days=d) for d in range(5)]
    _paths(tmp_path, times)
    (tmp_path / "README").write_text("not a dump")
    policy = RetentionPolicy(last=1, daily=2, weekly=0, monthly=0)

    deleted = prune(tmp_path, policy=policy, now=NOW)

    remaining = {p.name for p in tmp_path.iterdir()}
    assert remaining == {"README", dump_name(times[0]), dump_name(times[1])}
    assert {p.name for p in deleted} == {dump_name(t) for t in times[2:]}


def test_dry_run_reports_without_deleting(tmp_path: Path) -> None:
    times = [_at(days=d) for d in range(5)]
    _paths(tmp_path, times)
    policy = RetentionPolicy(last=1, daily=1, weekly=0, monthly=0)

    deleted = prune(tmp_path, policy=policy, now=NOW, dry_run=True)

    assert deleted, "the dry run must still report what it would remove"
    assert len(list(tmp_path.iterdir())) == 5


@pytest.mark.parametrize("field", ["last", "daily", "weekly", "monthly"])
def test_a_negative_tier_is_rejected(field: str) -> None:
    """A negative count would silently invert the comparison and delete
    everything."""
    with pytest.raises(ValueError, match=field):
        RetentionPolicy(**{field: -1})
