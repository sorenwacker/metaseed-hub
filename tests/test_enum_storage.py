"""Every persisted enum column stores the lowercase member value.

Roles and status previously stored the uppercase member name while reactions
stored the value. This pins the consistent by-value storage the models now
declare (and migration 260726_enum_store_by_value applies to existing databases).
"""

import pytest
from sqlalchemy import text
from sqlalchemy.ext.asyncio import AsyncSession

_EXPECTED = {
    "datasetrole": ["owner", "curator", "viewer"],
    "specrole": ["owner", "curator", "viewer"],
    "specdraftrole": ["owner", "editor", "viewer"],
    "specstatus": ["draft", "published", "archived"],
    "reactiontype": ["like", "dislike"],
}


@pytest.mark.asyncio
@pytest.mark.parametrize("type_name,expected", list(_EXPECTED.items()))
async def test_enum_labels_are_lowercase_values(
    session: AsyncSession, type_name: str, expected: list[str]
) -> None:
    rows = await session.execute(
        text(
            "SELECT e.enumlabel FROM pg_enum e "
            "JOIN pg_type t ON t.oid = e.enumtypid "
            "WHERE t.typname = :name ORDER BY e.enumsortorder"
        ),
        {"name": type_name},
    )
    labels = [r[0] for r in rows]
    assert labels == expected
