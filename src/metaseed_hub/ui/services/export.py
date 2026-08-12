"""Exporting a dataset as an Excel workbook.

The workbook itself is metaseed's: one sheet per entity type, every cell text,
the tree carried in a ``_parent`` column, and — since the RightField work — the
specification's vocabularies as dropdowns, cross-sheet pickers for references,
tables that absorb new rows, and descriptions on the headings.

This module used to hold its own copy of that builder, taking a facade where
the library's took an application state object. The copy stayed correct and
stopped improving: everything above landed in the library and reached the
standalone application, while the hub kept exporting bare grids. The library
now builds from a facade, which is what both applications hold, and what
remains here is the one genuinely hub-specific thing — what the file is called.

The builder comes through ``metaseed_hub.ui.metaseed_ui``, the hub's single
import boundary to metaseed's internal UI layer, rather than directly.
"""

from __future__ import annotations

from datetime import UTC, datetime
from io import BytesIO
from typing import TYPE_CHECKING

from metaseed import MetaseedClient, ProfileFacade

if TYPE_CHECKING:
    from openpyxl.workbook import Workbook

from metaseed_hub.ui.metaseed_ui import build_workbook_from_facade

__all__ = ["build_workbook", "export_to_bytes", "generate_filename"]


def build_workbook(facade: ProfileFacade) -> Workbook:
    """The dataset's workbook, built by the library."""
    return build_workbook_from_facade(facade)


def export_to_bytes(facade: ProfileFacade) -> BytesIO:
    """Export the facade's entities to an in-memory Excel file.

    Args:
        facade: Profile facade holding the dataset's entities.

    Returns:
        BytesIO positioned at the start of the Excel file.
    """
    output = BytesIO()
    build_workbook_from_facade(facade).save(output)
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
    date_str = datetime.now(UTC).strftime("%y%m%d")
    version_str = facade.version.replace(".", "-")

    entity_id = "export"
    client = MetaseedClient.from_facade(facade)
    roots = client.get_roots()
    if roots:
        unique_id = client.get_entity(roots[0].id).data.get("unique_id")
        if unique_id:
            entity_id = str(unique_id).replace("/", "-").replace(":", "-")[:30]

    return f"{date_str}-{facade.profile}-{version_str}-{entity_id}.xlsx"
