"""Tests for the cached GitHub star count shown in the footer.

The template global runs inside request rendering on the event loop, so it must
never perform network I/O itself: a stale cache schedules a background refresh
and the last known value is returned immediately.
"""

import asyncio
from typing import Any

import httpx
import pytest

from metaseed_hub.ui import render


class _FakeResponse:
    """Minimal stand-in for an httpx.Response."""

    def __init__(self, payload: dict[str, Any], status_code: int = 200) -> None:
        self._payload = payload
        self.status_code = status_code

    def raise_for_status(self) -> None:
        if self.status_code >= 400:
            raise httpx.HTTPStatusError("error", request=None, response=None)

    def json(self) -> dict[str, Any]:
        return self._payload


def _install_client(monkeypatch: pytest.MonkeyPatch, handler: Any) -> dict[str, int]:
    """Replace httpx.AsyncClient with a fake whose ``get`` delegates to ``handler``.

    Returns:
        A dict whose "requests" entry counts the GET calls made.
    """
    calls = {"requests": 0}

    class _Client:
        def __init__(self, *args: Any, **kwargs: Any) -> None:
            pass

        async def __aenter__(self) -> "_Client":
            return self

        async def __aexit__(self, *exc: Any) -> None:
            return None

        async def get(self, url: str, **kwargs: Any) -> Any:
            calls["requests"] += 1
            return handler(url)

    monkeypatch.setattr(render.httpx, "AsyncClient", _Client)
    return calls


async def _await_refresh(repo: str) -> None:
    """Wait for the scheduled background refresh of ``repo`` to finish."""
    task = render._stars_refresh_tasks.get(repo)
    if task is not None:
        await task


@pytest.fixture(autouse=True)
def _clear_cache():
    """Each test starts with an empty star cache and no in-flight refresh."""
    render._stars_cache.clear()
    render._stars_refresh_tasks.clear()
    yield
    render._stars_cache.clear()
    render._stars_refresh_tasks.clear()


@pytest.mark.asyncio
async def test_first_call_schedules_refresh_and_returns_none(monkeypatch):
    """With an empty cache the call returns None immediately and refreshes in background."""
    _install_client(monkeypatch, lambda _url: _FakeResponse({"stargazers_count": 42}))

    assert render.get_repo_stars("owner/repo") is None
    assert "owner/repo" in render._stars_refresh_tasks

    await _await_refresh("owner/repo")

    assert render.get_repo_stars("owner/repo") == 42


@pytest.mark.asyncio
async def test_cache_hit_avoids_second_request(monkeypatch):
    """A fresh cache entry is served without another request or refresh task."""
    calls = _install_client(monkeypatch, lambda _url: _FakeResponse({"stargazers_count": 7}))

    render.get_repo_stars("owner/repo")
    await _await_refresh("owner/repo")

    assert render.get_repo_stars("owner/repo") == 7
    assert render.get_repo_stars("owner/repo") == 7
    assert calls["requests"] == 1
    assert render._stars_refresh_tasks == {}


@pytest.mark.asyncio
async def test_get_repo_stars_does_not_block_on_slow_fetch(monkeypatch):
    """The template global returns immediately even while the fetch is hanging."""
    release = asyncio.Event()

    class _SlowClient:
        def __init__(self, *args: Any, **kwargs: Any) -> None:
            pass

        async def __aenter__(self) -> "_SlowClient":
            return self

        async def __aexit__(self, *exc: Any) -> None:
            return None

        async def get(self, url: str, **kwargs: Any) -> Any:
            await release.wait()
            return _FakeResponse({"stargazers_count": 5})

    monkeypatch.setattr(render.httpx, "AsyncClient", _SlowClient)

    # Returns without waiting for the in-flight request.
    assert render.get_repo_stars("owner/repo") is None
    # A second call while the refresh is in flight does not stack another task.
    task = render._stars_refresh_tasks["owner/repo"]
    assert render.get_repo_stars("owner/repo") is None
    assert render._stars_refresh_tasks["owner/repo"] is task

    release.set()
    await _await_refresh("owner/repo")

    assert render.get_repo_stars("owner/repo") == 5


@pytest.mark.asyncio
async def test_failure_returns_none_without_prior_value(monkeypatch):
    """A failing refresh with no prior value leaves the count at None."""

    def _raise(_url: str) -> Any:
        raise httpx.ConnectError("boom")

    _install_client(monkeypatch, _raise)

    assert render.get_repo_stars("owner/repo") is None
    await _await_refresh("owner/repo")

    assert render.get_repo_stars("owner/repo") is None


@pytest.mark.asyncio
async def test_failure_falls_back_to_prior_value(monkeypatch):
    """A failed refresh keeps serving the last known count."""
    _install_client(monkeypatch, lambda _url: _FakeResponse({"stargazers_count": 99}))
    render.get_repo_stars("owner/repo")
    await _await_refresh("owner/repo")
    assert render.get_repo_stars("owner/repo") == 99

    # Expire the cached entry so the next call re-fetches, then fail.
    fetched_at, value, _ttl = render._stars_cache["owner/repo"]
    render._stars_cache["owner/repo"] = (fetched_at - 10_000, value, render._STARS_OK_TTL_SECONDS)

    def _raise(_url: str) -> Any:
        raise httpx.ConnectError("boom")

    _install_client(monkeypatch, _raise)

    # The stale value is served while the refresh runs, and kept after it fails.
    assert render.get_repo_stars("owner/repo") == 99
    await _await_refresh("owner/repo")
    assert render.get_repo_stars("owner/repo") == 99


def test_without_running_loop_returns_cached_value_without_scheduling():
    """Outside an event loop the cached value is returned and nothing is scheduled."""
    render._stars_cache["owner/repo"] = (0.0, 3, 0)  # expired entry

    assert render.get_repo_stars("owner/repo") == 3
    assert render._stars_refresh_tasks == {}
