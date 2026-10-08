"""Creating datasets from public repository records, named by the repository.

The dataset page can fill an empty dataset from ENA, PRIDE, MetaboLights or a
BrAPI server, which asks for a dataset, a profile and a name before the record
that supplies all three is known. Here the identifier comes first: the importer
metaseed registers for the repository fetches the record, the record's title
names the dataset, and one identifier makes one dataset.

A submission is a job that runs in the background. An archive can take its
whole timeout to answer one identifier, so a list of them is minutes of work;
the job carries on after the person leaves the screen, records each outcome as
it is reached, and tells them when it is over.

See docs/datasets/import-export.md, *From a public repository*.
"""

from __future__ import annotations

import asyncio
import logging
from dataclasses import asdict, dataclass
from datetime import UTC, datetime, timedelta
from typing import TYPE_CHECKING, Any, Literal

import httpx
from metaseed import adapters
from sqlalchemy import select
from starlette.concurrency import run_in_threadpool

from metaseed_hub import notifications
from metaseed_hub.access import live_user
from metaseed_hub.models import ImportJob
from metaseed_hub.repositories.datasets import DuplicateDatasetNameError, create_dataset
from metaseed_hub.ui.helpers.dataset_state import (
    ensure_dataset_facade_for_write,
    save_dataset_state,
)

if TYPE_CHECKING:
    from collections.abc import Callable

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


#: How each outcome is counted in a job's summary, in this order.
STATUS_WORDS: dict[str, str] = {
    "imported": "imported",
    "empty": "nothing to import",
    "duplicate": "already imported",
    "failed": "failed",
    "not_checked": "not checked",
}

#: How long a finished job stays listed on the New Dataset screen.
RECENT = timedelta(hours=24)


def summary(outcomes: list[dict[str, Any]]) -> str:
    """``2 imported, 1 failed``: the outcomes counted, in a fixed order."""
    counts = {status: 0 for status in STATUS_WORDS}
    for outcome in outcomes:
        counts[outcome["status"]] = counts.get(outcome["status"], 0) + 1
    return ", ".join(f"{n} {STATUS_WORDS[status]}" for status, n in counts.items() if n)


def _as_record(outcome: Outcome) -> dict[str, Any]:
    record = asdict(outcome)
    dataset = record.pop("dataset")
    record["dataset_id"] = dataset.id if dataset else None
    record["name"] = dataset.name if dataset else ""
    return record


async def start_job(
    session: AsyncSession,
    *,
    tenant_id: str,
    user_id: str,
    profile: str,
    identifiers: list[str],
) -> ImportJob:
    """Record a submission as a job about to run. The caller commits."""
    job = ImportJob(
        tenant_id=tenant_id,
        user_id=user_id,
        profile=profile,
        identifiers=list(identifiers),
        outcomes=[],
        status="running",
    )
    session.add(job)
    await session.flush()
    return job


async def run_job(
    job_id: str, user: TokenUser, session_factory: Callable[[], AsyncSession]
) -> None:
    """Work through a job's identifiers, recording each outcome as it is reached.

    Every outcome is committed on its own, so the page polling the job sees it
    at once and a restart loses only the identifier in flight. A failure the
    importer did not foresee is an outcome too, never a job left running.

    Args:
        job_id: The job to run.
        user: Who started it, recorded as each dataset's author.
        session_factory: Opens a session of its own; the job outlives the
            request that started it.
    """
    async with session_factory() as session:
        job = await session.get(ImportJob, job_id)
        if job is None or job.status != "running":
            return
        tenant_id, profile = job.tenant_id, job.profile
        for index in range(len(job.outcomes), len(job.identifiers)):
            identifier = job.identifiers[index]
            try:
                outcome = await import_record(session, tenant_id, profile, identifier, user)
            except LookupError:
                outcome = Outcome(
                    identifier, "failed", detail="No importer is installed for that repository."
                )
            except Exception as exc:
                logger.exception("Import job %s failed on %s", job_id, identifier)
                await session.rollback()
                outcome = Outcome(identifier, "failed", detail=str(exc)[:200])
            # Fetched afresh: a refused dataset name rolls the session back,
            # which expires whatever it held before.
            job = await session.get(ImportJob, job_id)
            if job is None:
                return
            job.outcomes = [*job.outcomes, _as_record(outcome)]
            await session.commit()
        job = await session.get(ImportJob, job_id)
        if job is None:
            return
        job.status = "done"
        job.finished_at = datetime.now(UTC)
        plural = "s" if len(job.identifiers) != 1 else ""
        await notifications.import_finished(
            session,
            user_id=job.user_id,
            job_id=job.id,
            title=f"{len(job.identifiers)} {job.profile} record{plural}",
            summary=summary(job.outcomes),
        )
        await session.commit()


