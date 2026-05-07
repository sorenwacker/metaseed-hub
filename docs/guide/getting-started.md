# Getting Started

## Prerequisites

- Python 3.11+
- Docker and Docker Compose
- uv (Python package manager)

## Installation

Clone the repository:

```bash
git clone https://github.com/sorenwacker/metaseed-hub.git
cd metaseed-hub
```

Install dependencies:

```bash
uv sync --dev
```

## Running Locally

Start all services:

```bash
make dev
```

This command:

1. Starts PostgreSQL, Keycloak, and Redis containers
2. Runs database migrations
3. Launches the application at http://localhost:7001/hub/

## Default Credentials

| Service | Username | Password |
|---------|----------|----------|
| Hub | demo@example.com | demo123 |
| Keycloak Admin | admin | admin |

## First Steps

1. Navigate to http://localhost:7001/hub/
2. Login with the demo account
3. Create a workspace
4. Create a project with a profile (MIAPPE, ISA, DiSSCo, Darwin Core)
5. Add entities using the forms
