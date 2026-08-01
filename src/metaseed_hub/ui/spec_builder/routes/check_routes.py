"""The Checks panel: what metaseed reports about a draft specification."""

from __future__ import annotations

from fastapi import APIRouter, Request
from fastapi.responses import HTMLResponse
from fastapi.templating import Jinja2Templates

from ._common import DraftContextDep

__all__ = ["register_check_routes"]


def register_check_routes(router: APIRouter, templates: Jinja2Templates) -> None:
    """Register the draft's Checks panel route.

    Args:
        router: The spec builder router the route is added to.
        templates: The Jinja2 environment the panel is rendered with.
    """

    @router.get("/{draft_id}/checks", response_class=HTMLResponse)
    async def get_checks(
        request: Request,
        ctx: DraftContextDep,
    ) -> HTMLResponse:
        """Report the draft's defects and advisories, kept apart.

        metaseed reports the two separately and the panel keeps them separate
        all the way to the reader: a problem means the specification does not
        build, while an advisory means it builds and works but something in it
        is probably unintended. Merging them would make a working draft read as
        broken and invite an author to "fix" a specification that is fine.
        """
        from metaseed.specs.builder import SpecBuilder

        builder = SpecBuilder.from_spec(ctx.spec)

        return templates.TemplateResponse(
            request,
            "spec_builder/partials/checks.html",
            {
                "draft_id": ctx.draft.id,
                "problems": builder.validate(),
                "advisories": builder.warnings(),
            },
        )
