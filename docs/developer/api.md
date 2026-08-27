# API Reference

## REST API (`/api`, bearer token)

| Method | Path | Description |
|--------|------|-------------|
| GET | `/api/me` | The account and tenant the token acts in |
| GET | `/api/datasets` | The caller's datasets in a tenant (`?tenant_id=`) |
| POST | `/api/datasets` | Create a dataset |
| GET | `/api/datasets/{id}` | One dataset with its entities |
| PATCH | `/api/datasets/{id}` | Replace a dataset's name or entities |
| DELETE | `/api/datasets/{id}` | Soft-delete a dataset |
| GET | `/api/specs` | Published specifications |
| GET | `/api/specs/{name}/{version}` | One published specification as YAML |
| POST | `/api/specs` | Push a profile document (`{"yaml": ...}`) as a private draft; `"publish": true` publishes it, 409 under the version-bump gate |
| POST | `/api/specs/{id}/unpublish` | Withdraw a published specification to a private draft |

## Hub UI Routes

### Accounts

| Method | Path | Description |
|--------|------|-------------|
| GET | `/hub/` | List accounts |
| GET | `/hub/accounts/new` | New account form |
| POST | `/hub/accounts` | Create account |
| GET | `/hub/accounts/{id}` | Account detail |

### Projects

| Method | Path | Description |
|--------|------|-------------|
| GET | `/hub/projects/new` | New project form |
| POST | `/hub/projects` | Create project |
| GET | `/hub/projects/{id}` | Project editor |
| DELETE | `/hub/projects/{id}` | Delete project |

### Entities

| Method | Path | Description |
|--------|------|-------------|
| GET | `/hub/projects/{id}/form/{type}` | Entity form |
| POST | `/hub/projects/{id}/entities` | Create/update entity |
| GET | `/hub/projects/{id}/entity/{node_id}` | Edit entity |
| DELETE | `/hub/projects/{id}/entity/{node_id}` | Delete entity |

### Export

| Method | Path | Description |
|--------|------|-------------|
| GET | `/hub/projects/{id}/export` | Download Excel |

### Visualization

| Method | Path | Description |
|--------|------|-------------|
| GET | `/hub/projects/{id}/graph` | Graph view page |
| GET | `/hub/projects/{id}/api/graph` | Graph data (JSON) |

### Validation

| Method | Path | Description |
|--------|------|-------------|
| POST | `/hub/projects/{id}/validate` | Validate all entities |

## WebSocket

Connect to `/ws/{project_id}` for real-time collaboration.

Message types:

- `presence` - User join/leave
- `chat` - Chat messages
- `cursor` - Field focus indicators
- `update` - Entity changes
