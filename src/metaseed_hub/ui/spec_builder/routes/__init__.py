"""Spec builder routes package.

Splits the spec builder router into focused sub-modules for maintainability.
"""

from __future__ import annotations

from fastapi import APIRouter, Depends
from fastapi.templating import Jinja2Templates

from metaseed_hub.ui.security import require_same_origin

from .comment_routes import register_comment_routes
from .draft_routes import register_draft_routes
from .entity_routes import register_entity_routes
from .field_routes import register_field_routes
from .list_routes import register_list_routes
from .member_routes import register_member_routes
from .rule_routes import register_rule_routes

__all__ = ["create_spec_builder_router"]


def create_spec_builder_router(templates: Jinja2Templates) -> APIRouter:
    """Create the spec builder router with all routes.

    Args:
        templates: Jinja2Templates instance.

    Returns:
        Configured APIRouter.
    """
    # require_same_origin gives every spec-builder mutation an Origin-based CSRF
    # defense uniformly across HTMX, fetch, and form submissions.
    router = APIRouter(
        prefix="/spec-builder",
        tags=["spec-builder"],
        dependencies=[Depends(require_same_origin)],
    )

    # Register all route groups
    register_list_routes(router, templates)
    register_draft_routes(router, templates)
    register_entity_routes(router, templates)
    register_field_routes(router, templates)
    register_rule_routes(router, templates)
    register_member_routes(router, templates)
    register_comment_routes(router, templates)

    return router
