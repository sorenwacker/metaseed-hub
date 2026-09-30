"""Names, docstrings and code say what is true, and nothing reachable by nothing stays.

The last theme of the 260930 review: a docstring describing an access rule
the code does not implement, a comment naming a gate that did not exist, a
flag (``has_unsaved_changes``) describing a state that never occurs because
every edit is saved as it is made, a branch that cannot fire, a route nothing
requests, an attribute nothing reads, and ``Any`` where the type is known.
Each of these is pinned here so it cannot come back as prose.
"""

from __future__ import annotations

import ast
import inspect
from pathlib import Path

SRC = Path(__file__).resolve().parents[1] / "src" / "metaseed_hub"


def _source(rel: str) -> str:
    return (SRC / rel).read_text(encoding="utf-8")


def test_no_unsaved_state_is_tracked() -> None:
    """Every edit is saved when made; a flag saying otherwise misled the
    template into showing an asterisk for a state that never existed."""
    offenders = [
        f"{path.relative_to(SRC)}:{n}"
        for path in list(SRC.rglob("*.py")) + list(SRC.rglob("*.html"))
        for n, line in enumerate(path.read_text(encoding="utf-8").splitlines(), 1)
        if any(m in line for m in ("has_unsaved_changes", "mark_changed", "mark_saved", "unsaved"))
    ]
    assert not offenders, "\n".join(offenders)


def test_nothing_defines_what_nothing_uses() -> None:
    assert "get_entities_list" not in _source("ui/spec_builder/routes/entity_routes.py"), (
        "GET /{draft_id}/entities is requested by nothing"
    )
    assert "title_of" not in _source("sharing.py"), "SharedResource.title_of is read by nothing"
    assert "ready for a push" not in _source("ui/routes/seek.py"), (
        "the readiness branch for an empty wanted list cannot fire"
    )


def test_docstrings_describe_the_access_rules_the_code_applies() -> None:
    admin = ast.get_docstring(ast.parse(_source("ui/routes/admin.py"))) or ""
    assert "user.roles" not in admin and "entitlement" in admin
    seek = ast.get_docstring(ast.parse(_source("ui/routes/seek.py"))) or ""
    assert "feature" not in seek and "every signed-in user" in seek
    from metaseed_hub.ui.spec_builder.access import require_draft_access

    doc = inspect.getdoc(require_draft_access) or ""
    assert "any user in the draft's tenant" not in doc and "collaboration" in doc


def test_the_message_types_docstring_sits_on_its_constant() -> None:
    tree = ast.parse(_source("websocket/__init__.py"))
    body = tree.body
    for i, node in enumerate(body):
        if (
            isinstance(node, ast.AnnAssign)
            and isinstance(node.target, ast.Name)
            and node.target.id == "SERVER_MESSAGE_TYPES"
        ):
            following = body[i + 1]
            assert isinstance(following, ast.Expr) and isinstance(following.value, ast.Constant), (
                "the docstring for SERVER_MESSAGE_TYPES must follow its assignment"
            )
            assert "only the server may originate" in str(following.value.value)
            return
    raise AssertionError("SERVER_MESSAGE_TYPES not found")


def test_the_entity_service_names_its_types() -> None:
    from metaseed_hub.ui.services.entity_service import EntityService

    assert inspect.signature(EntityService.ensure_state).return_annotation != "Any"
    assert "kept as Any to avoid circular import" not in _source("ui/services/entity_service.py")
