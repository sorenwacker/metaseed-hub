# API Reference

## Hub UI Routes

### Workspaces

| Method | Path | Description |
|--------|------|-------------|
| GET | `/hub/` | List workspaces |
| GET | `/hub/workspaces/new` | New workspace form |
| POST | `/hub/workspaces` | Create workspace |
| GET | `/hub/workspaces/{id}` | Workspace detail |

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
