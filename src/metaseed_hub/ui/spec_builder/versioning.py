"""Profile versions and content hashes, where the hub has to act on them.

What a profile version *means* is metaseed's: ``MAJOR.MINOR``, MAJOR when a
dataset valid under the previous version may now fail, and
:mod:`metaseed.specs.compare` decides which edits force which bump. None of that
is restated here.

The hub adds two things metaseed cannot:

* **A release gate.** Saving a draft is not a claim about compatibility -- an
  author is allowed to be mid-thought -- so metaseed's save path is the wrong
  place to check one. Publishing *is* the claim, and publishing happens here.
* **A way back from a stored version that predates the rule.** Rows written
  before ``MAJOR.MINOR`` was enforced hold values such as ``v1.0`` or ``draft``,
  and a spec whose version fails validation cannot be deserialized at all. That
  must be a fixable problem naming the value and the rule, not a server error on
  a page the author can no longer open.

See `docs/spec-builder/publishing.md`.
"""

from __future__ import annotations

import html
import logging
from dataclasses import dataclass
from typing import TYPE_CHECKING, Any

from fastapi.responses import HTMLResponse
from metaseed.specs import compare_specs, declared_bump, required_bump
from metaseed.specs.versioning import check_profile_version, parse_profile_version

if TYPE_CHECKING:
    from fastapi import Request
    from metaseed.specs.schema import ProfileSpec
    from sqlalchemy.ext.asyncio import AsyncSession
    from starlette.responses import Response

    from metaseed_hub.models import Spec

logger = logging.getLogger(__name__)

_BUMP_RANK = {"downgrade": -1, "none": 0, "minor": 1, "major": 2}
"""How much compatibility each bump claims, so claims can be compared."""


class SpecVersionError(Exception):
    """A stored profile version is not ``MAJOR.MINOR``.

    Carries the offending value and metaseed's own explanation of the rule, so
    whatever surfaces it can tell the author what to change. Raised instead of
    letting the Pydantic ``ValidationError`` escape, because that reaches the
    user as a 500 on a page they then cannot open to fix it.

    Attributes:
        version: The stored value, exactly as found.
        subject: What holds it -- a profile name -- or None if unknown.
        problem: metaseed's message naming the value and the rule.
    """

    def __init__(self, *, version: str, subject: str | None = None) -> None:
        problem = check_profile_version(version)
        if problem is None:
            raise ValueError(f"{version!r} is a valid profile version; there is no problem")
        self.version = version
        self.subject = subject
        self.problem = problem
        where = f"{subject}: " if subject else ""
        super().__init__(f"{where}{problem}")

    @classmethod
    def in_stored_spec(cls, data: dict[str, Any]) -> SpecVersionError | None:
        """The problem in a stored spec payload's version, if that is what is wrong.

        Args:
            data: A serialized ``ProfileSpec``.

        Returns:
            The problem, or None if the version is fine and the payload fails
            validation for some other reason.
        """
        version = data.get("version")
        if version is None:
            return None
        text = str(version)
        if check_profile_version(text) is None:
            return None
        name = data.get("name")
        return cls(version=text, subject=str(name) if name else None)


def handle_spec_version_error(request: Request, exc: Exception) -> Response:
    """Render a malformed stored version as a fixable problem, not a 500.

    Registered app-wide so every route that deserializes a stored spec reports
    it the same way, rather than each one remembering to catch it.

    Args:
        request: The request that could not be served. Unused; the signature is
            Starlette's.
        exc: The raised :class:`SpecVersionError`.

    Returns:
        A 400 page naming the stored value and the rule it breaks.
    """
    del request
    assert isinstance(exc, SpecVersionError)
    logger.warning("Refusing to load a spec whose stored version is %r", exc.version)
    return HTMLResponse(
        "<div class='notification notification-error'>"
        "<strong>This specification's version needs fixing before it can be "
        f"opened.</strong><p>{html.escape(exc.problem)}</p>"
        "<p>Correct it under Profile Settings in the draft editor, or ask the "
        "person who published it to.</p>"
        "</div>",
        status_code=400,
    )


