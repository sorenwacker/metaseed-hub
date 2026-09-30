"""Simultaneous writes to one dataset are serialised, not lost.

Every mutation loads the whole stored tree, changes it in memory and writes
it back. Two requests that arrived together loaded the same state, so the
later write replaced the earlier one; both also computed the next version
number from the same base, so the second insert failed on
``uq_dataset_versions_number`` and its request ended in a 500 (production,
260923). The lock lives in the write loader. These tests drive several
writers through it at once against Postgres -- the web loader and the MCP
editing context, the two production surfaces -- and the gate keeps every
path that saves on a locking loader.
"""

from __future__ import annotations

import ast
import asyncio
import re
from pathlib import Path
from typing import Any
from uuid import uuid4

import pytest
from sqlalchemy import select
from sqlalchemy.ext.asyncio import AsyncEngine, AsyncSession, async_sessionmaker

from metaseed_hub.mcp import _editing
from metaseed_hub.models import Dataset, DatasetVersion, User
from metaseed_hub.ui.dependencies import tenant_slug_for
from metaseed_hub.ui.helpers.dataset_state import (
    ensure_dataset_facade,
    ensure_dataset_facade_for_write,
    save_dataset_state,
)
from metaseed_hub.ui.helpers.tree import add_entity_node, update_entity_node
from tests.factories import make_dataset, make_tenant, make_user

pytestmark = pytest.mark.asyncio

SRC = Path(__file__).resolve().parents[1] / "src" / "metaseed_hub"
STATE_MODULE = SRC / "ui" / "helpers" / "dataset_state.py"

# One edit per writer, each to a different field, so a lost update shows as a
# missing field rather than as one value legitimately winning over another.
EDITS = {
    "title": "renamed",
    "description": "described",
    "license": "CC-BY-4.0",
    "miappe_version": "1.1",
}


@pytest.fixture
async def investigation(session: AsyncSession) -> tuple[str, str, str]:
    """A MIAPPE 1.1 dataset holding one Investigation: (dataset id, node id, user id)."""
    sub = f"writers-{uuid4().hex[:8]}"
    tenant = make_tenant(slug=tenant_slug_for(sub))
    session.add(tenant)
    await session.flush()
    user = make_user(tenant=tenant, keycloak_id=sub, email=f"{sub}@example.org")
    session.add(user)
    dataset = make_dataset(
        tenant=tenant, name=f"shared-{uuid4().hex[:6]}", profile="miappe", version="1.1"
    )
    session.add(dataset)
    await session.commit()

    state = await ensure_dataset_facade_for_write(dataset, session)
    node = add_entity_node(state, "Investigation", {"unique_id": "INV-1", "title": "original"})
    await save_dataset_state(session, dataset, state, None)
    return dataset.id, node.id, user.id


def _maker(session: AsyncSession) -> async_sessionmaker[AsyncSession]:
    engine = session.bind
    assert isinstance(engine, AsyncEngine)
    return async_sessionmaker(engine, expire_on_commit=False)


async def _stored(session: AsyncSession, dataset_id: str) -> tuple[dict[str, Any], list[int]]:
    """The root entity's stored values and the version numbers, read fresh."""
    async with _maker(session)() as fresh:
        dataset = (
            await fresh.execute(select(Dataset).where(Dataset.id == dataset_id))
        ).scalar_one()
        numbers = (
            await fresh.execute(
                select(DatasetVersion.version_number)
                .where(DatasetVersion.dataset_id == dataset_id)
                .order_by(DatasetVersion.version_number)
            )
        ).scalars()
        return dataset.data["tree"][0]["data"], list(numbers)


async def _browser_edit(
    maker: async_sessionmaker[AsyncSession], dataset_id: str, node_id: str, field: str, value: str
) -> None:
    """What every table route does: load for writing, change one field, save."""
    async with maker() as s:
        dataset = (await s.execute(select(Dataset).where(Dataset.id == dataset_id))).scalar_one()
        state = await ensure_dataset_facade_for_write(dataset, s)
        node = state.nodes_by_id[node_id]
        helper = getattr(state.get_or_create_facade(), node.entity_type)
        values = node.instance.model_dump(exclude_none=True)
        values[field] = value
        update_entity_node(state, node_id, values, helper)
        await save_dataset_state(s, dataset, state, None)


async def _agent_edit(
    maker: async_sessionmaker[AsyncSession],
    dataset_id: str,
    node_id: str,
    user_id: str,
    field: str,
    value: str,
) -> None:
    """What every MCP entity tool does: edit inside the ``_editing`` context."""
    async with maker() as s:
        dataset = (await s.execute(select(Dataset).where(Dataset.id == dataset_id))).scalar_one()
        user = await s.get(User, user_id)
        async with _editing(s, dataset, user) as client:
            # Merged as the update_entity tool does: metaseed's update_entity
            # overwrites the entity's values wholesale.
            merged = {**client.get_entity(node_id).data, field: value}
            client.update_entity(node_id, merged, skip_validation=True)


async def test_simultaneous_browser_edits_all_survive_and_are_numbered_in_order(
    session: AsyncSession, investigation: tuple[str, str, str]
) -> None:
    """Before the lock: one edit survived and the others died on the version number."""
    dataset_id, node_id, _user_id = investigation
    maker = _maker(session)

    await asyncio.gather(
        *(_browser_edit(maker, dataset_id, node_id, field, value) for field, value in EDITS.items())
    )

    values, numbers = await _stored(session, dataset_id)
    assert {field: values.get(field) for field in EDITS} == EDITS
    assert numbers == [1, 2, 3, 4, 5], "the setup save plus one version per writer, consecutive"


