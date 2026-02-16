PYTHON ?= python3

.PHONY: setup db-up db-down migrate ingest ingest-courses ingest-courses-all ingest-all run-backend run-frontend test lint fmt usage usage-reset

setup:
	pip install -e ".[dev]"
	cd frontend && npm install

db-up:
	docker compose up -d db

db-down:
	docker compose down

migrate:
	alembic upgrade head

migrate-new:
	alembic revision --autogenerate -m "$(msg)"

ingest:
	$(PYTHON) -m ingestion.run_ingestion

ingest-courses:
	$(PYTHON) -m ingestion.gt_scheduler

ingest-courses-all:
	$(PYTHON) -m ingestion.gt_scheduler --all

ingest-all:
	@echo "=== Ingesting sitemap sources (registrar, catalog, library) ==="
	$(PYTHON) -m ingestion.run_ingestion
	@echo ""
	@echo "=== Ingesting GT Scheduler course data (all terms) ==="
	$(PYTHON) -m ingestion.gt_scheduler --all
	@echo ""
	@echo "=== Ingestion complete! ==="

usage:
	@$(PYTHON) -c "from app.core.usage import get_usage; u=get_usage(); print(f'Usage: \$${u[\"total_cost\"]:.4f} / \$${u[\"limit\"]:.2f} ({(u[\"total_cost\"]/u[\"limit\"]*100):.1f}%)')"

usage-reset:
	@$(PYTHON) -c "from app.core.usage import reset_usage; reset_usage(); print('Usage reset to \$$0.00')"

run-backend:
	uvicorn app.main:app --reload --host 0.0.0.0 --port 8000

run-frontend:
	cd frontend && npm run dev

test:
	$(PYTHON) -m pytest tests/ -v --tb=short

lint:
	ruff check .
	ruff format --check .

fmt:
	ruff check --fix .
	ruff format .
