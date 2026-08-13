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

# pytest fixtures
database_url  # noqa: F821
engine  # noqa: F821
connection  # noqa: F821
session  # noqa: F821

# FastAPI route handlers (registered via decorators)
health_check  # noqa: F821
readiness_check  # noqa: F821
root  # noqa: F821
websocket_endpoint  # noqa: F821
auth_login  # noqa: F821
auth_callback  # noqa: F821
auth_logout  # noqa: F821
home  # noqa: F821
privacy_policy  # noqa: F821
acceptable_use_policy  # noqa: F821

# Model fields used by SQLAlchemy ORM
display_name  # noqa: F821
team_id  # noqa: F821
role  # noqa: F821
entity_id  # noqa: F821
content  # noqa: F821
roles  # noqa: F821

# MCP rule-tool parameters read through locals() by _rule_attributes, which is
# how the tools are gated against metaseed's RULE_ATTRIBUTE_NAMES rather than
# listing every attribute a third time.
when  # noqa: F821
require  # noqa: F821
lat_field  # noqa: F821
lon_field  # noqa: F821
start_field  # noqa: F821
end_field  # noqa: F821
