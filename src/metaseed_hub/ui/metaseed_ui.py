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
"""

from pathlib import Path

import metaseed.ui
from metaseed.ui.state import AppState, TreeNode

_METASEED_UI_DIR = Path(metaseed.ui.__file__).parent

METASEED_STATIC_DIR = _METASEED_UI_DIR / "static"
METASEED_TEMPLATES_DIR = _METASEED_UI_DIR / "templates"

__all__ = [
    "METASEED_STATIC_DIR",
    "METASEED_TEMPLATES_DIR",
    "AppState",
    "TreeNode",
]
