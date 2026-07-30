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

Dependency rule: routes and templates call hub helpers (`ui/helpers/`, `ui/services/`), and the helpers call metaseed's public API (`MetaseedClient`, `ProfileFacade`). Imports of metaseed's internal UI layer (`metaseed.ui`) are allowed only in the designated boundary module `ui/metaseed_ui.py`, which re-exports what the hub still uses: `AppState`/`TreeNode` (the request-scoped entity-tree cache derived from the facade) and the packaged template/static directories. The gate test `tests/test_metaseed_coupling.py` scans every module under `src/metaseed_hub` and fails on any other `metaseed.ui` import.

## Integration with metaseed

Metaseed Hub uses metaseed as a library through its public API:

```python
from metaseed import MetaseedClient, ProfileFacade
```

Key integrations:

| Service | Module | Purpose |
|---------|--------|---------|
| Entity data | `metaseed` (`MetaseedClient`, `ProfileFacade`) | Load, mutate, serialize entities |
| Entity forms | `metaseed.facade` | Dynamic form generation |
| Validation | `metaseed.validators` | Schema validation |
| Export | `metaseed_hub.ui.services.export` | Excel export over the facade API |
| Graph | `metaseed_hub.ui.services.graph` | Visualization data via `ProfileFacade.to_graph` |
| UI internals | `metaseed_hub.ui.metaseed_ui` | Sole boundary to `metaseed.ui` (`AppState`, assets) |
