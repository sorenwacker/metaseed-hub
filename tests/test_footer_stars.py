"""Tests for the cached GitHub star count shown in the footer."""

import httpx
import pytest

from metaseed_hub.ui import render


class _FakeResponse:
    """Minimal stand-in for an httpx.Response."""

    def __init__(self, payload: dict, status_code: int = 200) -> None:
        self._payload = payload
        self.status_code = status_code

    def raise_for_status(self) -> None:
        if self.status_code >= 400:
            raise httpx.HTTPStatusError("error", request=None, response=None)

    def json(self) -> dict:
        return self._payload


@pytest.fixture(autouse=True)
def _clear_cache():
    """Each test starts with an empty star cache."""
    render._stars_cache.clear()
    yield
    render._stars_cache.clear()


def test_successful_fetch_returns_star_count(monkeypatch):
    monkeypatch.setattr(
        render.httpx, "get", lambda *a, **k: _FakeResponse({"stargazers_count": 42})
    )

    assert render.get_repo_stars("owner/repo") == 42


def test_cache_hit_avoids_second_request(monkeypatch):
    calls = {"n": 0}

    def _get(*_args, **_kwargs):
        calls["n"] += 1
        return _FakeResponse({"stargazers_count": 7})

    monkeypatch.setattr(render.httpx, "get", _get)

    assert render.get_repo_stars("owner/repo") == 7
    assert render.get_repo_stars("owner/repo") == 7
    assert calls["n"] == 1


def test_failure_returns_none_without_prior_value(monkeypatch):
    def _raise(*_args, **_kwargs):
        raise httpx.ConnectError("boom")

    monkeypatch.setattr(render.httpx, "get", _raise)

    assert render.get_repo_stars("owner/repo") is None


def test_failure_falls_back_to_prior_value(monkeypatch):
    monkeypatch.setattr(
        render.httpx, "get", lambda *a, **k: _FakeResponse({"stargazers_count": 99})
    )
    assert render.get_repo_stars("owner/repo") == 99

    # Expire the cached entry so the next call re-fetches, then fail.
    fetched_at, value, _ttl = render._stars_cache["owner/repo"]
    render._stars_cache["owner/repo"] = (fetched_at - 10_000, value, render._STARS_OK_TTL_SECONDS)
    monkeypatch.setattr(
        render.httpx, "get", lambda *a, **k: (_ for _ in ()).throw(httpx.ConnectError("boom"))
    )

    assert render.get_repo_stars("owner/repo") == 99
