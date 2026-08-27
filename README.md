<img src="src/metaseed_hub/ui/static/images/metaseed-logo.svg" alt="Metaseed Hub" width="300">

# Metaseed Hub

[![CI](https://github.com/sorenwacker/metaseed-hub/actions/workflows/ci.yml/badge.svg)](https://github.com/sorenwacker/metaseed-hub/actions/workflows/ci.yml)
[![Python 3.11+](https://img.shields.io/badge/python-3.11%2B-blue.svg)](https://www.python.org/downloads/)
[![License: MIT](https://img.shields.io/badge/license-MIT-green.svg)](LICENSE)

Metaseed Hub is the shared, deployed counterpart of [metaseed](https://github.com/sorenwacker/metaseed): a web application where a group creates, edits, shares, and publishes standardized research metadata. Metaseed runs on one machine for one person; the hub runs for a team, with accounts, sharing, and a place to publish specifications.

[Documentation](https://sorenwacker.github.io/metaseed-hub/) · [Live instance](https://metaseed.ewi.tudelft.nl) · [Changelog](CHANGELOG.md)

## What it does

- **Datasets**: entity forms and tables generated from a profile, validation against it, a graph view, Excel and ISA-JSON import and export, and a DCAT catalogue record.
- **Profiles**: every standard metaseed ships (MIAPPE, ISA, DiSSCo, Darwin Core, ENA, PRIDE, MetaboLights, FAIRDOM-SEEK), plus a spec builder for your own. A specification stays a private draft until its author publishes it; publishing goes through a version-bump gate, so a release can't silently break the datasets built on it.
- **Sharing**: datasets and drafts shared by email with a viewer or editor role; a published specification is available to every user of the hub.
- **FAIRDOM-SEEK**: push a dataset into a SEEK instance with your own API key, checked before it's sent.
- **Access for tools**: an MCP server for AI agents and a REST API, both authenticated with a personal access token that acts as you and only you. A metaseed instance uses that API to push and pull datasets and profiles ([guide](https://sorenwacker.github.io/metaseed/guides/hub-sync/)).
- **Operations**: OIDC sign-in (Keycloak, SRAM, SURFconext), an admin dashboard, scheduled database backups with retention, and pinned container images updated on a schedule.

## Run it locally

You need Python 3.11 or later, [uv](https://docs.astral.sh/uv/), and Docker with Compose.

```bash
make dev
```

This starts PostgreSQL, Keycloak, and Redis in containers, runs the database migrations, and serves the hub at http://localhost:7001/hub/. The development realm has a ready account: `demo@example.com` with password `demo123`.

## Use it

1. Sign in.
2. On **Datasets**, create a dataset and choose its profile; the built-in profiles and every published specification are offered.
3. Add entities in the generated forms or tables. Validation reports what is still missing.
4. Share the dataset from its **Sharing** panel, export it, or push it to SEEK.

To author a specification, open **Spec builder**, start from scratch, from an existing profile, or from an uploaded YAML document; **Publish** when it's ready for others. To let a script or an agent act as you, create a token under **Access tokens** on your profile; the [MCP guide](https://sorenwacker.github.io/metaseed-hub/mcp/) shows how to connect one.

## Development

```bash
uv sync --extra dev             # dependencies
uv run pre-commit install       # the same checks CI runs, before each commit
make test                       # the test suite (needs the containers from make dev)
uv run pre-commit run --all-files
```

The project follows document-driven and test-driven development: a change starts in `docs/`, gets a test, then an implementation. Rules are enforced by tests, not by review.

## Architecture

| Layer | Technology |
|-------|------------|
| Application | FastAPI with async SQLAlchemy |
| Metadata engine | [metaseed](https://github.com/sorenwacker/metaseed) — profiles, models, validation, exports |
| Database | PostgreSQL, migrated with Alembic |
| Sign-in | OIDC through Keycloak, SRAM, or SURFconext |
| Interface | Jinja2 templates with HTMX |
| Real-time | WebSockets with Redis |
| Deployment | Docker Compose on a host provisioned with Ansible, deployed on every release tag |

[ARCHITECTURE.md](ARCHITECTURE.md) and the [developer documentation](https://sorenwacker.github.io/metaseed-hub/developer/architecture/) go deeper.

## License

[MIT](LICENSE)
