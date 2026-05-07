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

## Integration with metaseed

Metaseed Hub uses metaseed as a library:

```python
from metaseed.ui.state import AppState
from metaseed.ui.services.export import export_to_bytes
from metaseed.ui.services.graph import build_graph
from metaseed.importers.isa import ISAImporter
```

Key integrations:

| Service | Module | Purpose |
|---------|--------|---------|
| Entity forms | `metaseed.facade` | Dynamic form generation |
| Validation | `metaseed.validators` | Schema validation |
| Export | `metaseed.ui.services.export` | Excel export |
| Graph | `metaseed.ui.services.graph` | Visualization data |
| Import | `metaseed.importers.isa` | ISA-JSON parsing |
