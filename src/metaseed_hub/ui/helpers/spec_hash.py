"""Recording which specification a dataset was written against, and noticing when it changes.

``dataset.version`` cannot answer the question. A draft specification is edited
in place, a published one can be withdrawn and republished, and two releases can
declare the same version with different content -- so a dataset that validated
when it was saved can start failing with nothing in the record to explain it.

The stored envelope therefore carries ``spec_hash``: metaseed's canonical
content hash of the profile spec in force when the dataset was written. On load
it is compared with the profile's current hash, and a difference is *reported*
alongside the validation issues. It is never enforced: the dataset still opens,
still edits, and still validates against the current specification. Drift
explains why a dataset that was finished now has issues; it is not itself one.

Envelopes written before the stamp existed have no ``spec_hash``. That means
unknown provenance, not unchanged, so nothing is reported for them -- which is
every dataset that exists today.

See `docs/datasets/index.md` and `docs/developer/architecture.md`.
"""

from __future__ import annotations

import logging
from typing import TYPE_CHECKING, Any

from metaseed.specs import content_hash
from metaseed.specs.versioning import HASH_ALGORITHM, SHORT_HASH_DIGITS

if TYPE_CHECKING:
    from metaseed.specs.schema import ProfileSpec
    from sqlalchemy.ext.asyncio import AsyncSession

    from metaseed_hub.models import Dataset

logger = logging.getLogger("metaseed_hub")

SPEC_HASH_KEY = "spec_hash"
"""Where the stamp lives in the stored ``dataset.data`` envelope."""

DRIFT_RULE = "spec_drift"
"""The ``rule`` a reported drift carries, alongside metaseed's own rule names."""

_SHORT_LENGTH = len(HASH_ALGORITHM) + 1 + SHORT_HASH_DIGITS
"""Characters of a hash worth showing, matching metaseed's ``short_hash``."""


async def dataset_profile_spec(session: AsyncSession, dataset: Dataset) -> ProfileSpec | None:
    """The specification the dataset's profile resolves to right now.

    Resolves the same three ways ``ensure_dataset_facade`` does -- draft spec,
    published spec, built-in profile -- so the hash compared on load is the hash
    of the specification actually used to load.

    Args:
        session: Database session.
        dataset: The dataset whose profile to resolve.

    Returns:
        The current specification, or None if it cannot be resolved. None is
        returned rather than raised because this is a provenance side-channel:
        a profile that cannot be loaded is already reported by the caller's own
        validation path, and failing here would turn a report into a crash.
    """
    from metaseed_hub.models import Spec, SpecDraft
    from metaseed_hub.ui.spec_builder.state import dict_to_spec

    try:
        stored: dict[str, Any] | None = None
        if dataset.spec_draft_id:
            draft = await session.get(SpecDraft, dataset.spec_draft_id)
            stored = draft.spec_data if draft else None
        elif dataset.spec_id:
            published = await session.get(Spec, dataset.spec_id)
            stored = published.spec_data if published else None
        else:
            from metaseed.specs.loader import SpecLoader

            profile_spec: ProfileSpec = SpecLoader().load_profile(dataset.version, dataset.profile)
            return profile_spec

        if not stored:
            return None
        # Both drafts and published specs store a SpecBuilderState envelope with
        # the ProfileSpec under "spec"; older rows stored the spec directly.
        return dict_to_spec(stored.get("spec", stored))
    except Exception as exc:
        logger.debug(
            "No content hash for dataset %s: its profile %r could not be resolved (%s)",
            dataset.id,
            dataset.profile,
            exc,
        )
        return None


async def dataset_spec_hash(session: AsyncSession, dataset: Dataset) -> str | None:
    """The content hash of the specification the dataset currently loads with.

    Args:
        session: Database session.
        dataset: The dataset whose profile to hash.

    Returns:
        ``"sha256:<64 hex digits>"``, or None if the profile cannot be resolved.
    """
    spec = await dataset_profile_spec(session, dataset)
    return content_hash(spec) if spec is not None else None


def stamp_spec_hash(data: dict[str, Any], spec_hash: str | None) -> dict[str, Any]:
    """Record a specification's hash in a dataset envelope about to be stored.

    Args:
        data: The serialized ``{profile, version, tree}`` envelope.
        spec_hash: The hash to record, or None if it could not be computed.

    Returns:
        The envelope with the hash recorded. An unknown hash leaves the envelope
        alone rather than writing a null: absent means "unknown provenance",
        which is exactly what it would be.
    """
    if spec_hash is None:
        return data
    return {**data, SPEC_HASH_KEY: spec_hash}


async def spec_drift_message(session: AsyncSession, dataset: Dataset) -> str | None:
    """Report that the dataset's specification changed since it was written.

    Args:
        session: Database session.
        dataset: The dataset being loaded.

    Returns:
        A message for the validation report, or None when there is nothing to
        report -- which covers three distinct cases, all correctly silent: the
        specification is unchanged, the dataset carries no stamp (unknown
        provenance, not unchanged), or the specification cannot be resolved at
        all (nothing to compare against is not evidence of a change).
    """
    stamped = (dataset.data or {}).get(SPEC_HASH_KEY)
    if not stamped:
        return None
    current = await dataset_spec_hash(session, dataset)
    if current is None or current == stamped:
        return None
    return (
        f"This dataset was written against {dataset.profile} "
        f"{str(stamped)[:_SHORT_LENGTH]}, which is now "
        f"{current[:_SHORT_LENGTH]}. The specification changed after the dataset "
        "was saved, so entities that were complete then may be missing something "
        "now. Nothing was blocked: check the issues below and re-save to record "
        "the current specification."
    )
