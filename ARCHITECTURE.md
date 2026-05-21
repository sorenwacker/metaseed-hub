# metaseed-hub Architecture

Collaborative metadata platform built on top of metaseed.

## Overview

metaseed-hub extends metaseed with multi-user collaboration, authentication, and real-time features for teams working on scientific metadata.

## Tech Stack

| Layer | Technology | Purpose |
|-------|------------|---------|
| Auth | Keycloak (OIDC) | Identity management, SSO |
| Backend | FastAPI + SQLAlchemy | Async API, ORM |
| Database | PostgreSQL | Persistent storage |
| Real-time | WebSockets + Redis | Live updates, chat |
| Task Queue | Celery + Redis | Background jobs |
| Frontend | HTMX + Alpine.js | Interactive UI |

## Deployment Model

- **Self-hosted (on-premise)**: Organizations deploy their own instance
- Each organization connects their own Keycloak realm
- PostgreSQL for data persistence

## Core Entities

```
┌─────────────────────────────────────────────────────────────────┐
│                           TENANT                                │
│  (Organization - isolated data boundary)                        │
├─────────────────────────────────────────────────────────────────┤
│                                                                 │
│   ┌─────────────┐          ┌─────────────┐                     │
│   │    Team     │◄────────►│    User     │                     │
│   │             │  member  │ (Keycloak)  │                     │
│   └──────┬──────┘          └─────────────┘                     │
│          │                                                      │
│          │ owns                                                 │
│          ▼                                                      │
│   ┌─────────────┐                                              │
│   │  Workspace  │  (Container for projects)                    │
│   └──────┬──────┘                                              │
│          │                                                      │
│          │ contains                                             │
│          ▼                                                      │
│   ┌─────────────┐     ┌─────────────┐     ┌─────────────┐     │
│   │   Project   │────►│    Note     │     │    Chat     │     │
│   │ (profile:   │     │ (per entity)│     │  (realtime) │     │
│   │  miappe,    │     └─────────────┘     └─────────────┘     │
│   │  isa, etc)  │                                              │
│   └─────────────┘                                              │
│                                                                 │
└─────────────────────────────────────────────────────────────────┘
```

## Database Schema

### Design Principles

- **Tenant isolation**: Users, teams, workspaces exist within tenant boundaries
- **Tenant-scoped uniqueness**: Email and keycloak_id unique per tenant (not globally)
- **Composite uniqueness**: Names unique within their parent scope (team per tenant, project per workspace)
- **Consistent timestamps**: All models have created_at and updated_at
- **Soft delete**: Tenants, Users, Teams, Workspaces, Projects support soft delete via deleted_at
- **Index strategy**: Foreign keys and frequently queried columns are indexed

### Tenant
```sql
CREATE TABLE tenants (
    id UUID PRIMARY KEY,
    name VARCHAR(255) NOT NULL,
    slug VARCHAR(100) UNIQUE NOT NULL,
    created_at TIMESTAMPTZ NOT NULL DEFAULT NOW(),
    updated_at TIMESTAMPTZ NOT NULL DEFAULT NOW(),
    deleted_at TIMESTAMPTZ
);
```

### Team
```sql
CREATE TABLE teams (
    id UUID PRIMARY KEY,
    tenant_id UUID NOT NULL REFERENCES tenants(id) ON DELETE CASCADE,
    name VARCHAR(255) NOT NULL,
    created_at TIMESTAMPTZ NOT NULL DEFAULT NOW(),
    updated_at TIMESTAMPTZ NOT NULL DEFAULT NOW(),
    deleted_at TIMESTAMPTZ,
    UNIQUE (tenant_id, name)
);
CREATE INDEX ix_teams_tenant_id ON teams(tenant_id);
```

### User
```sql
CREATE TABLE users (
    id UUID PRIMARY KEY,
    keycloak_id VARCHAR(255) NOT NULL,
    email VARCHAR(255) NOT NULL,
    display_name VARCHAR(255) NOT NULL,
    tenant_id UUID NOT NULL REFERENCES tenants(id) ON DELETE CASCADE,
    created_at TIMESTAMPTZ NOT NULL DEFAULT NOW(),
    updated_at TIMESTAMPTZ NOT NULL DEFAULT NOW(),
    deleted_at TIMESTAMPTZ,
    UNIQUE (tenant_id, keycloak_id),
    UNIQUE (tenant_id, email)
);
CREATE INDEX ix_users_tenant_id ON users(tenant_id);
```

### TeamMembership
```sql
CREATE TABLE team_memberships (
    user_id UUID REFERENCES users(id) ON DELETE CASCADE,
    team_id UUID REFERENCES teams(id) ON DELETE CASCADE,
    role VARCHAR(20) NOT NULL CHECK (role IN ('owner', 'admin', 'member')),
    created_at TIMESTAMPTZ NOT NULL DEFAULT NOW(),
    PRIMARY KEY (user_id, team_id)
);
```

