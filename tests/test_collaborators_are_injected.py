"""A component depends on injected collaborators, never on discovered ones.

Three places reached for a global instead: the error-recording middleware
imported the UI layer and the global database from inside its methods (a
top-level module reaching down into the layers above it), ``auth_callback``
opened a session from the global ``db`` while every sibling route takes
``DbSession``, and the websocket manager was a module-level instance whose
Redis URL it resolved itself. Whoever composes the application now supplies
each of them, and a test can substitute them without patching a module.
"""

from __future__ import annotations

import ast
import inspect
from pathlib import Path
from typing import Any
from unittest.mock import AsyncMock, Mock

import pytest

SRC = Path(__file__).resolve().parents[1] / "src" / "metaseed_hub"


def _imports_of(path: Path) -> set[str]:
    tree = ast.parse(path.read_text(encoding="utf-8"))
    found: set[str] = set()
    for node in ast.walk(tree):
        if isinstance(node, ast.ImportFrom) and node.module:
            found.add(node.module)
        elif isinstance(node, ast.Import):
            found.update(alias.name for alias in node.names)
    return found


class TestErrorRecordingMiddleware:
    def test_it_imports_neither_the_ui_layer_nor_the_global_database(self) -> None:
        imported = _imports_of(SRC / "errors.py")
        assert not any(m.startswith("metaseed_hub.ui") for m in imported), imported
        assert "metaseed_hub.database" not in imported, imported

    @pytest.mark.asyncio
    async def test_it_records_with_the_collaborators_it_was_given(self) -> None:
        from metaseed_hub.errors import ErrorRecordingMiddleware

        opened: list[str] = []
        session = AsyncMock()
        session.execute = AsyncMock(return_value=Mock(scalar_one_or_none=Mock(return_value="u-1")))

        class _Factory:
            def __call__(self) -> _Factory:
                return self

            async def __aenter__(self) -> Any:
                opened.append("open")
                return session

            async def __aexit__(self, *args: Any) -> None:
                opened.append("close")

        resolver = AsyncMock(return_value=Mock(keycloak_id="kc-1"))
        middleware = ErrorRecordingMiddleware(
            app=Mock(), session_factory=_Factory(), resolve_caller=resolver
        )

        async def _boom(request: Any) -> Any:
            raise RuntimeError("escaped")

        request = Mock(url=Mock(path="/hub/x"), method="GET", cookies={}, headers={})
        with pytest.raises(RuntimeError):
            await middleware.dispatch(request, _boom)

        assert opened == ["open", "close"], "the given session factory was used"
        resolver.assert_awaited_once_with(request)


class TestAuthCallback:
    def test_the_route_module_does_not_open_a_session_from_the_global(self) -> None:
        assert "metaseed_hub.database" not in _imports_of(SRC / "ui" / "routes" / "auth.py")

    def test_the_callback_takes_its_session_like_every_other_route(self) -> None:
        from metaseed_hub.ui.routes.auth import auth_callback

        assert "session" in inspect.signature(auth_callback).parameters


class TestWebSocketManager:
    def test_no_module_level_instance_and_no_settings_lookup(self) -> None:
        path = SRC / "websocket" / "__init__.py"
        tree = ast.parse(path.read_text(encoding="utf-8"))
        module_assignments = {
            t.id
            for node in tree.body
            if isinstance(node, ast.Assign)
            for t in node.targets
            if isinstance(t, ast.Name)
        }
        assert "manager" not in module_assignments, "a module-level manager instance"
        assert "metaseed_hub.config" not in _imports_of(path), (
            "the manager resolves settings itself"
        )
        main_source = (SRC / "main.py").read_text(encoding="utf-8")
        assert "from metaseed_hub.websocket import manager" not in main_source

    def test_the_manager_is_built_by_the_app_with_its_redis_url(self) -> None:
        from metaseed_hub.main import create_app
        from metaseed_hub.websocket import WebSocketManager

        assert "redis_url" in inspect.signature(WebSocketManager).parameters
        app = create_app()
        assert isinstance(app.state.manager, WebSocketManager)
