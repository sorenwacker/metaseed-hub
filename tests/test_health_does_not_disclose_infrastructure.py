"""An unhealthy service is reported, not described.

`/api/health` is mounted without an auth dependency, so anyone can poll it.
It reported failures as `f"unhealthy: {e}"`, and connection errors from
asyncpg and redis-py carry the detail needed to reach the service: host, port,
database name, user, and driver hints. That is infrastructure topology handed
to anonymous callers.

The caller now learns only that a service is unhealthy. The exception still
exists — it goes to the server log, where the operator can read it.
"""

from __future__ import annotations

import logging
from typing import Any

import pytest

from metaseed_hub.api import health


class _Boom:
    """A connection attempt that fails the way a real one leaks detail."""

    message = (
        "connection to server at 'db.internal' (10.0.0.7), port 5432 failed: "
        "FATAL: role 'metaseed_prod' does not exist"
    )

    def __call__(self, *args: Any, **kwargs: Any) -> Any:
        raise OSError(self.message)


@pytest.fixture
def both_services_fail(monkeypatch: pytest.MonkeyPatch) -> _Boom:
    boom = _Boom()
    monkeypatch.setattr(health, "create_async_engine", boom)

    # Patch the module's own attribute, not a sys.modules entry: health.py does
    # `import redis.asyncio as redis`, which binds the attribute off the parent
    # package whenever redis.asyncio has genuinely been imported — so a
    # sys.modules substitution silently loses to the real module.
    import redis.asyncio

    def _refuse(*args: Any, **kwargs: Any) -> Any:
        raise OSError(_Boom.message)

    monkeypatch.setattr(redis.asyncio, "from_url", _refuse)
    return boom


@pytest.mark.asyncio
async def test_a_failure_is_reported_without_its_detail(both_services_fail: _Boom) -> None:
    result = await health.health_check()

    assert result["status"] == "degraded"
    assert result["services"]["database"] == "unhealthy"
    assert result["services"]["redis"] == "unhealthy"


@pytest.mark.asyncio
async def test_no_part_of_the_exception_reaches_the_caller(both_services_fail: _Boom) -> None:
    """Host, port, database name and user must not appear in the response."""
    result = await health.health_check()

    body = str(result)
    for secret in ("db.internal", "10.0.0.7", "5432", "metaseed_prod", "FATAL"):
        assert secret not in body


@pytest.mark.asyncio
async def test_the_operator_still_gets_the_exception(
    both_services_fail: _Boom, caplog: pytest.LogCaptureFixture
) -> None:
    """Hiding it from the caller must not hide it from the log."""
    with caplog.at_level(logging.ERROR, logger="metaseed_hub"):
        await health.health_check()

    assert "db.internal" in caplog.text
