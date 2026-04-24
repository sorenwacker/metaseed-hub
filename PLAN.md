# metaseed-hub Implementation Plan

## Phase 1: Foundation (Week 1-2)

### 1.1 Project Setup
- [ ] Initialize Python project with pyproject.toml
- [ ] Set up uv for dependency management
- [ ] Configure ruff for linting
- [ ] Set up pytest for testing

### 1.2 Docker Environment
- [ ] Create docker-compose.yml with:
  - PostgreSQL 16
  - Keycloak (dev mode)
  - Redis 7
- [ ] Create .env.example with all config vars
- [ ] Document local dev setup in README.md

### 1.3 Database Models
- [ ] Create SQLAlchemy async models:
  - Tenant
  - Team
  - User
  - TeamMembership
  - Workspace
  - Project
  - Note
  - ChatMessage
- [ ] Set up Alembic for migrations
- [ ] Create initial migration

### 1.4 Configuration
- [ ] Create Pydantic Settings class
- [ ] Environment variable loading
- [ ] Keycloak connection settings
- [ ] Database URL configuration

---

## Phase 2: Authentication (Week 2-3)

### 2.1 Keycloak Integration
- [ ] Install python-keycloak or python-jose
- [ ] Create auth module with:
  - JWT token verification
  - JWKS fetching and caching
  - Token introspection
- [ ] Create FastAPI dependency `get_current_user`

### 2.2 User Provisioning
- [ ] Auto-create User record on first login
- [ ] Link to Keycloak ID
- [ ] Handle tenant assignment (first user creates tenant, or invite flow)

### 2.3 Authorization
- [ ] Role-based access control helpers
- [ ] Tenant isolation middleware
- [ ] Team permission checks

---

## Phase 3: Core API (Week 3-4)

### 3.1 FastAPI App
- [ ] Create main.py with app factory
- [ ] CORS middleware configuration
- [ ] Exception handlers
- [ ] Request ID middleware

### 3.2 API Endpoints
- [ ] Health check endpoints (/health, /ready)
- [ ] Auth endpoints (/auth/me, /auth/logout)
- [ ] Teams CRUD
- [ ] Workspaces CRUD
- [ ] Projects CRUD

### 3.3 metaseed Integration
- [ ] Add metaseed as dependency
- [ ] Create service layer for project operations
- [ ] Entity validation using metaseed validators
- [ ] Profile/version selection per project

---

## Phase 4: Real-time Collaboration (Week 4-5)

### 4.1 WebSocket Infrastructure
- [ ] WebSocket endpoint with JWT auth
- [ ] Connection manager class
- [ ] Room-based messaging (per project)
- [ ] Redis pub/sub for multi-instance support

### 4.2 Presence System
- [ ] Track active users per project
- [ ] Join/leave notifications
- [ ] User cursor positions (optional)

### 4.3 Chat
- [ ] Real-time chat messages
- [ ] Persist to database
- [ ] Load chat history

---

## Phase 5: Notes & Annotations (Week 5-6)

### 5.1 Notes API
- [ ] Create note attached to entity
- [ ] List notes for project/entity
- [ ] Update/delete notes

### 5.2 Entity Context
- [ ] Link notes to specific entity_type + entity_id
- [ ] Display notes in context in frontend

---

## Phase 6: Frontend (Week 6-8)

### 6.1 Base Templates
- [ ] Jinja2 base layout
- [ ] Navigation component
- [ ] User menu with logout

### 6.2 HTMX Integration
- [ ] Project list view
- [ ] Project editor (reuse metaseed UI components)
- [ ] Team management UI

### 6.3 Real-time UI
- [ ] WebSocket connection in JavaScript
- [ ] Chat panel
- [ ] Presence indicators
- [ ] Live update notifications

---

## Phase 7: Polish & Deploy (Week 8+)

### 7.1 Testing
- [ ] Unit tests for services
- [ ] Integration tests for API
- [ ] WebSocket tests

### 7.2 Documentation
- [ ] API documentation (OpenAPI)
- [ ] Deployment guide
- [ ] Keycloak setup guide

### 7.3 Production Readiness
- [ ] Dockerfile
- [ ] Helm chart (optional)
- [ ] Health checks
- [ ] Logging configuration
- [ ] Metrics (Prometheus)

---

## Dependencies

```toml
[project]
dependencies = [
    "fastapi>=0.111.0",
    "uvicorn[standard]>=0.30.0",
    "sqlalchemy[asyncio]>=2.0.0",
    "asyncpg>=0.29.0",
    "alembic>=1.13.0",
    "pydantic-settings>=2.0.0",
    "python-jose[cryptography]>=3.3.0",
    "httpx>=0.27.0",
    "redis>=5.0.0",
    "websockets>=12.0",
    "jinja2>=3.1.0",
]

[project.optional-dependencies]
dev = [
    "pytest>=8.0.0",
    "pytest-asyncio>=0.23.0",
    "httpx>=0.27.0",
    "ruff>=0.4.0",
]
```

---

## Environment Variables

```bash
# Database
DATABASE_URL=postgresql+asyncpg://user:pass@localhost:5432/metaseed_hub

# Keycloak
KEYCLOAK_URL=http://localhost:8080
KEYCLOAK_REALM=metaseed
KEYCLOAK_CLIENT_ID=metaseed-hub
KEYCLOAK_CLIENT_SECRET=your-secret

# Redis
REDIS_URL=redis://localhost:6379/0

# App
SECRET_KEY=your-secret-key
DEBUG=true
ALLOWED_ORIGINS=http://localhost:3000,http://localhost:8000
```

---

## Docker Compose Services

```yaml
services:
  postgres:
    image: postgres:16
    environment:
      POSTGRES_USER: metaseed
      POSTGRES_PASSWORD: metaseed
      POSTGRES_DB: metaseed_hub
    ports:
      - "5432:5432"

  keycloak:
    image: quay.io/keycloak/keycloak:latest
    command: start-dev
    environment:
      KEYCLOAK_ADMIN: admin
      KEYCLOAK_ADMIN_PASSWORD: admin
      KC_DB: postgres
      KC_DB_URL: jdbc:postgresql://postgres:5432/keycloak
      KC_DB_USERNAME: metaseed
      KC_DB_PASSWORD: metaseed
    ports:
      - "8080:8080"
    depends_on:
      - postgres

  redis:
    image: redis:7
    ports:
      - "6379:6379"
```

---

## Getting Started

```bash
# Clone and enter directory
cd metaseed-hub

# Start services
docker compose up -d

# Install dependencies
uv sync

# Run migrations
uv run alembic upgrade head

# Start dev server
uv run uvicorn metaseed_hub.main:app --reload

# Access Keycloak admin: http://localhost:8080
# Create realm "metaseed" and client "metaseed-hub"
```