### Workspace
```sql
CREATE TABLE workspaces (
    id UUID PRIMARY KEY,
    tenant_id UUID NOT NULL REFERENCES tenants(id) ON DELETE CASCADE,
    name VARCHAR(255) NOT NULL,
    description TEXT,
    created_at TIMESTAMPTZ NOT NULL DEFAULT NOW(),
    updated_at TIMESTAMPTZ NOT NULL DEFAULT NOW(),
    deleted_at TIMESTAMPTZ,
    UNIQUE (tenant_id, name)
);
CREATE INDEX ix_workspaces_tenant_id ON workspaces(tenant_id);
```

### Project
```sql
CREATE TABLE projects (
    id UUID PRIMARY KEY,
    workspace_id UUID NOT NULL REFERENCES workspaces(id) ON DELETE CASCADE,
    name VARCHAR(255) NOT NULL,
    profile VARCHAR(100) NOT NULL,  -- miappe, isa, dissco
    version VARCHAR(50) NOT NULL,   -- 1.1, 2.0, etc
    data JSONB NOT NULL DEFAULT '{}',
    created_at TIMESTAMPTZ NOT NULL DEFAULT NOW(),
    updated_at TIMESTAMPTZ NOT NULL DEFAULT NOW(),
    deleted_at TIMESTAMPTZ,
    UNIQUE (workspace_id, name)
);
CREATE INDEX ix_projects_workspace_id ON projects(workspace_id);
```

### Note
```sql
CREATE TABLE notes (
    id UUID PRIMARY KEY,
    project_id UUID NOT NULL REFERENCES projects(id) ON DELETE CASCADE,
    user_id UUID NOT NULL REFERENCES users(id) ON DELETE CASCADE,
    entity_type VARCHAR(100) NOT NULL,  -- Investigation, Study, etc
    entity_id VARCHAR(255) NOT NULL,    -- Identifier within the project
    content TEXT NOT NULL,
    created_at TIMESTAMPTZ NOT NULL DEFAULT NOW(),
    updated_at TIMESTAMPTZ NOT NULL DEFAULT NOW()
);
CREATE INDEX ix_notes_project_entity ON notes(project_id, entity_type, entity_id);
```

### ChatMessage
```sql
CREATE TABLE chat_messages (
    id UUID PRIMARY KEY,
    project_id UUID NOT NULL REFERENCES projects(id) ON DELETE CASCADE,
    user_id UUID NOT NULL REFERENCES users(id) ON DELETE CASCADE,
    content TEXT NOT NULL,
    created_at TIMESTAMPTZ NOT NULL DEFAULT NOW(),
    updated_at TIMESTAMPTZ NOT NULL DEFAULT NOW()
);
CREATE INDEX ix_chat_messages_project_id ON chat_messages(project_id);
CREATE INDEX ix_chat_messages_created_at ON chat_messages(created_at);
```

## Integration with metaseed

metaseed-hub uses metaseed as a library:

```python
from metaseed.facade import ProfileFacade
from metaseed.models import get_model
from metaseed.validators import validate

# Create facade for a project's profile
facade = ProfileFacade(profile="miappe", version="1.1")

# Access entity helpers
investigation = facade.Investigation.create(
    unique_id="inv-001",
    title="My Investigation"
)

# Validate data
errors = validate(data, "Investigation", version="1.1")
```

## API Structure

```
/api
├── /health              GET     Health check
├── /auth
│   ├── /me              GET     Current user info
│   └── /logout          POST    Logout
├── /tenants
│   └── /{id}            GET     Tenant details
├── /teams
│   ├── /                GET     List teams
│   ├── /                POST    Create team
│   └── /{id}
│       ├── /            GET     Team details
│       ├── /members     GET     List members
│       └── /members     POST    Add member
├── /workspaces
│   ├── /                GET     List workspaces
│   ├── /                POST    Create workspace
│   └── /{id}            GET     Workspace details
├── /projects
│   ├── /                GET     List projects
│   ├── /                POST    Create project
│   └── /{id}
│       ├── /            GET     Project details
│       ├── /            PATCH   Update project
│       ├── /            DELETE  Delete project
│       ├── /entities    GET     List entities (from metaseed)
│       ├── /validate    POST    Validate project data
│       ├── /export      GET     Export to Excel
│       ├── /notes       GET     List notes
│       ├── /notes       POST    Create note
│       └── /chat        GET     Chat history
└── /ws
    └── /{project_id}    WS      Real-time collaboration
```

## WebSocket Protocol

### Connection
```
WS /ws/{project_id}?token={jwt_token}
```

### Message Types

**Client → Server:**
```json
{"type": "presence", "action": "join"}
{"type": "chat", "content": "Hello team!"}
{"type": "cursor", "entity_id": "study-001", "field": "title"}
{"type": "edit", "entity_id": "study-001", "field": "title", "value": "..."}
```

**Server → Client:**
```json
{"type": "presence", "user": "alice@example.com", "action": "joined"}
{"type": "chat", "user": "bob@example.com", "content": "Hello!", "timestamp": "..."}
{"type": "cursor", "user": "alice@example.com", "entity_id": "study-001", "field": "title"}
{"type": "update", "entity_id": "study-001", "data": {...}}
```

