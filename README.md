# Metaseed Hub

<img src="src/metaseed_hub/ui/static/images/metaseed-logo.svg" alt="Metaseed Logo" width="300">

Collaborative metadata management platform for scientific research data. Built on [metaseed](https://github.com/your-org/metaseed), it enables teams to create, edit, and share standardized metadata following MIAPPE, ISA, DiSSCo, and Darwin Core specifications.

## Features

- Multi-tenant workspaces and projects
- Dynamic entity forms generated from metaseed specs
- Support for all metaseed profiles (MIAPPE, ISA, DiSSCo, Darwin Core)
- Real-time collaboration with presence and chat
- OIDC authentication (Keycloak, SRAM, SURFconext)
- Botanical-themed UI matching metaseed design

## Prerequisites

- Python 3.11+
- Docker and Docker Compose
- uv (Python package manager)

## Quick Start

### 1. Start Infrastructure Services

```bash
docker compose up -d
```

This starts:
- PostgreSQL on port 7432
- Keycloak on port 7080
- Redis on port 7379

### 2. Configure Keycloak

1. Access Keycloak admin console at http://localhost:7080
2. Login with admin/admin
3. Create a new realm named `metaseed`
4. Create a client named `metaseed-hub` with:
   - Client authentication: ON
   - Valid redirect URIs: `http://localhost:7001/hub/auth/callback`
   - Web origins: `http://localhost:7001`

### 3. Environment Configuration

```bash
cp .env.example .env
```

Edit `.env` with your Keycloak client secret:

```
DATABASE_URL=postgresql+asyncpg://metaseed:metaseed@localhost:7432/metaseed
KEYCLOAK_ISSUER=http://localhost:7080/realms/metaseed
KEYCLOAK_CLIENT_ID=metaseed-hub
KEYCLOAK_CLIENT_SECRET=your-client-secret
APP_URL=http://localhost:7001
```

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
uv run uvicorn metaseed_hub.main:app --host 0.0.0.0 --port 7001 --reload
```

Access the Hub at http://localhost:7001/hub/

## Usage

1. **Login** via your identity provider (Keycloak/SRAM)
2. **Create a Workspace** to organize your projects
3. **Create a Project** selecting a profile (MIAPPE, ISA, DiSSCo, Darwin Core) and version
4. **Add Entities** using the dynamically generated forms
5. **Collaborate** with team members in real-time

## API Endpoints

| Endpoint | Description |
|----------|-------------|
| `/hub/` | Hub UI home |
| `/hub/auth/login` | OIDC login |
| `/hub/privacy` | Privacy policy |
| `/hub/aup` | Acceptable use policy |
| `/api/health` | Health check |
| `/api/projects` | Projects REST API |
| `/docs` | OpenAPI documentation |

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

### Code Quality

```bash
uv run pre-commit run --all-files
```

This runs:
- ruff (lint + format)
- mypy (type checking)
- vulture (dead code detection)

### Creating Migrations

```bash
uv run alembic revision --autogenerate -m "Description"
uv run alembic upgrade head
```

## Architecture

See [ARCHITECTURE.md](ARCHITECTURE.md) for detailed documentation.

### Data Model

- **Tenant**: Multi-tenant organization boundary
- **Workspace**: Container for related projects
- **Project**: Metaseed project with profile, version, and entity data
- **User/Team**: Collaboration and access control

### Tech Stack

| Layer | Technology |
|-------|------------|
| Backend | FastAPI + SQLAlchemy (async) |
| Database | PostgreSQL |
| Auth | Keycloak / SRAM (OIDC) |
| Frontend | HTMX + Jinja2 |
| Real-time | WebSockets + Redis |

## Production Deployment

For production (e.g., `https://metaseed.ewi.tudelft.nl`):

1. Update `.env` with production URLs
2. Set `secure=True` for cookies in `app.py`
3. Configure reverse proxy (nginx) with HTTPS
4. Use production Keycloak/SRAM realm

## License

[Add license information]

## Contributing

[Add contribution guidelines]
