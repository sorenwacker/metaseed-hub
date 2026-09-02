"""Specification-drafting tools for the hub's MCP endpoint.

Every tool here follows the package's per-call pattern: the caller is resolved
from its token, the draft is looked up in that user's own account, and
mutations run inside the ``building`` context, which persists the draft after
the block. The mutations themselves are metaseed's :class:`SpecBuilder`'s,
already shared with the web UI; the hub adds only loading and saving.

The registrar takes the shared helpers as arguments rather than importing them
from the package, so the package can import this module without a cycle.
"""

from __future__ import annotations

import json
import logging
from typing import TYPE_CHECKING, Any

from sqlalchemy import select

from metaseed_hub.mcp._contracts import ProfileSpecResolver
from metaseed_hub.models import SpecDraft

if TYPE_CHECKING:
    from collections.abc import Awaitable, Callable
    from contextlib import AbstractAsyncContextManager

    from mcp.server.fastmcp import FastMCP
    from sqlalchemy.ext.asyncio import AsyncSession

    from metaseed_hub.models import User

    Caller = Callable[[], AbstractAsyncContextManager[tuple[AsyncSession, User]]]
    OwnedDraft = Callable[[AsyncSession, User, str], Awaitable[SpecDraft]]
    Building = Callable[[AsyncSession, SpecDraft, User], AbstractAsyncContextManager[Any]]

logger = logging.getLogger("metaseed_hub")


def _clean(attrs: dict[str, Any]) -> dict[str, Any]:
    """Drop keys whose value is None, so unset arguments leave fields unchanged."""
    return {key: value for key, value in attrs.items() if value is not None}


def _constraint_values(supplied: dict[str, Any]) -> dict[str, Any]:
    """The constraints the caller actually supplied, keyed as metaseed names them.

    Args:
        supplied: Every constraint this tool exposes, mapped to the argument the
            caller passed, with None for the ones left unset.

    Returns:
        The subset whose value is not None, ready to merge.

    Raises:
        ValueError: If metaseed defines a constraint this tool does not expose.
            Such a constraint would be silently uneditable over MCP, so it is
            reported rather than skipped.
    """
    from metaseed.specs.builder import CONSTRAINT_NAMES

    missing = sorted(set(CONSTRAINT_NAMES) - set(supplied))
    if missing:
        raise ValueError(
            f"This tool does not expose the constraint(s) {', '.join(missing)}, "
            "which metaseed defines. Edit the field through the web interface "
            "until the tool is extended."
        )
    return _clean(supplied)


def _markers(supplied: dict[str, Any]) -> dict[str, Any]:
    """The field markers the caller supplied, normalized as metaseed normalizes them.

    A marker, unlike a numeric constraint, has a representable empty value, so
    it needs no ``clear`` list: ``False``, ``""`` and ``[]`` are the removal
    request and are mapped onto None, which keeps an unset marker out of the
    serialized spec rather than writing ``owns: false``.

    Args:
        supplied: Every marker this tool exposes, mapped to the argument the
            caller passed, with None for the ones left unset.

    Returns:
        The markers to assign, omitted ones dropped and emptied ones None.

    Raises:
        ValueError: If metaseed defines a marker this tool does not expose --
            such a marker would be silently undeclarable over MCP -- or if a
            supplied value is not one the schema accepts. Raised before the
            tools mutate anything, so a refused call leaves the draft as it was.
    """
    from metaseed.specs.builder import (
        FIELD_MARKER_NAMES,
        normalize_markers,
        validate_marker_values,
    )

    missing = sorted(set(FIELD_MARKER_NAMES) - set(supplied))
    if missing:
        raise ValueError(
            f"This tool does not expose the field marker(s) {', '.join(missing)}, "
            "which metaseed defines. Edit the field through the web interface "
            "until the tool is extended."
        )
    markers: dict[str, Any] = normalize_markers(supplied)
    error = validate_marker_values(markers)
    if error:
        raise ValueError(error)
    return markers


