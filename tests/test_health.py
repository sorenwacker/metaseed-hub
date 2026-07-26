"""Tests for the /health endpoint's resource handling.

A failed database or Redis probe must still release the objects it created,
otherwise every unhealthy poll leaks a connection pool.
"""

import pytest

from metaseed_hub.api import health


class _FailingConnCtx:
    async def __aenter__(self) -> None:
        raise RuntimeError("db down")

    async def __aexit__(self, *args: object) -> bool:
        return False


class FakeEngine:
    def __init__(self) -> None:
        self.disposed = False

    def connect(self) -> _FailingConnCtx:
        return _FailingConnCtx()

    async def dispose(self) -> None:
        self.disposed = True


class FakeRedis:
    def __init__(self) -> None:
        self.closed = False

    async def ping(self) -> None:
        raise RuntimeError("redis down")

    async def aclose(self) -> None:
        self.closed = True


@pytest.mark.asyncio
async def test_health_releases_resources_when_probes_fail(monkeypatch):
    fake_engine = FakeEngine()
    fake_redis = FakeRedis()
    monkeypatch.setattr(health, "create_async_engine", lambda url: fake_engine)
    monkeypatch.setattr("redis.asyncio.from_url", lambda url: fake_redis)

    result = await health.health_check()

    # Both probes failed, so the endpoint reports degraded...
    assert result["status"] == "degraded"
    assert result["services"]["database"].startswith("unhealthy")
    assert result["services"]["redis"].startswith("unhealthy")
    # ...and both created objects were released rather than leaked.
    assert fake_engine.disposed is True
    assert fake_redis.closed is True
