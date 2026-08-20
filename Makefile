PYTHON ?= python3

.PHONY: setup db-up db-down migrate probe-doc sync-doc sync-doc-many resume-doc-run sync-gt-all resume-gt-all sync-oscar sync-oscar-all run-backend run-frontend test test-db lint fmt usage eval-v2

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

probe-doc:
	$(PYTHON) -m ingestion.documents.cli probe --source "$(source)"

sync-doc:
	$(PYTHON) -m ingestion.documents.cli sync --source "$(source)"

sync-doc-many:
	$(PYTHON) -m ingestion.documents.cli sync-many --source "$(source)" $(if $(verification_limit),--verification-limit "$(verification_limit)",)

resume-doc-run:
	$(PYTHON) -m ingestion.documents.cli sync-many --source "$(source)" --resume --run-id "$(run_id)"

sync-gt-all:
	$(PYTHON) -m ingestion.documents.cli sync-all --profile run3 $(if $(verification_limit),--verification-limit "$(verification_limit)",)

resume-gt-all:
	$(PYTHON) -m ingestion.documents.cli sync-all --profile run3 --resume --run-id "$(run_id)"

sync-oscar:
	$(PYTHON) -m ingestion.schedule.cli --term "$(term)" --subject "$(subject)" --probe-course "$(course)"

sync-oscar-all:
	$(PYTHON) -m ingestion.schedule.sync_term --term "$(term)" --probe-subject "$(or $(probe_subject),CS)" --probe-course "$(or $(course),7650)"

ingest:
	$(PYTHON) -m ingestion.run_ingestion

ingest-courses:
	$(PYTHON) -m ingestion.gt_scheduler

ingest-courses-all:
	$(PYTHON) -m ingestion.gt_scheduler --all

ingest-calendar:
	$(PYTHON) -m ingestion.gt_calendar

ingest-all:
	@echo "=== Ingesting sitemap sources (registrar, catalog, library) ==="
	$(PYTHON) -m ingestion.run_ingestion
	@echo ""
	@echo "=== Ingesting GT Scheduler course data (all terms) ==="
	$(PYTHON) -m ingestion.gt_scheduler --all
	@echo ""
	@echo ""
	@echo "=== Ingesting academic calendar events ==="
	$(PYTHON) -m ingestion.gt_calendar
	@echo ""
	@echo "=== Ingestion complete! ==="

usage:
	@$(PYTHON) -c "from app.core.usage import get_usage; u=get_usage(); print(f'Usage: \$${u[\"total_cost\"]:.4f} / \$${u[\"limit\"]:.2f} ({(u[\"total_cost\"]/u[\"limit\"]*100):.1f}%)')"

eval-debug:
	$(PYTHON) eval/db_coverage_audit.py
	$(PYTHON) eval/debug_deadlines_matrix.py

run-backend:
	uvicorn app.main:app --reload --host 0.0.0.0 --port 8000

run-frontend:
	cd frontend && npm run dev

test:
	PYTHONPATH=$$PWD $(PYTHON) -m pytest -q

test-db:
	RUN_DB_TESTS=1 PYTHONPATH=$$PWD $(PYTHON) -m pytest -q tests/integration

eval-v2:
	PYTHONPATH=$$PWD $(PYTHON) eval/agentic_rag_eval.py

lint:
	ruff check .
	ruff format --check .

fmt:
	ruff check --fix .
	ruff format .
