"""A push runs the structural gates before it leaves the machine; CI runs the suite.

The hub's push hook was a hand-written ``.git/hooks/pre-push`` running
``make test`` -- the full five-minute suite, which the pull request's CI then
ran again -- and because it was not pre-commit's hook, the duplicate-code check
declared for the pre-push stage never ran at all. The repository now ships the
hook (metaseed #277, same shape): ``scripts/pre_push_tests.py`` runs the gate
tests that read files rather than the database, in seconds; the full suite runs
for a tag push, where the deploy timer does not wait for CI, or when
``METASEED_PUSH_FULL=1`` asks for it.
"""

from __future__ import annotations

import importlib.util
from pathlib import Path

import yaml

ROOT = Path(__file__).resolve().parents[1]
SCRIPT = ROOT / "scripts" / "pre_push_tests.py"


def _script():
    spec = importlib.util.spec_from_file_location("pre_push_tests", SCRIPT)
    module = importlib.util.module_from_spec(spec)
    spec.loader.exec_module(module)  # type: ignore[union-attr]
    return module


def _config() -> dict:
    return yaml.safe_load((ROOT / ".pre-commit-config.yaml").read_text(encoding="utf-8"))


def test_the_pre_push_hooks_are_installed_by_pre_commit_install() -> None:
    """Without this, ``pre-commit install`` sets up the commit hooks only and
    every pre-push hook is prose."""
    assert "pre-push" in _config().get("default_install_hook_types", [])


def test_the_hook_runs_the_script_not_the_whole_suite() -> None:
    hooks = [h for repo in _config()["repos"] for h in repo.get("hooks", []) if h["id"] == "pytest"]
    assert hooks, "no pytest hook in .pre-commit-config.yaml"
    assert "pre-push" in hooks[0].get("stages", [])
    assert "scripts/pre_push_tests.py" in hooks[0]["entry"], hooks[0]["entry"]


def test_a_branch_push_runs_the_gates() -> None:
    script = _script()
    assert script.scope({"PRE_COMMIT_REMOTE_BRANCH": "refs/heads/feature"}) == script.GATES_ONLY


def test_a_tag_push_runs_the_full_suite() -> None:
    script = _script()
    assert script.scope({"PRE_COMMIT_REMOTE_BRANCH": "refs/tags/v1.0.0"}) == script.FULL_SUITE
    assert script.scope({"METASEED_PUSH_FULL": "1"}) == script.FULL_SUITE


def test_every_gate_exists_and_needs_no_database() -> None:
    """A gate that took the ``session`` fixture would need Postgres and would
    not be the fast, run-anywhere check the push relies on."""
    missing = [p for p in _script().GATES if not (ROOT / p).exists()]
    assert not missing, missing
    using_the_database = [
        p
        for p in _script().GATES
        if "session" in (ROOT / p).read_text(encoding="utf-8").split("def test_", 1)[-1]
        and "session: AsyncSession" in (ROOT / p).read_text(encoding="utf-8")
    ]
    assert not using_the_database, using_the_database