@dataclass(frozen=True)
class BumpRefusal:
    """A refused publish, in the terms the author has to act on.

    Attributes:
        profile: The profile name being published.
        published_version: The latest already-published version it was compared
            against.
        declared_version: The version the draft declares.
        declared: What that version pair claims -- ``minor``, ``none``, or
            ``downgrade``.
        required: What the content actually requires.
        suggested_version: The version to declare instead.
        breaking: One line per breaking change, as metaseed renders them.
    """

    profile: str
    published_version: str
    declared_version: str
    declared: str
    required: str
    suggested_version: str
    breaking: tuple[str, ...]

    @property
    def message(self) -> str:
        """One-line rendering, for logs and non-HTML callers."""
        changes = "; ".join(self.breaking) or "no breaking change was itemized"
        return (
            f"{self.profile} {self.declared_version} claims a {self.declared} change over "
            f"{self.published_version}, but the content requires a {self.required} bump "
            f"({changes}). Declare {self.suggested_version} instead."
        )


def _next_version(published: str, bump: str) -> str:
    """The lowest version that honestly claims ``bump`` over ``published``."""
    major, minor = parse_profile_version(published)
    return f"{major + 1}.0" if bump == "major" else f"{major}.{minor + 1}"


def bump_refusal(old: ProfileSpec, new: ProfileSpec) -> BumpRefusal | None:
    """Why publishing ``new`` over ``old`` must be refused, if it must.

    Args:
        old: The latest published version of this profile.
        new: The specification about to be published.

    Returns:
        The refusal, or None when the declared bump is at least what the content
        requires. Declaring *more* than required is allowed: calling a
        compatible change MAJOR is a judgement, not a mistake.
    """
    required = required_bump(old, new)
    if required == "none":
        # Identical content. Whatever version it declares, republishing it
        # cannot invalidate anything, so there is nothing to refuse.
        return None

    declared = declared_bump(old.version, new.version)
    if _BUMP_RANK[declared] >= _BUMP_RANK[required]:
        return None

    comparison = compare_specs(old, new)
    return BumpRefusal(
        profile=new.name,
        published_version=old.version,
        declared_version=new.version,
        declared=declared,
        required=required,
        suggested_version=_next_version(old.version, required),
        breaking=tuple(change.message for change in comparison.breaking),
    )


async def latest_published_spec(session: AsyncSession, *, tenant_id: str, name: str) -> Spec | None:
    """The newest published specification of a profile name in one account.

    Scoped to the account because that is the release lineage: the uniqueness
    rule on published specs is (tenant, name, version), so a name means one
    thing per account and two accounts may legitimately publish unrelated
    profiles under the same name.

    Ordered on the parsed ``(major, minor)`` pair, not the string, so ``1.10``
    comes after ``1.9``. A row whose version cannot be parsed has no place in
    that order and is skipped with a warning rather than guessed at.

    Args:
        session: Database session.
        tenant_id: The account to look in.
        name: The profile name, matched case-insensitively.

    Returns:
        The latest published spec, or None if the name has never been published
        in this account.
    """
    from sqlalchemy import func, select

    from metaseed_hub.models import Spec, SpecStatus

    result = await session.execute(
        select(Spec).where(
            Spec.tenant_id == tenant_id,
            func.lower(Spec.name) == name.lower(),
            Spec.status == SpecStatus.PUBLISHED,
            Spec.deleted_at.is_(None),
        )
    )
    ranked: list[tuple[tuple[int, int], Spec]] = []
    for row in result.scalars().all():
        try:
            ranked.append((parse_profile_version(row.version), row))
        except ValueError:
            logger.warning(
                "Spec %s declares version %r, which cannot be ordered; "
                "excluded from the version comparison",
                row.id,
                row.version,
            )
    if not ranked:
        return None
    return max(ranked, key=lambda pair: pair[0])[1]
