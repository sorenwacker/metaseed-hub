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
    get_dataset_state,
    save_dataset_state,
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
    deserialize_tree,
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
    "_csrf_signature_valid",
    "_sign_csrf",
    "add_entity_node",
    "build_entity_form_context",
    "build_inline_tables",
    "create_nested_nodes",
    "deserialize_tree",
    "ensure_dataset_facade",
    "escape_pattern_hyphen",
    "get_dataset_state",
    "get_or_create_csrf_token",
    "set_csrf_cookie",
    "get_tree_data_from_nodes",
    "humanize_field_name",
    "make_json_serializable",
    "parse_workbook_sheets",
    "read_upload_capped",
    "save_dataset_state",
    "serialize_tree",
    "validate_csrf_token",
]
