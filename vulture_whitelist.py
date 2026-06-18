"""Vulture whitelist for false positives in dead code detection.

This file contains patterns that vulture incorrectly flags as unused.
These are typically framework-specific patterns where the code is
accessed dynamically or through reflection.
"""

# SQLAlchemy relationship back_populates are used by SQLAlchemy ORM
# but appear unused to static analysis
tenant  # noqa: F821
teams  # noqa: F821
users  # noqa: F821
workspaces  # noqa: F821
memberships  # noqa: F821
notes  # noqa: F821
chat_messages  # noqa: F821
project  # noqa: F821
workspace  # noqa: F821
user  # noqa: F821
team  # noqa: F821
projects  # noqa: F821

# Pydantic model_config is accessed by Pydantic internally
model_config  # noqa: F821
from_attributes  # noqa: F821

# Enum values are accessed dynamically
OWNER  # noqa: F821
ADMIN  # noqa: F821
MEMBER  # noqa: F821

# Alembic migration functions are called by the migration runner
upgrade  # noqa: F821
downgrade  # noqa: F821

# SQLAlchemy Base class attributes
type_annotation_map  # noqa: F821

# Mixin properties used by models
soft_delete  # noqa: F821
created_at  # noqa: F821
updated_at  # noqa: F821
deleted_at  # noqa: F821

# SQLAlchemy declared_attr uses cls parameter internally
cls  # noqa: F821

# FastAPI dependencies
get_session  # noqa: F821
get_current_user  # noqa: F821
get_settings  # noqa: F821

# Settings properties
keycloak_issuer  # noqa: F821
keycloak_jwks_url  # noqa: F821
keycloak_token_url  # noqa: F821

# pytest fixtures
database_url  # noqa: F821
engine  # noqa: F821
setup_database  # noqa: F821
connection  # noqa: F821
session  # noqa: F821

# FastAPI route handlers (registered via decorators)
health_check  # noqa: F821
readiness_check  # noqa: F821
list_projects  # noqa: F821
create_project  # noqa: F821
get_project  # noqa: F821
update_project  # noqa: F821
delete_project  # noqa: F821
root  # noqa: F821
websocket_endpoint  # noqa: F821
auth_login  # noqa: F821
auth_callback  # noqa: F821
auth_logout  # noqa: F821
home  # noqa: F821
workspace_new  # noqa: F821
workspace_create  # noqa: F821
workspace_detail  # noqa: F821
project_new  # noqa: F821
project_create  # noqa: F821
project_editor  # noqa: F821
project_tree  # noqa: F821
project_entity_form  # noqa: F821
project_entity_create  # noqa: F821
project_entity_edit  # noqa: F821
project_entity_delete  # noqa: F821
privacy_policy  # noqa: F821
acceptable_use_policy  # noqa: F821
project_chat  # noqa: F821
project_metaseed_ui  # noqa: F821

# WebSocket manager methods called dynamically
send_to_connection  # noqa: F821
get_room_presence  # noqa: F821

# Model fields used by SQLAlchemy ORM
display_name  # noqa: F821
team_id  # noqa: F821
role  # noqa: F821
entity_id  # noqa: F821
content  # noqa: F821
roles  # noqa: F821