## Authentication Flow

1. User accesses metaseed-hub
2. Redirect to Keycloak login
3. Keycloak authenticates, returns JWT
4. JWT sent with every API request
5. Backend verifies JWT against Keycloak JWKS
6. User info extracted from JWT claims

## Hub UI

The Hub provides an HTMX-powered interface for managing workspaces, projects, and entities.

### UI Routes

```
/hub
├── /                           Home - list workspaces
├── /auth/login                 Redirect to Keycloak
├── /auth/callback              OAuth callback
├── /auth/logout                Logout
├── /workspaces/new             Workspace creation form
├── /workspaces                 POST create workspace
├── /workspaces/{id}            Workspace detail - list projects
├── /projects/new               Project creation form (dynamic profiles)
├── /projects                   POST create project
├── /projects/{id}              Project editor with entity tree
├── /projects/{id}/tree         Entity tree (HTMX partial)
├── /projects/{id}/form/{type}  Entity creation/edit form
├── /projects/{id}/entities     POST create/update entity
├── /projects/{id}/entity/{id}  Entity edit form
└── /projects/{id}/chat         POST chat message
```

### Design System

The UI uses metaseed's botanical design theme:
- **Colors**: Forest greens, sage accents, parchment backgrounds
- **Typography**: Fraunces (display), DM Sans (body)
- **Components**: Cards with gradient borders, collapsible sections, inline-block fields

### Entity Form Features

- Dynamic form generation from metaseed specs
- Required/Optional field sections
- Collapsible optional fields
- Nested entity placeholders
- Real-time tree updates via HX-Trigger

### Graph Visualization

The Hub includes a graph view for visualizing entity relationships using vis-network.

**Access Points:**
- **Project Graph**: Click "Graph View" button in project header (opens in new tab)
- **Entity Graph**: Click "View in Graph" on entity form (shows entity and descendants)

**API Endpoint:** `GET /projects/{id}/api/graph?node_id={optional}`

Returns JSON for vis-network:
```json
{
  "nodes": [{"id": "...", "label": "...", "group": "Investigation", "title": "..."}],
  "edges": [{"from": "...", "to": "..."}]
}
```

**Features:**
- Color-coded entity types with legend
- Toggle entity type visibility
- Physics-based or hierarchical layout
- Adjustable spacing and repulsion
- Double-click node to open entity editor

### Import and Export

The Hub reuses metaseed's import/export services for data interchange.

**Export to Excel:**
- **Endpoint:** `GET /projects/{id}/export`
- Uses `metaseed.ui.services.export.export_to_bytes()` and `generate_filename()`
- Returns Excel workbook (.xlsx) with one worksheet per entity type
- Columns match entity field names

### Validation

Entity validation happens at multiple levels:

1. **Form submission**: Pydantic validation via `helper.create(**values)` when saving
2. **Project validation**: "Verify" button validates all entities against schema
3. **Export validation**: Full schema validation before export (planned)

**Validation API:** `POST /projects/{id}/validate`

Returns HTML with validation results:
- Success: "All N entities are valid"
- Errors: List of entities with validation errors, with links to edit each

**Validation Checks:**
- Required fields present
- Type correctness (integer, float, boolean, etc.)
- Constraints (min/max, pattern, enum values)
- Nested entity validation

## File Structure

```
metaseed-hub/
├── src/metaseed_hub/
│   ├── __init__.py
│   ├── main.py              # FastAPI app entry point
│   ├── config.py            # Settings (Keycloak, DB, etc.)
│   ├── database.py          # Centralized session management
│   ├── api/
│   │   ├── __init__.py      # Router registration
│   │   └── projects.py      # Project CRUD API
│   ├── auth/
│   │   └── __init__.py      # Keycloak JWT verification
│   ├── models/
│   │   ├── __init__.py      # SQLAlchemy models
│   │   └── mixins.py        # Timestamp, SoftDelete mixins
│   ├── websocket/
│   │   └── __init__.py      # WebSocket connection manager
│   └── ui/
│       ├── app.py           # Hub UI FastAPI routes
│       ├── static/
│       │   ├── css/hub.css  # Botanical design system
│       │   └── js/hub.js    # UI interactions
│       └── templates/
│           ├── base.html
│           ├── home.html
│           ├── workspace.html
│           ├── project.html
│           ├── login.html
│           └── partials/
│               ├── workspace_form.html
│               ├── project_form.html
│               └── entity_form.html
├── alembic/                  # Database migrations
│   ├── env.py               # Migration config (filters Keycloak tables)
│   └── versions/
├── tests/
│   ├── conftest.py          # Async DB fixtures
│   ├── factories.py         # Model factories
│   └── test_models.py       # Model constraint tests
├── .pre-commit-config.yaml  # ruff, mypy, vulture
├── vulture_whitelist.py     # Dead code false positives
├── docker-compose.yml       # PostgreSQL, Keycloak, Redis
├── pyproject.toml
└── README.md
```
