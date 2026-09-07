"""The hub's single import boundary to metaseed's internal UI layer.

The hub consumes metaseed through its public API (``MetaseedClient``,
``ProfileFacade``). What remains of the internal ``metaseed.ui`` layer is
re-exported here, and only here; ``tests/test_metaseed_coupling.py`` fails if
any other module under ``src/metaseed_hub`` imports ``metaseed.ui`` directly.

Re-exported internals:

- ``AppState`` / ``TreeNode``: the request-scoped entity-tree cache the editor
  routes and template helpers render from. Every mutation goes through
  ``AppState.add_node``/``update_node``, which write to the facade (the source
  of truth) and keep the cache consistent.
- ``METASEED_STATIC_DIR`` / ``METASEED_TEMPLATES_DIR``: the asset directories
  packaged with metaseed's standalone explorer, which the hub mounts and adds
  to its template loader.
- ``build_workbook_from_facade``: the Excel export. The hub had its own copy of
  this — the same builder taking a facade where the library's took an
  ``AppState`` — which stayed correct and stopped improving, so the dropdowns,
  tables and heading descriptions never reached it. The library now builds from
  the facade both applications hold, and the copy is gone.
- ``workbook_to_payload``: the Excel import, the export's other half. The hub
  had a copy of this too, and that one was not merely behind but wrong: it never
  removed the quote the export puts in front of a formula-triggering cell, and
  never split a scalar list back out of the cell the export joined it into, so
  every round trip through the hub changed those values.
"""

from pathlib import Path

import metaseed.ui
from metaseed.ui.services.export import build_workbook_from_facade
from metaseed.ui.services.import_excel import workbook_to_payload
from metaseed.ui.state import AppState, TreeNode

_METASEED_UI_DIR = Path(metaseed.ui.__file__).parent

METASEED_STATIC_DIR = _METASEED_UI_DIR / "static"
METASEED_TEMPLATES_DIR = _METASEED_UI_DIR / "templates"

__all__ = [
    "METASEED_STATIC_DIR",
    "build_workbook_from_facade",
    "METASEED_TEMPLATES_DIR",
    "AppState",
    "TreeNode",
    "workbook_to_payload",
]
