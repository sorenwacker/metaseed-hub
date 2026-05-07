# Metaseed Hub

[![CI](https://github.com/sorenwacker/metaseed-hub/actions/workflows/ci.yml/badge.svg)](https://github.com/sorenwacker/metaseed-hub/actions/workflows/ci.yml)
[![Python 3.11+](https://img.shields.io/badge/python-3.11+-blue.svg)](https://www.python.org/downloads/)
[![License](https://img.shields.io/badge/license-Apache%202.0-green.svg)](LICENSE)

<img src="src/metaseed_hub/ui/static/images/metaseed-logo.svg" alt="Metaseed Logo" width="300">

Collaborative metadata management platform for scientific research data. Built on [metaseed](https://github.com/sorenwacker/metaseed), it enables teams to create, edit, and share standardized metadata following MIAPPE, ISA, DiSSCo, and Darwin Core specifications.

**Live instance:** https://metaseed.ewi.tudelft.nl

## Features

- Multi-tenant workspaces and projects
- Dynamic entity forms generated from metaseed specs
- Support for MIAPPE, ISA, DiSSCo, and Darwin Core profiles
- Real-time collaboration with presence and chat
- OIDC authentication (Keycloak, SRAM, SURFconext)

## Prerequisites

- Python 3.11+
- Docker and Docker Compose
- uv (Python package manager)

## Quick Start

```bash
make dev
```

This starts all services (PostgreSQL, Keycloak, Redis), runs migrations, and launches the app at http://localhost:7001/hub/

Default login: `demo@example.com` / `demo123`

## Usage

1. Login via Keycloak
2. Create a Workspace to organize projects
3. Create a Project with a profile (MIAPPE, ISA, DiSSCo, Darwin Core)
4. Add entities using the generated forms
5. Export data or visualize as graph

### Import and Export

**Export to Excel:**
Click the Export button in the project toolbar to download all entities as an Excel workbook. Each entity type gets its own worksheet.

**Import ISA-JSON:**
Click the Import button to upload ISA-JSON files. The importer parses Investigation, Study, and Assay structures and creates corresponding entities in the project.

## Development

```bash
# Install dependencies
uv sync --dev

# Set up pre-commit hooks
uv run pre-commit install

# Run tests
uv run pytest

# Code quality
uv run pre-commit run --all-files
```

## Architecture

See [ARCHITECTURE.md](ARCHITECTURE.md) for details.

| Layer | Technology |
|-------|------------|
| Backend | FastAPI + SQLAlchemy (async) |
| Database | PostgreSQL |
| Auth | Keycloak / SRAM (OIDC) |
| Frontend | HTMX + Jinja2 |
| Real-time | WebSockets + Redis |

## License

Apache 2.0
