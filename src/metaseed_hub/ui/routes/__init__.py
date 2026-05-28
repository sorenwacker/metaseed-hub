"""Hub UI route modules."""

from metaseed_hub.ui.routes.auth import router as auth_router
from metaseed_hub.ui.routes.dataset import init_templates as init_dataset_templates
from metaseed_hub.ui.routes.dataset import router as dataset_router
from metaseed_hub.ui.routes.entity import init_templates as init_entity_templates
from metaseed_hub.ui.routes.entity import router as entity_router
from metaseed_hub.ui.routes.ontology_api import router as ontology_router
from metaseed_hub.ui.routes.table import router as table_router
from metaseed_hub.ui.routes.workspace import init_templates as init_workspace_templates
from metaseed_hub.ui.routes.workspace import router as workspace_router

__all__ = [
    "auth_router",
    "dataset_router",
    "entity_router",
    "init_dataset_templates",
    "init_entity_templates",
    "init_workspace_templates",
    "ontology_router",
    "table_router",
    "workspace_router",
]
