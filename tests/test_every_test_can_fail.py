"""Every test must be able to fail.

A test that is always skipped, has an empty body, or checks nothing passes on
broken code, which is worse than no test: it reports cover that is not there.
The same gate runs in metaseed.
"""

from __future__ import annotations

import ast
from pathlib import Path

TESTS = Path(__file__).parent

#: Calls that check without an ``assert`` statement.
#: ``until`` is a ``WebDriverWait`` that fails the test when it times out.
_CHECKING_CALLS = frozenset({"raises", "warns", "fail", "deprecated_call", "until"})

#: Tests whose check is that a call completes without raising; each names that call.
_CHECKED_BY_NOT_RAISING = {
    "test_security.py::test_validate_csrf_or_error_valid": "validate_csrf_or_error",
    "test_mcp_spec_drafts.py::test_two_users_may_hold_the_same_draft_name": "create",
    "test_websocket.py::test_dispatch_local_unknown_project_is_noop": "_dispatch_local",
    "test_spec_builder_roles.py::test_owner_member_passes_owner_gate": "require_owner_role",
    "test_admin_user_activity.py::test_a_database_failure_does_not_break_sign_in": "record_login",
    "test_helpers.py::test_isa_serialize_is_json_serializable": "json.dumps",
    "test_helpers.py::test_miappe_serialize_is_json_serializable": "json.dumps",
    "test_selenium_dataset_list.py::test_the_cards_view_loads_without_a_script_error": (
        "_no_severe_console_errors"
    ),
    "test_admin_error_log.py::test_recording_never_raises": "record_error",
}


def _tests() -> list[tuple[str, ast.FunctionDef | ast.AsyncFunctionDef]]:
    found = []
    for path in sorted(TESTS.rglob("test_*.py")):
        rel = path.relative_to(TESTS).as_posix()
        for node in ast.walk(ast.parse(path.read_text())):
            if isinstance(node, (ast.FunctionDef, ast.AsyncFunctionDef, ast.ClassDef)):
                found.append((rel, node))
    return found


def _is_unconditional_skip(decorator: ast.expr) -> bool:
    target = decorator.func if isinstance(decorator, ast.Call) else decorator
    return ast.unparse(target) == "pytest.mark.skip"


def _checks(node: ast.FunctionDef | ast.AsyncFunctionDef) -> bool:
    for inner in ast.walk(node):
        if isinstance(inner, (ast.Assert, ast.Raise)):
            return True
        if isinstance(inner, ast.Call):
            func = inner.func
            name = func.attr if isinstance(func, ast.Attribute) else getattr(func, "id", "")
            # A mock's assert_called_with, assert_not_awaited, ...
            if name in _CHECKING_CALLS or name.startswith("assert_"):
                return True
    return False


def test_no_test_is_skipped_unconditionally() -> None:
    skipped = [
        f"{rel}::{node.name}"
        for rel, node in _tests()
        if any(_is_unconditional_skip(d) for d in node.decorator_list)
    ]
    assert not skipped, "delete or fix these instead of skipping them:\n" + "\n".join(skipped)


def test_every_test_checks_something() -> None:
    unchecked = [
        f"{rel}::{node.name}"
        for rel, node in _tests()
        if not isinstance(node, ast.ClassDef)
        and node.name.startswith("test")
        and f"{rel}::{node.name}" not in _CHECKED_BY_NOT_RAISING
        and not _checks(node)
    ]
    assert not unchecked, "these tests pass whatever the code does:\n" + "\n".join(unchecked)


def test_every_allowlisted_test_still_calls_its_helper() -> None:
    by_key = {f"{rel}::{n.name}": n for rel, n in _tests() if not isinstance(n, ast.ClassDef)}
    stale = []
    for key, helper in _CHECKED_BY_NOT_RAISING.items():
        node = by_key.get(key)
        if node is None or helper not in ast.unparse(node):
            stale.append(key)
    assert not stale, "remove these from the list:\n" + "\n".join(stale)