def _constraints(limits: dict[str, Any]) -> Any | None:
    """A ``Constraints`` over the limits supplied, or None if none were.

    ``limits`` maps every constraint this tool exposes; routing it through
    :func:`_constraint_values` means ``spec_add_field`` refuses when metaseed
    defines a constraint the tool does not expose, exactly as ``spec_update_field``
    does — otherwise a new metaseed constraint was silently unaddable.

    None rather than an empty object: an empty ``constraints`` block would be
    written into every field of every draft the hub builds.
    """
    from metaseed.specs.schema import Constraints

    values = _constraint_values(limits)
    return Constraints(**values) if values else None


def _status(builder: Any) -> dict[str, Any]:
    """A summary of a draft's spec: name, version, root, entities, rules.

    The same shape the standalone metaseed server reports, so an agent moving
    between the two reads the same summary.
    """
    spec = builder.spec
    return {
        "name": spec.name,
        "version": spec.version,
        "display_name": spec.display_name,
        "root_entity": spec.root_entity,
        "entities": {
            entity_name: [f.name for f in entity.fields]
            for entity_name, entity in spec.entities.items()
        },
        "validation_rules": [r.name for r in spec.validation_rules],
    }


def _loaded_spec(row: SpecDraft, draft: str) -> Any:
    """The ProfileSpec a draft row holds.

    Raises:
        ValueError: If the row holds no specification.
    """
    from metaseed_hub.ui.spec_builder.state import SpecBuilderState

    state = SpecBuilderState.from_dict(row.spec_data) if row.spec_data else SpecBuilderState()
    if state.spec is None:
        raise ValueError(f"Draft {draft!r} holds no specification")
    return state.spec


async def _new_named_draft(
    session: AsyncSession,
    user: User,
    name: str,
    spec: Any,
    template_source: tuple[str, str] | None = None,
) -> SpecDraft:
    """Create a draft holding ``spec``, under a name free in the caller's account.

    The one draft-creation path of this module, shared by spec_create,
    spec_import_yaml, and spec_clone so the name-uniqueness rule cannot drift
    between them. Scoped to the user like ``_owned_draft``: draft names are
    unique per user, so someone else's draft must not block the name.

    Raises:
        ValueError: If the caller already has a draft by that name.
    """
    from metaseed_hub.ui.spec_builder.access import create_new_draft

    existing = await session.execute(
        select(SpecDraft).where(
            SpecDraft.tenant_id == user.tenant_id,
            SpecDraft.user_id == user.id,
            SpecDraft.name == name,
        )
    )
    if existing.scalar_one_or_none() is not None:
        raise ValueError(f"A draft named {name!r} already exists")

    return await create_new_draft(
        session,
        user_id=user.id,
        tenant_id=user.tenant_id,
        name=name,
        spec=spec,
        template_source=template_source,
    )


