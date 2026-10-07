"""Creating datasets from public repository records, named by the repository.

The dataset page can fill an empty dataset from ENA, PRIDE, MetaboLights or a
BrAPI server, which asks for a dataset, a profile and a name before the record
that supplies all three is known. Here the identifier comes first: the importer
metaseed registers for the repository fetches the record, the record's title
names the dataset, and one identifier makes one dataset.

See docs/datasets/import-export.md, *From a public repository*.
"""

from __future__ import annotations

import logging
from dataclasses import dataclass
from typing import TYPE_CHECKING, Literal

import httpx
from metaseed import adapters
from starlette.concurrency import run_in_threadpool

from metaseed_hub.access import live_user
from metaseed_hub.repositories.datasets import DuplicateDatasetNameError, create_dataset
from metaseed_hub.ui.helpers.dataset_state import (
    ensure_dataset_facade_for_write,
    save_dataset_state,
)

if TYPE_CHECKING:
    from metaseed import MetaseedClient
    from metaseed.adapters import Action
    from sqlalchemy.ext.asyncio import AsyncSession

    from metaseed_hub.auth import TokenUser
    from metaseed_hub.models import Dataset

logger = logging.getLogger(__name__)

#: One submission fetches its identifiers one after another, and a repository
#: may take its whole timeout to answer each.
MAX_IDENTIFIERS = 20

#: Room for the identifier appended to a name that is already taken.
MAX_TITLE_LENGTH = 180

Status = Literal["imported", "empty", "duplicate", "failed", "not_checked"]


@dataclass(frozen=True)
class Repository:
    """A repository metaseed can import from, as the New Dataset screen offers it."""

    profile: str
    button: str
    input_label: str
    placeholder: str


@dataclass(frozen=True)
class Outcome:
    """What became of one identifier."""

    identifier: str
    status: Status
    dataset: Dataset | None = None
    detail: str = ""


def repositories() -> list[Repository]:
    """The repositories on offer, from metaseed's adapter registry."""
    found = []
    for profile in adapters.importable_profiles():
        action = source_import_action(profile)
        if action is not None:
            found.append(
                Repository(profile, action.label, action.input_label, action.input_placeholder)
            )
    return found


def parse_identifiers(text: str) -> list[str]:
    """The identifiers in a pasted block: one per line, blanks and repeats dropped."""
    return list(dict.fromkeys(line.strip() for line in text.splitlines() if line.strip()))


def record_title(client: MetaseedClient, identifier: str) -> str:
    """The title the imported root record carries, else the identifier."""
    entities = client.serialize().get("entities") or []
    root = next(
        (e for e in entities if not any(key.startswith("_parent") for key in e)),
        None,
    )
    title = str((root or {}).get("title") or "").strip()
    return title[:MAX_TITLE_LENGTH] or identifier


def source_import_action(profile: str) -> Action | None:
    """The registry's import action for ``profile``, or None.

    metaseed declares one per repository it can pull from. Resolving through
    the registry rather than naming the importers here means a new one reaches
    the hub by being declared upstream.
    """
    return adapters.import_action_for_profile(profile)


def run_source_import(profile: str, value: str) -> MetaseedClient:
    """Import ``value`` through the importer registered for ``profile``.

    Every import action takes a single string, though its meaning varies by
    repository: an accession for the archives, a server URL for BrAPI.

    Raises:
        LookupError: If no importer is registered for ``profile``.
    """
    action = source_import_action(profile)
    if action is None:
        raise LookupError(f"No source importer for profile '{profile}'")
    client: MetaseedClient = action.resolve()(value)
    return client


def _repository_did_not_answer(exc: Exception) -> bool:
    """Whether the failure is the repository's availability, not the identifier."""
    if isinstance(exc, httpx.TransportError):
        return True
    status = getattr(getattr(exc, "response", None), "status_code", None)
    return isinstance(status, int) and status >= 500


async def _create(
    session: AsyncSession,
    tenant_id: str,
    name: str,
    client: MetaseedClient,
    user: TokenUser,
) -> Dataset:
    creator = await live_user(session, user)
    dataset = await create_dataset(
        session,
        tenant_id=tenant_id,
        name=name,
        profile=client.profile,
        version=client.version,
        creator_id=creator.id if creator else None,
    )
    state = await ensure_dataset_facade_for_write(dataset, session)
    state.profile = client.profile
    state.version = client.version
    state.facade = client.facade
    state.invalidate_cache()
    await save_dataset_state(session, dataset, state, user)
    return dataset


async def import_record(
    session: AsyncSession,
    tenant_id: str,
    profile: str,
    identifier: str,
    user: TokenUser,
) -> Outcome:
    """Fetch one record and create the dataset it names.

    The caller commits. Nothing is created unless the outcome is ``imported``.

    Raises:
        LookupError: If no importer is registered for ``profile``.
    """
    try:
        # Off the event loop: the importer is blocking HTTP against an archive.
        client = await run_in_threadpool(run_source_import, profile, identifier)
    except LookupError:
        raise
    except Exception as exc:
        logger.exception("Repository import failed for %s:%s", profile, identifier)
        if _repository_did_not_answer(exc):
            return Outcome(identifier, "not_checked", detail=str(exc)[:200])
        return Outcome(identifier, "failed", detail=str(exc)[:200])

    if not client.serialize().get("entities"):
        return Outcome(identifier, "empty")

    title = record_title(client, identifier)
    for name in dict.fromkeys((title, f"{title} ({identifier})")):
        try:
            dataset = await _create(session, tenant_id, name, client, user)
        except DuplicateDatasetNameError:
            continue
        return Outcome(identifier, "imported", dataset=dataset)
    return Outcome(identifier, "duplicate", detail=title)
