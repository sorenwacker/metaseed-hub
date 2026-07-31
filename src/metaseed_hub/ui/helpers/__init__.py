"""Shared helper functions for Hub UI routes.

This package was split out of a single ``helpers`` module by concern (CSRF,
uploads, entity tree, table rendering, dataset state, text). The public names are
re-exported here so existing ``from metaseed_hub.ui.helpers import ...`` imports
keep working.
"""

from metaseed_hub.ui.helpers.csrf import (
    CSRF_TOKEN_COOKIE,
    _csrf_signature_valid,
    _sign_csrf,
    get_or_create_csrf_token,
    set_csrf_cookie,
    validate_csrf_token,
)
from metaseed_hub.ui.helpers.dataset_state import (
    ensure_dataset_facade,
    save_dataset_state,
)
from metaseed_hub.ui.helpers.entity_import import (
    add_entities_in_order,
    group_entities_by_type,
)
from metaseed_hub.ui.helpers.load_report import (
    SKIPPED_NODE_RULE,
    skipped_node_issues,
    skipped_node_message,
)
from metaseed_hub.ui.helpers.tables import (
    build_entity_form_context,
    build_inline_tables,
)
from metaseed_hub.ui.helpers.text import (
    escape_pattern_hyphen,
    humanize_field_name,
)
from metaseed_hub.ui.helpers.tree import (
    add_entity_node,
    create_nested_nodes,
    get_tree_data_from_nodes,
    make_json_serializable,
    serialize_tree,
)
from metaseed_hub.ui.helpers.uploads import (
    MAX_UPLOAD_BYTES,
    parse_workbook_sheets,
    read_upload_capped,
)

__all__ = [
    "CSRF_TOKEN_COOKIE",
    "MAX_UPLOAD_BYTES",
    "SKIPPED_NODE_RULE",
    "_csrf_signature_valid",
    "_sign_csrf",
    "add_entities_in_order",
    "add_entity_node",
    "build_entity_form_context",
    "build_inline_tables",
    "create_nested_nodes",
    "ensure_dataset_facade",
    "escape_pattern_hyphen",
    "get_or_create_csrf_token",
    "group_entities_by_type",
    "set_csrf_cookie",
    "get_tree_data_from_nodes",
    "humanize_field_name",
    "make_json_serializable",
    "parse_workbook_sheets",
    "read_upload_capped",
    "save_dataset_state",
    "serialize_tree",
    "skipped_node_issues",
    "skipped_node_message",
    "validate_csrf_token",
]