def register_spec_tools(  # noqa: C901
    mcp: FastMCP,
    *,
    caller: Caller,
    owned_draft: OwnedDraft,
    building: Building,
    profile_spec: ProfileSpecResolver,
) -> None:
    """Register the specification tools with the hub's MCP server.

    Args:
        mcp: The FastMCP server to add the tools to.
        caller: Async context manager resolving the current call's token to a
            ``(session, user)`` pair.
        owned_draft: Coroutine returning the caller's own draft by name.
        building: Async context manager yielding a ``SpecBuilder`` over a draft
            and persisting the draft after the block.
        profile_spec: Coroutine resolving a profile name and version to a
            ``ProfileSpec``, built-in first, then published.
    """

    @mcp.tool()
    async def spec_create(name: str, version: str, description: str = "") -> str:
        """Start a new specification as a private draft.

        A draft is visible only to you. Publishing it — which shares it with
        every user of the hub — is done from the web interface, deliberately:
        it is not something an agent should do on your behalf.

        Args:
            name: The profile name.
            version: The profile version, e.g. "1.0".
            description: What the specification is for.
        """
        from metaseed.specs.builder import SpecBuilder

        async with caller() as (session, user):
            builder = SpecBuilder.empty(name, version, description=description)
            draft = await _new_named_draft(session, user, name, builder.spec)
            logger.info("mcp: %s created spec draft %r", user.email, name)
            return json.dumps({"name": draft.name, "version": draft.version})

    @mcp.tool()
    async def spec_import_yaml(yaml_text: str, name: str = "") -> str:
        """Start a new private draft from a YAML specification document.

        The same parser the web interface's Import page uses, so a spec written
        elsewhere — for example saved by the standalone metaseed server, which
        keeps its files on the local filesystem and not on the hub — lands here
        as a draft. Publishing stays a human action in the web interface.

        Args:
            yaml_text: The specification as a YAML document.
            name: The draft's name in your account. Left empty, the spec's
                own name is used.
        """
        from metaseed_hub.ui.spec_builder_helpers import parse_spec_from_yaml

        async with caller() as (session, user):
            spec = parse_spec_from_yaml(yaml_text)
            draft_name = name.strip() or spec.name or "Imported Spec"
            draft = await _new_named_draft(session, user, draft_name, spec)
            logger.info("mcp: %s imported spec draft %r", user.email, draft_name)
            return json.dumps({"name": draft.name, "version": draft.version})

    @mcp.tool()
    async def spec_clone(profile: str, version: str, name: str = "") -> str:
        """Start a new private draft from a built-in profile or published spec.

        The clone is your own copy: editing it changes nothing for anyone else.
        Both kinds of source appear in list_profiles.

        Args:
            profile: A profile name from list_profiles.
            version: The profile version.
            name: The draft's name in your account. Left empty, the
                profile's own name is used.
        """
        import copy

        async with caller() as (session, user):
            spec = copy.deepcopy(
                await profile_spec(session, profile, version, prefer_tenant=user.tenant_id)
            )
            draft_name = name.strip() or spec.name
            draft = await _new_named_draft(
                session, user, draft_name, spec, template_source=(profile, version)
            )
            logger.info(
                "mcp: %s cloned %s %s as spec draft %r", user.email, profile, version, draft_name
            )
            return json.dumps({"name": draft.name, "version": draft.version})

    @mcp.tool()
    async def spec_add_entity(
        draft: str, entity: str, description: str = "", ontology_term: str | None = None
    ) -> str:
        """Add an entity type to a draft specification.

        Args:
            draft: The draft's name in the caller's account.
            entity: The entity type's name, e.g. "Study".
            description: What the entity represents.
            ontology_term: An ontology term identifying it, where one applies.
        """
        async with caller() as (session, user):
            row = await owned_draft(session, user, draft)
            async with building(session, row, user) as builder:
                builder.add_entity(entity, description=description, ontology_term=ontology_term)
                problems = builder.validate()
            return json.dumps({"entity": entity, "problems": problems})

    @mcp.tool()
    async def spec_update_entity(
        draft: str,
        entity: str,
        description: str | None = None,
        ontology_term: str | None = None,
    ) -> str:
        """Change an entity's description or ontology term in place.

        Arguments left unset keep their current values.

        Args:
            draft: The draft's name in the caller's account.
            entity: The entity type to change.
            description: The new description.
            ontology_term: The new ontology term.
        """
        async with caller() as (session, user):
            row = await owned_draft(session, user, draft)
            async with building(session, row, user) as builder:
                builder.update_entity(entity, description=description, ontology_term=ontology_term)
                problems = builder.validate()
            return json.dumps({"entity": entity, "problems": problems})

    @mcp.tool()
    async def spec_rename_entity(draft: str, old_name: str, new_name: str) -> str:
        """Rename an entity type, cascading every reference to it.

        The root entity, nested-field ``items`` links, and cross-references
        follow the new name, so the spec is never left pointing at an entity
        that no longer exists.

        Args:
            draft: The draft's name in the caller's account.
            old_name: The entity type's current name.
            new_name: The name to give it.
        """
        async with caller() as (session, user):
            row = await owned_draft(session, user, draft)
            async with building(session, row, user) as builder:
                builder.rename_entity(old_name, new_name)
                problems = builder.validate()
            return json.dumps({"entity": new_name, "problems": problems})

    @mcp.tool()
    async def spec_delete_entity(draft: str, entity: str) -> str:
        """Remove an entity type from a draft specification.

        If the entity was the root, the draft is left without a root entity
        until spec_set_root_entity names a new one.

        Args:
            draft: The draft's name in the caller's account.
            entity: The entity type to remove.
        """
        async with caller() as (session, user):
            row = await owned_draft(session, user, draft)
            async with building(session, row, user) as builder:
                builder.delete_entity(entity)
                problems = builder.validate()
            return json.dumps({"deleted": entity, "problems": problems})

    @mcp.tool()
    async def spec_add_field(
        draft: str,
        entity: str,
        field: str,
        field_type: str,
        required: bool = False,
        description: str = "",
        items: str | None = None,
        ontology_term: str | None = None,
        reference: str | None = None,
        parent_ref: str | None = None,
        pattern: str | None = None,
        min_length: int | None = None,
        max_length: int | None = None,
        minimum: float | None = None,
        maximum: float | None = None,
        min_items: int | None = None,
        max_items: int | None = None,
        enum: list[str] | None = None,
        codename: str | None = None,
        ontologies: list[str] | None = None,
        unique_within: str | None = None,
        reference_scope: str | None = None,
        dcat: str | None = None,
        owns: bool | None = None,
        is_identifier: bool | None = None,
        is_label: bool | None = None,
        example: str | None = None,
        options: list[str] | None = None,
        unit: str | None = None,
        label: str | None = None,
        tier: str | None = None,
        isa_tag: str | None = None,
        within: str | None = None,
        seek_attribute_type: str | None = None,
        seek_controlled_vocab: str | None = None,
        seek_cv_free_text: bool | None = None,
    ) -> str:
        """Add a field to an entity in a draft specification.

        A nested field — a ``list`` or ``entity`` whose ``items`` names a child
        entity — is what links the child under this one, and adding it also
        creates the parent's identifier and the child's back-reference. Without
        it the child is an orphan no dataset can reach.

        Beyond the constraints, the field's markers can be declared here.
        ``is_identifier`` and ``is_label`` say which field identifies the entity
        and which one labels it, overriding the positional convention that would
        otherwise pick the first field; the rest describe the field. An unset
        marker is left out of the specification rather than written as false.

        Args:
            draft: The draft's name in the caller's account.
            entity: The entity type to add the field to.
            field: The field's name.
            field_type: One of string, integer, float, boolean, date, datetime,
                uri, ontology_term, list, entity.
            required: Whether a valid dataset must supply it. Reported by
                validation when absent, not enforced when saving.
            description: What the field records.
            items: For list and entity fields, the child entity type this field
                nests — the link that makes the child reachable.
            ontology_term: An ontology term identifying it, where one applies.
            reference: A cross-reference target as "Entity.field".
            parent_ref: The parent-reference field this one answers.
            pattern: A regular expression a string value must match.
            min_length: The shortest a string value may be.
            max_length: The longest a string value may be.
            minimum: The smallest a numeric value may be.
            maximum: The largest a numeric value may be.
            min_items: The fewest entries a list may hold.
            max_items: The most entries a list may hold.
            enum: The values allowed, where the field is enumerated.
            codename: A short machine name the field is also known by.
            ontologies: The ontologies a term for this field may come from.
            unique_within: The entity type this field's value is unique within.
            reference_scope: Whether this field's reference must resolve in
                the dataset (the default) or may name a record held
                elsewhere ("external"), such as a GBIF taxon.
            dcat: The DCAT property this field maps onto.
            owns: Whether the referenced entity is contained by this one.
            is_identifier: Whether this field identifies the entity.
            is_label: Whether this field labels the entity in listings.
            example: An example value, shown to whoever fills the field in.
            options: Suggested values, which unlike ``enum`` are not enforced.
            unit: The unit the value is measured in.
            label: The human-readable name shown for the field.
            tier: How strongly the field is expected: required, recommended,
                or optional.
            isa_tag: The ISA tag the field carries into a SEEK Sample Type.
            within: An ontology term whose descendants are the values this
                field takes, e.g. "CO_715:0000006". Scopes a column to one
                branch rather than a whole ontology.
        """
        markers = _markers(
            {
                "codename": codename,
                "ontologies": ontologies,
                "unique_within": unique_within,
                "reference_scope": reference_scope,
                "dcat": dcat,
                "owns": owns,
                "is_identifier": is_identifier,
                "is_label": is_label,
                "example": example,
                "options": options,
                "unit": unit,
                "label": label,
                "tier": tier,
                "isa_tag": isa_tag,
                "within": within,
                "seek_attribute_type": seek_attribute_type,
                "seek_controlled_vocab": seek_controlled_vocab,
                "seek_cv_free_text": seek_cv_free_text,
            }
        )
        constraints = _constraints(
            {
                "pattern": pattern,
                "min_length": min_length,
                "max_length": max_length,
                "minimum": minimum,
                "maximum": maximum,
                "min_items": min_items,
                "max_items": max_items,
                "enum": enum,
            }
        )
        async with caller() as (session, user):
            row = await owned_draft(session, user, draft)
            async with building(session, row, user) as builder:
                builder.add_field(
                    entity,
                    field,
                    field_type,
                    required=required,
                    description=description,
                    **_clean(
                        {
                            "items": items,
                            "ontology_term": ontology_term,
                            "reference": reference,
                            "parent_ref": parent_ref,
                            "constraints": constraints,
                        }
                    ),
                    # On a new field an emptied marker and an omitted one are
                    # the same request, so the Nones are dropped rather than
                    # assigned.
                    **_clean(markers),
                )
                problems = builder.validate()
            return json.dumps({"entity": entity, "field": field, "problems": problems})

    @mcp.tool()
    async def spec_update_field(
        draft: str,
        entity: str,
        field_name: str,
        field_type: str | None = None,
        required: bool | None = None,
        description: str | None = None,
        items: str | None = None,
        ontology_term: str | None = None,
        reference: str | None = None,
        parent_ref: str | None = None,
        pattern: str | None = None,
        min_length: int | None = None,
        max_length: int | None = None,
        minimum: float | None = None,
        maximum: float | None = None,
        min_items: int | None = None,
        max_items: int | None = None,
        enum: list[str] | None = None,
        codename: str | None = None,
        ontologies: list[str] | None = None,
        unique_within: str | None = None,
        reference_scope: str | None = None,
        dcat: str | None = None,
        owns: bool | None = None,
        is_identifier: bool | None = None,
        is_label: bool | None = None,
        example: str | None = None,
        options: list[str] | None = None,
        unit: str | None = None,
        label: str | None = None,
        tier: str | None = None,
        isa_tag: str | None = None,
        within: str | None = None,
        seek_attribute_type: str | None = None,
        seek_controlled_vocab: str | None = None,
        seek_cv_free_text: bool | None = None,
        clear: list[str] | None = None,
    ) -> str:
        """Change a field in place. Arguments left unset keep their values.

        A constraint you do not supply keeps the value it had, so tightening one
        bound no longer discards the rest. Because an unset argument means
        "unchanged", it cannot express removal: ``clear`` names the constraints
        to unset. Naming a constraint in both ``clear`` and its own argument is
        refused — the two requests contradict each other — and the field is left
        untouched, as it is when a name is not a constraint at all.

        The markers are assigned whole and need no ``clear``: pass ``false``,
        ``""`` or ``[]`` to unset one, which leaves it out of the specification
        rather than writing it as false. A list marker is replaced, not merged.

        Args:
            draft: The draft's name in the caller's account.
            entity: The entity type holding the field.
            field_name: The field to change.
            field_type: A new type (string, integer, float, boolean, date,
                datetime, uri, ontology_term, list, entity).
            required: Whether a valid dataset must supply it. Reported by
                validation when absent, not enforced when saving.
            description: The new description.
            items: For list and entity fields, the child entity type this
                field nests — the link that makes the child reachable.
            ontology_term: An ontology term identifying it, where one applies.
            reference: A cross-reference target as "Entity.field".
            parent_ref: The parent-reference field this one answers.
            pattern: A regular expression a string value must match.
            min_length: The shortest a string value may be.
            max_length: The longest a string value may be.
            minimum: The smallest a numeric value may be.
            maximum: The largest a numeric value may be.
            min_items: The fewest entries a list may hold.
            max_items: The most entries a list may hold.
            enum: The values allowed, where the field is enumerated.
            codename: A short machine name the field is also known by.
            ontologies: The ontologies a term for this field may come from.
            unique_within: The entity type this field's value is unique within.
            reference_scope: Whether this field's reference must resolve in
                the dataset (the default) or may name a record held
                elsewhere ("external"), such as a GBIF taxon.
            dcat: The DCAT property this field maps onto.
            owns: Whether the referenced entity is contained by this one.
            is_identifier: Whether this field identifies the entity.
            is_label: Whether this field labels the entity in listings.
            example: An example value, shown to whoever fills the field in.
            options: Suggested values, which unlike ``enum`` are not enforced.
            unit: The unit the value is measured in.
            label: The human-readable name shown for the field.
            tier: How strongly the field is expected: required, recommended,
                or optional.
            clear: Constraint names to remove, e.g. ["pattern"]. Constraints
                only: a marker is unset by passing its own empty value.
        """
        from metaseed.specs.builder import validate_constraint_names

        markers = _markers(
            {
                "codename": codename,
                "ontologies": ontologies,
                "unique_within": unique_within,
                "reference_scope": reference_scope,
                "dcat": dcat,
                "owns": owns,
                "is_identifier": is_identifier,
                "is_label": is_label,
                "example": example,
                "options": options,
                "unit": unit,
                "label": label,
                "tier": tier,
                "isa_tag": isa_tag,
                "within": within,
                "seek_attribute_type": seek_attribute_type,
                "seek_controlled_vocab": seek_controlled_vocab,
                "seek_cv_free_text": seek_cv_free_text,
            }
        )
        constraints = _constraint_values(
            {
                "pattern": pattern,
                "min_length": min_length,
                "max_length": max_length,
                "minimum": minimum,
                "maximum": maximum,
                "min_items": min_items,
                "max_items": max_items,
                "enum": enum,
            }
        )
        cleared = list(clear or ())
        # Checked before anything is mutated, as the marker values above are:
        # update_field below lands first, so letting the merge reject the name
        # would leave the ordinary attributes applied and the constraints not.
        name_error = validate_constraint_names(cleared)
        if name_error:
            raise ValueError(name_error)

        async with caller() as (session, user):
            row = await owned_draft(session, user, draft)
            async with building(session, row, user) as builder:
                attributes = _clean(
                    {
                        "type": field_type,
                        "required": required,
                        "description": description,
                        "items": items,
                        "ontology_term": ontology_term,
                        "reference": reference,
                        "parent_ref": parent_ref,
                    }
                )
                if attributes or markers:
                    # The markers are not cleaned: a normalized None among them
                    # is an explicit "unset this", not an unsupplied argument.
                    builder.update_field(entity, field_name, **attributes, **markers)
                if constraints or cleared:
                    builder.update_field_constraints(
                        entity, field_name, clear=cleared, **constraints
                    )
                problems = builder.validate()
            return json.dumps({"entity": entity, "field": field_name, "problems": problems})

    @mcp.tool()
    async def spec_delete_field(draft: str, entity: str, field_name: str) -> str:
        """Remove a field from an entity in a draft specification.

        Args:
            draft: The draft's name in the caller's account.
            entity: The entity type holding the field.
            field_name: The field to remove.
        """
        async with caller() as (session, user):
            row = await owned_draft(session, user, draft)
            async with building(session, row, user) as builder:
                builder.delete_field(entity, field_name)
                problems = builder.validate()
            return json.dumps({"entity": entity, "deleted": field_name, "problems": problems})

    @mcp.tool()
    async def spec_move_field(draft: str, entity: str, field_name: str, direction: str) -> str:
        """Move a field one position up or down within its entity.

        Args:
            draft: The draft's name in the caller's account.
            entity: The entity type holding the field.
            field_name: The field to move.
            direction: "up" or "down".
        """
        async with caller() as (session, user):
            row = await owned_draft(session, user, draft)
            async with building(session, row, user) as builder:
                builder.move_field(entity, field_name, direction)
                problems = builder.validate()
            return json.dumps(
                {"entity": entity, "field": field_name, "moved": direction, "problems": problems}
            )

    @mcp.tool()
    async def spec_set_root_entity(draft: str, entity: str) -> str:
        """Set which entity a dataset of this profile starts from.

        Args:
            draft: The draft's name in the caller's account.
            entity: The entity type to use as the root.
        """
        async with caller() as (session, user):
            row = await owned_draft(session, user, draft)
            async with building(session, row, user) as builder:
                builder.set_root_entity(entity)
                problems = builder.validate()
            return json.dumps({"root_entity": entity, "problems": problems})

    @mcp.tool()
    async def spec_set_metadata(
        draft: str,
        name: str | None = None,
        version: str | None = None,
        display_name: str | None = None,
        description: str | None = None,
        ontology: str | None = None,
    ) -> str:
        """Change the draft's profile-level metadata in place.

        Arguments left unset keep their values. Renaming here changes the
        specification's profile name; the draft keeps the name it is addressed
        by in these tools.

        Args:
            draft: The draft's name in the caller's account.
            name: The new profile name.
            version: The new profile version.
            display_name: The human-readable name shown in listings.
            description: What the specification is for.
            ontology: The ontology prefix the profile draws terms from.
        """
        async with caller() as (session, user):
            row = await owned_draft(session, user, draft)
            async with building(session, row, user) as builder:
                builder.set_metadata(
                    **_clean(
                        {
                            "name": name,
                            "version": version,
                            "display_name": display_name,
                            "description": description,
                            "ontology": ontology,
                        }
                    )
                )
                problems = builder.validate()
            return json.dumps({"draft": draft, "problems": problems})

    @mcp.tool()
    async def spec_status(draft: str) -> str:
        """Summarize a draft: name, version, root, entities, and rules.

        Args:
            draft: The draft's name in the caller's account.
        """
        from metaseed.specs.builder import SpecBuilder

        async with caller() as (session, user):
            row = await owned_draft(session, user, draft)
            builder = SpecBuilder.from_spec(_loaded_spec(row, draft))
            return json.dumps(_status(builder))

    @mcp.tool()
    async def spec_validate(draft: str) -> str:
        """Report what is wrong or missing in a draft specification.

        ``problems`` are defects: the draft does not build, and ``valid`` is
        false. ``warnings`` are advisory — an identifier inferred onto an
        optional free-text field, for one — and are reported separately because
        a draft that trips one still builds, so it stays valid.

        Args:
            draft: The draft's name in the caller's account.
        """
        from metaseed.specs.builder import SpecBuilder

        from metaseed_hub.ui.spec_builder.state import SpecBuilderState

        async with caller() as (session, user):
            row = await owned_draft(session, user, draft)
            state = (
                SpecBuilderState.from_dict(row.spec_data) if row.spec_data else SpecBuilderState()
            )
            if state.spec is None:
                return json.dumps(
                    {
                        "draft": draft,
                        "problems": ["The draft holds no specification"],
                        "warnings": [],
                    }
                )
            builder = SpecBuilder.from_spec(state.spec)
            problems = builder.validate()
            return json.dumps(
                {
                    "draft": draft,
                    "valid": not problems,
                    "problems": problems,
                    "warnings": builder.warnings(),
                }
            )

    @mcp.tool()
    async def spec_preview_yaml(draft: str) -> str:
        """Return a draft specification as YAML, without saving anything.

        Args:
            draft: The draft's name in the caller's account.
        """
        from metaseed.specs.builder import SpecBuilder

        async with caller() as (session, user):
            row = await owned_draft(session, user, draft)
            builder = SpecBuilder.from_spec(_loaded_spec(row, draft))
            return json.dumps({"draft": draft, "yaml": builder.to_yaml()})

    @mcp.tool()
    async def spec_delete_draft(draft: str) -> str:
        """Delete one of your own draft specifications.

        The whole draft goes, with no version history to restore it from, so
        this is the one spec tool that cannot be undone. A draft that datasets
        are built on is kept instead: they validate against it and would be left
        without a specification.

        Args:
            draft: The draft's name in the caller's account.
        """
        from metaseed_hub.ui.spec_builder.access import delete_draft

        async with caller() as (session, user):
            row = await owned_draft(session, user, draft)
            dependents = await delete_draft(session, row)
            if dependents:
                raise ValueError(
                    f"{len(dependents)} dataset(s) are built on {draft!r} "
                    f"({', '.join(dependents)}); delete or move them first"
                )
            logger.info("mcp: %s deleted spec draft %r", user.email, draft)
            return json.dumps({"deleted": draft})

    @mcp.tool()
    async def list_spec_drafts() -> str:
        """List the caller's own draft specifications."""
        async with caller() as (session, user):
            result = await session.execute(
                select(SpecDraft)
                .where(SpecDraft.tenant_id == user.tenant_id, SpecDraft.user_id == user.id)
                .order_by(SpecDraft.updated_at.desc())
            )
            return json.dumps(
                [{"name": d.name, "version": d.version} for d in result.scalars().all()]
            )
