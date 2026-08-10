"""Hub UI route modules."""

from metaseed_hub.ui.routes.admin import init_templates as init_admin_templates
from metaseed_hub.ui.routes.admin import is_admin
from metaseed_hub.ui.routes.admin import router as admin_router
from metaseed_hub.ui.routes.auth import router as auth_router
from metaseed_hub.ui.routes.dataset import init_templates as init_dataset_templates
from metaseed_hub.ui.routes.dataset import router as dataset_router
from metaseed_hub.ui.routes.entity import init_templates as init_entity_templates
from metaseed_hub.ui.routes.entity import router as entity_router
from metaseed_hub.ui.routes.ontology_api import router as ontology_router
from metaseed_hub.ui.routes.seek import router as seek_router
from metaseed_hub.ui.routes.table import router as table_router

__all__ = [
    "admin_router",
    "seek_router",
    "auth_router",
    "dataset_router",
    "entity_router",
    "init_admin_templates",
    "init_dataset_templates",
    "init_entity_templates",
    "is_admin",
    "ontology_router",
    "table_router",
]
