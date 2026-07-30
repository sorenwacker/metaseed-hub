"""Spec-builder edits must not be silently discarded by a stale cache.

A user reported edits that reported success and then vanished. Drafts are held
in a process-local ``StateCache`` and each edit rewrites the whole
``spec_data`` blob from the cached copy, but the cache was never checked against
the stored row. Production runs uvicorn with two workers, each with its own
cache, so an edit served by one worker was routinely overwritten by the next
edit served by the other -- and both reported success.

These tests exercise one process, which is enough: the cache is what goes stale,
and any writer that is not the cache holder reproduces it.
"""

from __future__ import annotations

import pytest
from metaseed.specs.schema import EntityDefSpec, ProfileSpec
from sqlalchemy.ext.asyncio import AsyncSession

from metaseed_hub.models import SpecDraft
from metaseed_hub.ui.spec_builder.access import (
    DraftConflictError,
    create_new_draft,
    load_state_for_draft,
    save_state_to_draft,
)
from metaseed_hub.ui.spec_builder.cache import state_cache
from metaseed_hub.ui.spec_builder.state import SpecBuilderState
from tests.factories import make_spec_draft, make_tenant, make_user


def _spec(*entity_names: str) -> ProfileSpec:
    return ProfileSpec(
        name="demo",
        version="0.1",
        root_entity=entity_names[0] if entity_names else "Investigation",
        entities={
            name: EntityDefSpec(description=f"{name} entity", fields=[]) for name in entity_names
        },
    )


async def _draft(session: AsyncSession) -> tuple[SpecDraft, str]:
    """A saved draft owned by a user, plus that user's database id."""
    tenant = make_tenant()
    session.add(tenant)
    await session.flush()
    user = make_user(tenant=tenant)
    session.add(user)
    await session.flush()
    state = SpecBuilderState(spec=_spec("Investigation"))
    draft = make_spec_draft(
        tenant=tenant,
        user=user,
        name="demo",
        spec_data=state.to_dict(),
    )
    session.add(draft)
    await session.commit()
    state_cache.pop(draft.id)
    return draft, user.id


async def _write_as_another_worker(session: AsyncSession, draft: SpecDraft, entity: str) -> None:
    """Persist an edit the way a second worker would: from the stored row.

    It deliberately does not touch this process's cache, which is exactly the
    situation a second uvicorn worker creates.
    """
    state = SpecBuilderState.from_dict(draft.spec_data)
    assert state.spec is not None
    state.spec.entities[entity] = EntityDefSpec(description=f"{entity} entity", fields=[])
    draft.spec_data = state.to_dict()
    from sqlalchemy.orm.attributes import flag_modified

    flag_modified(draft, "spec_data")
    await session.commit()


async def test_a_stale_cache_does_not_hide_an_edit_made_elsewhere(
    session: AsyncSession,
) -> None:
    """The load path must notice the row changed and rebuild from it.

    Before the fix this returned the cached copy, so the next save wrote it back
    over the other edit -- reporting success while destroying the change.
    """
    draft, user_id = await _draft(session)
    await load_state_for_draft(session, draft.id, user_id)  # populates the cache

    await _write_as_another_worker(session, draft, "Study")

    state, _ = await load_state_for_draft(session, draft.id, user_id)

    assert state.spec is not None
    assert "Study" in state.spec.entities, "the load path served a stale cached draft"


async def test_saving_after_someone_else_edited_does_not_destroy_their_work(
    session: AsyncSession,
) -> None:
    """The whole blob is rewritten on save, so a stale writer must be refused."""
    draft, user_id = await _draft(session)
    stale_state, _ = await load_state_for_draft(session, draft.id, user_id)
    assert stale_state.spec is not None

    await _write_as_another_worker(session, draft, "Study")

    # The stale holder now edits and saves its own copy.
    stale_state.spec.entities["Assay"] = EntityDefSpec(description="Assay", fields=[])
    with pytest.raises(DraftConflictError):
        await save_state_to_draft(session, stale_state, draft, expected_revision=None)

    await session.refresh(draft)
    assert "Study" in draft.spec_data["spec"]["entities"], "the other edit was overwritten"


async def test_a_normal_edit_still_saves(session: AsyncSession) -> None:
    """The conflict check must not block the ordinary single-editor case."""
    draft, user_id = await _draft(session)
    state, draft = await load_state_for_draft(session, draft.id, user_id)
    assert state.spec is not None

    state.spec.entities["Study"] = EntityDefSpec(description="Study", fields=[])
    await save_state_to_draft(session, state, draft)

    await session.refresh(draft)
    assert "Study" in draft.spec_data["spec"]["entities"]


def test_the_user_is_told_rather_than_shown_a_success() -> None:
    """The refusal must reach the user as an error notification.

    Raising without a handler would surface as a 500, which reads as "the app
    broke" rather than "your edit was not applied, here is why".
    """
    from metaseed_hub.ui.app import create_hub_app
    from metaseed_hub.ui.spec_builder.access import (
        DraftConflictError,
        handle_draft_conflict,
    )

    app = create_hub_app()
    assert DraftConflictError in app.exception_handlers, (
        "a refused save would surface as an unhandled 500"
    )

    response = handle_draft_conflict(None, DraftConflictError("draft-1"))
    body = response.body.decode()

    assert response.status_code == 409
    assert "notification-error" in body
    assert "success" not in body.lower()
    assert "Reload" in body, "the message must say what to do next"


async def test_a_new_draft_is_cached_at_its_row_revision(session: AsyncSession) -> None:
    """create_new_draft must tag its cache write with the row revision.

    An untagged entry never matches the revision check, so the very next load
    rebuilt from the row and the cache write was dead weight; worse, a save
    from that entry would resolve expected_revision to None and be refused as
    a spurious conflict.
    """
    tenant = make_tenant()
    session.add(tenant)
    await session.flush()
    user = make_user(tenant=tenant)
    session.add(user)
    await session.commit()

    draft = await create_new_draft(session, user.id, tenant.id, "demo", _spec("Investigation"))

    assert state_cache.revision(draft.id) == draft.updated_at


async def test_saving_a_state_with_no_spec_is_refused(session: AsyncSession) -> None:
    """A spec-less state has nothing meaningful to persist.

    The old code path deleted the draft row instead -- before the conflict
    check, so it would have destroyed intervening edits had it ever run.
    """
    draft, user_id = await _draft(session)
    state, draft = await load_state_for_draft(session, draft.id, user_id)
    state.spec = None

    with pytest.raises(ValueError, match="no spec"):
        await save_state_to_draft(session, state, draft)

    await session.refresh(draft)
    assert draft.spec_data["spec"] is not None, "the draft row must survive untouched"


async def test_consecutive_edits_by_the_same_holder_keep_working(
    session: AsyncSession,
) -> None:
    """Each save must leave the cache able to save again, or the second edit of
    a session would fail with a spurious conflict."""
    draft, user_id = await _draft(session)

    for entity in ("Study", "Assay", "Sample"):
        state, draft = await load_state_for_draft(session, draft.id, user_id)
        assert state.spec is not None
        state.spec.entities[entity] = EntityDefSpec(description=entity, fields=[])
        await save_state_to_draft(session, state, draft)

    await session.refresh(draft)
    stored = draft.spec_data["spec"]["entities"]
    assert {"Investigation", "Study", "Assay", "Sample"} <= set(stored)
