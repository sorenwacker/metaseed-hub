"""Shared router and template initialization for dataset routes."""

from fastapi import APIRouter
from fastapi.templating import Jinja2Templates

from metaseed_hub.ui.render import init_templates as _init_render_templates

router = APIRouter(prefix="/datasets", tags=["datasets"])


def init_templates(templates: Jinja2Templates) -> None:
    """Initialize templates reference."""
    _init_render_templates(templates)
