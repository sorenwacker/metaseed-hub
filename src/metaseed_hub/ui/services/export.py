"""Excel export of a dataset, built on metaseed's public client/facade API.

One sheet per entity type the profile defines, one row per entity. Entities
embedded as lists of dicts inside nested fields (for example contacts recorded
inline on an Investigation) get rows on their own type's sheet in addition to
the parent's field showing their count.
"""

from datetime import datetime
from io import BytesIO
from typing import Any

from metaseed import MetaseedClient, ProfileFacade
from openpyxl import Workbook

# Characters that make Excel/LibreOffice interpret a cell as a formula. A
# string value beginning with one of these (e.g. a collaborator-supplied field
# like ``=HYPERLINK(...)``) would otherwise round-trip into a live formula on
# export.
_FORMULA_TRIGGERS = ("=", "+", "-", "@", "\t", "\r")


def _escape_formula(value: object) -> object:
    """Neutralize a formula-injection payload in a string cell value.

    Prefixes a single quote so the value is stored and opened as literal text,
    not a formula. Non-strings cannot be formulas and are returned unchanged.

    Args:
        value: Cell value about to be written.

    Returns:
        The value, prefixed with ``'`` if it would trigger a formula.
    """
    if isinstance(value, str) and value.startswith(_FORMULA_TRIGGERS):
        return "'" + value
    return value


def _format_cell_value(value: object, is_nested_field: bool) -> object:
    """Flatten a field value into something Excel can hold in one cell.

    Nested-entity fields are summarized as item counts; lists of primitives are
    joined with commas; dicts and other objects degrade to markers or strings.

    Args:
        value: Raw field value from the entity payload.
        is_nested_field: Whether the field holds nested entities.

    Returns:
        Value suitable for an Excel cell.
    """
    if is_nested_field:
        if isinstance(value, list):
            return len(value)
        return 1 if value else 0
    if isinstance(value, list):
        if value and not isinstance(value[0], dict):
            return ", ".join(str(v) for v in value)
        return len(value)
    if isinstance(value, dict):
        return "[object]"
    if not isinstance(value, str | int | float | bool | type(None)):
        return str(value)
    return value


def _collect_entities_by_type(facade: ProfileFacade) -> dict[str, list[dict[str, Any]]]:
    """Group every entity payload in the facade by entity type.

    Serializes the facade's store through ``MetaseedClient.serialize`` (flat
    format, one payload per stored entity) and additionally recurses into
    nested fields, so entities embedded as lists of dicts inside a parent's
    data are collected under their own type as well.

    Args:
        facade: Profile facade holding the dataset's entities.

    Returns:
        Mapping of entity type to its data payloads (metadata keys stripped).
    """
    by_type: dict[str, list[dict[str, Any]]] = {}

    def collect(entity_type: str, data: dict[str, Any]) -> None:
        by_type.setdefault(entity_type, []).append(data)
        helper = getattr(facade, entity_type, None)
        if helper is None:
            return
        for field_name, nested_type in helper.nested_fields.items():
            items = data.get(field_name)
            if not isinstance(items, list):
                continue
            for item in items:
                if isinstance(item, dict):
                    collect(nested_type, item)

    payload = MetaseedClient.from_facade(facade).serialize()
    for entity in payload.get("entities", []):
        entity_type = entity.get("_type")
        if not entity_type:
            continue
        collect(entity_type, {k: v for k, v in entity.items() if not k.startswith("_")})
    return by_type


def build_workbook(facade: ProfileFacade) -> Workbook:
    """Build an Excel workbook with one sheet per entity type.

    Args:
        facade: Profile facade holding the dataset's entities.

    Returns:
        Openpyxl workbook; sheets follow the profile's entity-type order.
    """
    entities_by_type = _collect_entities_by_type(facade)

    workbook = Workbook()
    workbook.remove(workbook.active)

    for entity_type in facade.entities:
        helper = getattr(facade, entity_type, None)
        if helper is None:
            continue

        sheet = workbook.create_sheet(entity_type)
        nested_fields = set(helper.nested_fields)
        columns = helper.all_fields
        sheet.append(columns)

        for row_offset, entity_data in enumerate(entities_by_type.get(entity_type, []), start=2):
            for col_offset, col in enumerate(columns, start=1):
                value = _escape_formula(
                    _format_cell_value(entity_data.get(col, ""), col in nested_fields)
                )
                cell = sheet.cell(
                    row=row_offset,
                    column=col_offset,
                    value=str(value) if value != "" else "",
                )
                # Every data cell is text. Excel otherwise reinterprets what it
                # recognises -- gene names become dates, identifiers lose their
                # leading zeros -- and a metadata value must survive the round
                # trip byte for byte.
                cell.number_format = "@"

    return workbook


def export_to_bytes(facade: ProfileFacade) -> BytesIO:
    """Export the facade's entities to an in-memory Excel file.

    Args:
        facade: Profile facade holding the dataset's entities.

    Returns:
        BytesIO positioned at the start of the Excel file.
    """
    output = BytesIO()
    build_workbook(facade).save(output)
    output.seek(0)
    return output


def generate_filename(facade: ProfileFacade) -> str:
    """Generate a ``YYMMDD-profile-version-rootid.xlsx`` export filename.

    The root-entity segment is the first root's ``unique_id`` (path-hostile
    characters replaced, truncated to 30), falling back to ``export`` when the
    dataset has no root or the root has no ``unique_id``.

    Args:
        facade: Profile facade holding the dataset's entities.

    Returns:
        Filename for the Excel export.
    """
    date_str = datetime.now().strftime("%y%m%d")
    version_str = facade.version.replace(".", "-")

    entity_id = "export"
    client = MetaseedClient.from_facade(facade)
    roots = client.get_roots()
    if roots:
        unique_id = client.get_entity(roots[0].id).data.get("unique_id")
        if unique_id:
            entity_id = str(unique_id).replace("/", "-").replace(":", "-")[:30]

    return f"{date_str}-{facade.profile}-{version_str}-{entity_id}.xlsx"
