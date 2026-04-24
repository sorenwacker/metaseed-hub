# Metaseed Hub

Collaborative hub for metaseed projects with real-time features.

## Prerequisites

- Python 3.11+
- Docker and Docker Compose
- uv (Python package manager)

## Setup

### 1. Start Infrastructure Services

```bash
docker-compose up -d
```

This starts:
- PostgreSQL 16 on port 5432
- Keycloak on port 8080
- Redis on port 6379

### 2. Configure Keycloak

1. Access Keycloak admin console at http://localhost:8080
2. Login with admin/admin
3. Create a new realm named `metaseed`
4. Create a client named `metaseed-hub` with:
   - Client authentication: ON
   - Valid redirect URIs: http://localhost:3000/*
   - Web origins: http://localhost:3000

### 3. Environment Configuration

```bash
cp .env.example .env
```

Edit `.env` with your Keycloak client secret and other settings.

### 4. Install Dependencies

```bash
uv sync
```

### 5. Run Database Migrations

```bash
uv run alembic upgrade head
```

### 6. Start the Application

```bash
uv run uvicorn metaseed_hub.main:app --reload
```

The API is available at http://localhost:8000

## API Documentation

- OpenAPI docs: http://localhost:8000/docs
- ReDoc: http://localhost:8000/redoc

## Development

### Install Development Dependencies

```bash
uv sync --dev
```

### Set Up Pre-commit Hooks

```bash
uv run pre-commit install
```

### Running Tests

```bash
uv run pytest
```

### Creating Migrations

```bash
uv run alembic revision --autogenerate -m "Description"
```

### Code Quality

```bash
uv run ruff check .
uv run mypy src
uv run vulture src/ vulture_whitelist.py
```

### Manual Pre-commit Check

```bash
uv run pre-commit run --all-files
```

## Architecture

### Models

- **Tenant**: Multi-tenant organization
- **Team**: Groups within a tenant
- **User**: Application user linked to Keycloak
- **TeamMembership**: User-team association with roles
- **Workspace**: Container for projects
- **Project**: Metaseed project with JSONB data
- **Note**: Annotations on project entities
- **ChatMessage**: Real-time chat within projects

### WebSocket

Connect to `/ws/{project_id}?token={jwt_token}` for real-time features:
- Presence tracking (join/leave events)
- Chat messages
- Project updates

### Authentication

Uses Keycloak OIDC. Include JWT token in:
- REST API: `Authorization: Bearer {token}`
- WebSocket: `?token={token}` query parameter
