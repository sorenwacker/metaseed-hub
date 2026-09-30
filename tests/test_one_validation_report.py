"""The web Validate panel and the MCP report take their issues from one place.

The panel re-created each entity with Pydantic and reported what that raised:
a wrong type, and nothing else. A missing required field, a profile rule, a
reference to a study that does not exist -- all reported by metaseed's
validator, which the MCP path asked -- never reached the person clicking
Validate. Both paths now take their issues from ``validation_issues``, which
runs the validator in a worker thread; the gate keeps a second source from
coming back.
"""

from __future__ import annotations

import ast
from pathlib import Path
from unittest.mock import Mock
from uuid import uuid4

import pytest
from metaseed import MetaseedClient
from sqlalchemy.ext.asyncio import AsyncSession

from metaseed_hub.auth import TokenUser
from metaseed_hub.mcp import _validation_report
from metaseed_hub.models import Dataset
from metaseed_hub.ui.dependencies import tenant_slug_for
from metaseed_hub.ui.helpers import CSRF_TOKEN_COOKIE, get_or_create_csrf_token
from metaseed_hub.ui.routes.dataset.editor import dataset_validate
from tests.factories import make_dataset, make_tenant, make_user

pytestmark = pytest.mark.asyncio

SRC = Path(__file__).resolve().parents[1] / "src" / "metaseed_hub"
REPORT_MODULE = SRC / "ui" / "helpers" / "validation_report.py"
# These call SpecBuilder.validate(), which checks a specification, not a dataset.
SPEC_AUTHORING = (
    SRC / "mcp" / "_spec_tools.py",
    SRC / "mcp" / "_rule_tools.py",
    SRC / "ui" / "spec_builder",
)

# The three planted errors, one per layer the panel used to miss or catch.
MALFORMED_DATE = "15.03.2024"
MISSING_STUDY = "STU-9"


def _planted_tree() -> dict:
    """A MIAPPE 1.2 tree with a malformed date, a missing required field and a broken reference."""
    client = MetaseedClient("miappe", "1.2")
    inv = client.create_entity(
        "Investigation",
        {"unique_id": "INV-1", "title": "T", "submission_date": MALFORMED_DATE},
        skip_validation=True,
    )
    study = client.create_entity(
        "Study",
        {"unique_id": "STU-1", "investigation_id": "INV-1", "title": "S"},
        parent_id=inv.id,
        skip_validation=True,
    )
    client.create_entity(
        "ObservationUnit",
        {"unique_id": "OU-1", "study_id": MISSING_STUDY},
        parent_id=study.id,
        skip_validation=True,
    )
    client.create_entity(
        "ObservedVariable", {"unique_id": "VAR-1"}, parent_id=study.id, skip_validation=True
    )
    return client.serialize(format="tree")


async def _planted_dataset(session: AsyncSession) -> tuple[Dataset, TokenUser]:
    sub = f"validate-{uuid4().hex[:8]}"
    tenant = make_tenant(slug=tenant_slug_for(sub))
    session.add(tenant)
    await session.flush()
    session.add(make_user(tenant=tenant, keycloak_id=sub, email=f"{sub}@example.org"))
    dataset = make_dataset(tenant=tenant, profile="miappe", version="1.2", data=_planted_tree())
    session.add(dataset)
    await session.commit()
    return dataset, TokenUser(sub=sub, email=f"{sub}@example.org", name="V", roles=[])


def _csrf_request() -> Mock:
    request = Mock()
    request.cookies = {}
    request.headers = {}
    token = get_or_create_csrf_token(request)
    request.cookies = {CSRF_TOKEN_COOKIE: token}
    request.headers = {"X-CSRF-Token": token}
    return request


async def test_the_panel_reports_all_three_planted_errors(session: AsyncSession) -> None:
    """Before: only the date."""
    dataset, user = await _planted_dataset(session)

    html = (await dataset_validate(_csrf_request(), dataset.id, session, user)).body.decode()

    assert "submission_date" in html and "valid date" in html
    assert "name" in html and "required" in html
    assert MISSING_STUDY in html and "study_id" in html
    assert "3 of 4 entities have issues" in html


async def test_the_mcp_report_reports_the_same_three(session: AsyncSession) -> None:
    dataset, _user = await _planted_dataset(session)

    report = await _validation_report(session, dataset)

    assert {"constraint", "required_fields", "reference_integrity"} <= {
        i["rule"] for i in report["issues"]
    }
    assert report["valid"] is False


async def test_both_paths_name_the_same_entities(session: AsyncSession) -> None:
    """The panel links each issue to its node; the MCP report carries the node id."""
    dataset, user = await _planted_dataset(session)

    html = (await dataset_validate(_csrf_request(), dataset.id, session, user)).body.decode()
    report = await _validation_report(session, dataset)

    for entity_id in {i["entity_id"] for i in report["issues"] if i["entity_id"]}:
        assert f"/hub/datasets/{dataset.id}/entity/{entity_id}" in html


# --- gate -------------------------------------------------------------------


def _validate_calls() -> list[str]:
    """``<something>.validate()`` with no arguments, outside the report helper."""
    found = []
    for path in SRC.rglob("*.py"):
        if path == REPORT_MODULE or any(
            path == excluded or excluded in path.parents for excluded in SPEC_AUTHORING
        ):
            continue
        for node in ast.walk(ast.parse(path.read_text(encoding="utf-8"))):
            if (
                isinstance(node, ast.Call)
                and isinstance(node.func, ast.Attribute)
                and node.func.attr == "validate"
                and not node.args
                and not node.keywords
            ):
                found.append(f"{path.relative_to(SRC)}:{node.lineno}")
    return found


def _calls_in(path: Path, function: str) -> set[str]:
    tree = ast.parse(path.read_text(encoding="utf-8"))
    for node in ast.walk(tree):
        if isinstance(node, (ast.FunctionDef, ast.AsyncFunctionDef)) and node.name == function:
            return {
                c.func.attr if isinstance(c.func, ast.Attribute) else getattr(c.func, "id", "")
                for c in ast.walk(node)
                if isinstance(c, ast.Call)
            }
    raise AssertionError(f"{function} not found in {path}")


def test_the_gate_sees_the_helper() -> None:
    """The helper hands ``client.validate`` to the worker thread uncalled, so
    the call scan cannot see it; the source must."""
    assert "run_in_threadpool(client.validate)" in REPORT_MODULE.read_text(encoding="utf-8")


def test_the_validator_is_called_only_in_the_report_helper() -> None:
    offenders = _validate_calls()
    assert not offenders, "use validation_issues:\n" + "\n".join(offenders)


def test_both_reporting_paths_use_the_helper() -> None:
    assert "validation_issues" in _calls_in(
        SRC / "ui" / "routes" / "dataset" / "editor.py", "dataset_validate"
    )
    assert "validation_issues" in _calls_in(SRC / "mcp" / "__init__.py", "_validation_report")
