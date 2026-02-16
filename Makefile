.PHONY: setup db-up db-down migrate ingest run-backend run-frontend test lint fmt

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
	python -m ingestion.run_ingestion

run-backend:
	uvicorn app.main:app --reload --host 0.0.0.0 --port 8000

run-frontend:
	cd frontend && npm run dev

test:
	python -m pytest tests/ -v --tb=short

lint:
	ruff check .
	ruff format --check .

fmt:
	ruff check --fix .
	ruff format .
