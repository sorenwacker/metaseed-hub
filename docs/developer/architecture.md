# Architecture

## Tech Stack

| Layer | Technology |
|-------|------------|
| Backend | FastAPI + SQLAlchemy (async) |
| Database | PostgreSQL |
| Auth | Keycloak (OIDC) |
| Frontend | HTMX + Jinja2 |
| Real-time | WebSockets + Redis |

## Core Entities

```
Tenant (organization)
  └── Workspace (container)
        └── Project (profile: miappe, isa, dissco, dwc)
              └── Entities (Investigation, Study, etc.)
```

## Database Schema

Projects store entity data as JSONB:

```sql
CREATE TABLE projects (
    id UUID PRIMARY KEY,
    workspace_id UUID NOT NULL,
    name VARCHAR(255) NOT NULL,
    profile VARCHAR(100) NOT NULL,
    version VARCHAR(50) NOT NULL,
    data JSONB NOT NULL DEFAULT '{}'
);
```

## Dataset persistence

`dataset.data` (JSONB) stores a `{profile, version, tree: [...]}` envelope produced by metaseed's `MetaseedClient.serialize(format="tree")`. Each tree node carries `id`, `entity_type`, `label`, `data`, and `children`.

The metaseed `ProfileFacade` is the single source of truth for entity data. `TreeNode`/`AppState` caches are derived views for template rendering; they are never written independently of the facade.

- **Load**: `ensure_dataset_facade` (ui/helpers/dataset_state.py) resolves the client for the dataset's schema (built-in profile, draft spec, or published spec), sanitizes the stored payload with `sanitize_tree_payload` (drops nodes whose entity type the schema does not define, fills missing node ids), and populates the facade with `client.load`. Loading is permissive: entities are reconstructed with `skip_validation`/`model_construct`, so incomplete drafts load without loss and legacy payloads with unknown field names load without failing. If stored entity data cannot be loaded, `DatasetDataLoadError` is raised instead of returning an empty state, because a later save from an empty state would overwrite the stored tree.
- **Mutate**: all writes go through the facade — `AppState.add_node`/`update_node`/`delete_node` (which keep the caches consistent) or `EntityService`, which wraps a `MetaseedClient` directly.
- **Save**: `save_dataset_state` serializes via `serialize_tree`, which delegates to `MetaseedClient.from_facade(state.facade).serialize(format="tree")` — the same serializer `EntityService` uses, so there is exactly one write format. `serialize_tree` refuses (raises) if the TreeNode cache holds nodes missing from the facade, because serializing would silently drop them.

Every save that changes `dataset.data` also records a `DatasetVersion` row (see the versions feature).

## Integration with metaseed

Metaseed Hub uses metaseed as a library:

```python
from metaseed.ui.state import AppState
from metaseed.ui.services.export import export_to_bytes
from metaseed.ui.services.graph import build_graph
```

Key integrations:

| Service | Module | Purpose |
|---------|--------|---------|
| Entity forms | `metaseed.facade` | Dynamic form generation |
| Validation | `metaseed.validators` | Schema validation |
| Export | `metaseed.ui.services.export` | Excel export |
| Graph | `metaseed.ui.services.graph` | Visualization data |
