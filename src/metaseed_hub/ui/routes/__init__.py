"""Hub UI route modules."""

from metaseed_hub.ui.routes.auth import router as auth_router
from metaseed_hub.ui.routes.entity import init_templates as init_entity_templates
from metaseed_hub.ui.routes.entity import router as entity_router
from metaseed_hub.ui.routes.project import init_templates as init_project_templates
from metaseed_hub.ui.routes.project import router as project_router
from metaseed_hub.ui.routes.table import router as table_router
from metaseed_hub.ui.routes.workspace import init_templates as init_workspace_templates
from metaseed_hub.ui.routes.workspace import router as workspace_router

__all__ = [
    "auth_router",
    "entity_router",
    "init_entity_templates",
    "init_project_templates",
    "init_workspace_templates",
    "project_router",
    "table_router",
    "workspace_router",
]