async def mark_interrupted(session: AsyncSession, job_ids: list[str] | None = None) -> None:
    """Close jobs that were running when the process stopped.

    The identifiers never reached are recorded as not checked, so the panel and
    the summary say what was and was not done. The caller commits.

    Args:
        session: Database session.
        job_ids: Only these jobs; every running job when omitted, which is
            what startup does, since nothing can be running then.
    """
    query = select(ImportJob).where(ImportJob.status == "running")
    if job_ids is not None:
        query = query.where(ImportJob.id.in_(job_ids))
    for job in (await session.execute(query)).scalars().all():
        job.outcomes = [
            *job.outcomes,
            *(
                {
                    "identifier": identifier,
                    "status": "not_checked",
                    "dataset_id": None,
                    "name": "",
                    "detail": "The hub restarted before this identifier was reached.",
                }
                for identifier in job.identifiers[len(job.outcomes) :]
            ),
        ]
        job.status = "interrupted"
        job.finished_at = datetime.now(UTC)


async def recent_jobs(session: AsyncSession, user_id: str) -> list[ImportJob]:
    """The person's jobs of the last day, newest first: what a returning person finds."""
    rows = await session.execute(
        select(ImportJob)
        .where(ImportJob.user_id == user_id, ImportJob.created_at >= datetime.now(UTC) - RECENT)
        .order_by(ImportJob.created_at.desc())
        .limit(10)
    )
    return list(rows.scalars().all())


async def job_of(session: AsyncSession, job_id: str, user_id: str) -> ImportJob | None:
    """The job, if it is this person's."""
    job = await session.get(ImportJob, job_id)
    return job if job is not None and job.user_id == user_id else None


class ImportJobRunner:
    """Runs import jobs as tasks of this process, and knows which are its own.

    Composed by whoever builds the application, with the session factory the
    jobs open their sessions from. The hub runs more than one worker process;
    a job belongs to the one that started it, its state lives in the database,
    and any process answers a poll.
    """

    def __init__(self, session_factory: Callable[[], AsyncSession]) -> None:
        self._session_factory = session_factory
        self._tasks: dict[str, asyncio.Task[None]] = {}

    def start(self, job_id: str, user: TokenUser) -> None:
        """Run the job from now on, independently of the request."""
        task = asyncio.create_task(run_job(job_id, user, self._session_factory))
        self._tasks[job_id] = task
        task.add_done_callback(lambda _task: self._tasks.pop(job_id, None))

    async def wait(self) -> None:
        """Until every job this process started is over."""
        await asyncio.gather(*list(self._tasks.values()), return_exceptions=True)

    async def shutdown(self) -> None:
        """Stop this process's jobs and record them as interrupted."""
        own = list(self._tasks)
        for task in list(self._tasks.values()):
            task.cancel()
        await asyncio.gather(*list(self._tasks.values()), return_exceptions=True)
        if own:
            async with self._session_factory() as session:
                await mark_interrupted(session, own)
                await session.commit()
