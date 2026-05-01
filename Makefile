.PHONY: dev test lint docs clean up down db-migrate db-wait setup-keycloak

up:
	docker compose up -d
	@$(MAKE) db-wait
	@$(MAKE) setup-keycloak

down:
	docker compose down

db-wait:
	@echo "Waiting for PostgreSQL..."
	@until docker exec metaseed-postgres-dev pg_isready -U metaseed -d metaseed_hub > /dev/null 2>&1; do sleep 1; done
	@echo "PostgreSQL is ready"

setup-keycloak:
	@echo "Waiting for Keycloak..."
	@until curl -s http://localhost:7080/health/ready > /dev/null 2>&1; do sleep 2; done
	@echo "Keycloak is ready"
	uv run python scripts/setup_keycloak.py

db-migrate: db-wait
	uv run alembic upgrade head

dev: up db-migrate
	uv run uvicorn metaseed_hub.main:app --reload --host 0.0.0.0 --port 7001

test:
	uv run pytest

lint:
	uv run ruff check src tests
	uv run mypy src

docs:
	uv run mkdocs serve

clean:
	find . -type d -name __pycache__ -exec rm -rf {} + 2>/dev/null || true
	find . -type f -name "*.pyc" -delete 2>/dev/null || true