async def test_simultaneous_agent_edits_all_survive(
    session: AsyncSession, investigation: tuple[str, str, str]
) -> None:
    dataset_id, node_id, user_id = investigation
    maker = _maker(session)
    edits = dict(list(EDITS.items())[:2])

    await asyncio.gather(
        *(
            _agent_edit(maker, dataset_id, node_id, user_id, field, value)
            for field, value in edits.items()
        )
    )

    values, numbers = await _stored(session, dataset_id)
    assert {field: values.get(field) for field in edits} == edits
    assert numbers == sorted(numbers) and len(numbers) == len(set(numbers))


async def test_a_browser_edit_and_an_agent_edit_at_once_both_survive(
    session: AsyncSession, investigation: tuple[str, str, str]
) -> None:
    dataset_id, node_id, user_id = investigation
    maker = _maker(session)

    await asyncio.gather(
        _browser_edit(maker, dataset_id, node_id, "title", "renamed"),
        _agent_edit(maker, dataset_id, node_id, user_id, "description", "described"),
    )

    values, _numbers = await _stored(session, dataset_id)
    assert values.get("title") == "renamed"
    assert values.get("description") == "described"


async def test_the_unlocked_loader_is_still_what_reads_use(
    session: AsyncSession, investigation: tuple[str, str, str]
) -> None:
    """Reads must not queue behind writers; the plain loader takes no lock."""
    dataset_id, _node_id, _user_id = investigation
    dataset = (await session.execute(select(Dataset).where(Dataset.id == dataset_id))).scalar_one()
    source = STATE_MODULE.read_text(encoding="utf-8")
    plain = source[
        source.index("async def ensure_dataset_facade(") : source.index(
            "async def ensure_dataset_facade_for_write("
        )
    ]

    state = await ensure_dataset_facade(dataset, session)

    assert state.nodes_by_id
    assert "with_for_update" not in plain and "lock_dataset_for_write" not in plain


# --- gate ---------------------------------------------------------------------

SAVES = {"save_dataset_state", "record_version"}
LOCKS = {"lock_dataset_for_write", "ensure_dataset_facade_for_write"}
STATE_PARAMS = {"state", "dataset_state"}


def _own_calls(node: ast.AST) -> set[str]:
    """Names called directly in ``node``, not inside a nested def or class."""
    calls: set[str] = set()
    pending = list(ast.iter_child_nodes(node))
    while pending:
        child = pending.pop()
        if isinstance(child, (ast.FunctionDef, ast.AsyncFunctionDef, ast.ClassDef)):
            continue
        if isinstance(child, ast.Call):
            func = child.func
            calls.add(func.attr if isinstance(func, ast.Attribute) else getattr(func, "id", ""))
        pending.extend(ast.iter_child_nodes(child))
    return calls


def _units() -> list[tuple[str, set[str], set[str]]]:
    """(label, calls, parameters) per class or per function outside a class.

    A class is one unit because a method that saves relies on a method that
    loaded; a nested function is its own unit because the MCP tools are all
    nested in one factory.
    """
    units = []
    for path in SRC.rglob("*.py"):
        if path == STATE_MODULE:
            continue
        tree = ast.parse(path.read_text(encoding="utf-8"))
        in_class: set[ast.AST] = set()
        for node in ast.walk(tree):
            if isinstance(node, ast.ClassDef):
                calls = {c for c in ast.walk(node) if isinstance(c, ast.Call)}
                names = {
                    c.func.attr if isinstance(c.func, ast.Attribute) else getattr(c.func, "id", "")
                    for c in calls
                }
                units.append(
                    (f"{path.relative_to(SRC)}:{node.lineno} class {node.name}", names, set())
                )
                in_class |= {
                    n
                    for n in ast.walk(node)
                    if isinstance(n, (ast.FunctionDef, ast.AsyncFunctionDef))
                }
        for node in ast.walk(tree):
            if isinstance(node, (ast.FunctionDef, ast.AsyncFunctionDef)) and node not in in_class:
                params = {a.arg for a in node.args.args + node.args.kwonlyargs}
                units.append(
                    (f"{path.relative_to(SRC)}:{node.lineno} {node.name}", _own_calls(node), params)
                )
    return units


def test_the_gate_sees_the_savers() -> None:
    savers = [label for label, calls, _ in _units() if calls & SAVES]
    assert any("table.py" in label for label in savers)
    assert any("mcp/__init__.py" in label for label in savers)


def test_every_saver_holds_the_dataset_lock() -> None:
    offenders = [
        label
        for label, calls, params in _units()
        if calls & SAVES and not (calls & LOCKS) and not (params & STATE_PARAMS)
    ]
    assert not offenders, (
        "saves without lock_dataset_for_write/ensure_dataset_facade_for_write:\n"
        + "\n".join(offenders)
    )


def test_versions_are_numbered_in_one_place() -> None:
    offenders = []
    for path in SRC.rglob("*.py"):
        if path == STATE_MODULE or path.parts[-2] == "models":
            continue
        source = path.read_text(encoding="utf-8")
        for match in re.finditer(r"DatasetVersion\(|max\(DatasetVersion\.version_number", source):
            offenders.append(
                f"{path.relative_to(SRC)}:{source.count(chr(10), 0, match.start()) + 1}"
            )
    assert not offenders, "use record_version:\n" + "\n".join(offenders)
    assert "DatasetVersion(" in STATE_MODULE.read_text(encoding="utf-8")
