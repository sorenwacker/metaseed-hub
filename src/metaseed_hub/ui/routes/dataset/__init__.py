"""Dataset routes for Hub UI.

The routes are split across submodules by responsibility; importing them here
registers them on the shared router.
"""

from . import comments, crud, editor, versions  # noqa: F401  (registers routes)
from ._router import init_templates, router

__all__ = ["router", "init_templates"]
