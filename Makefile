PYTHON ?= python3

.PHONY: setup db-up db-down migrate probe-doc sync-doc sync-doc-many resume-doc-run sync-gt-all resume-gt-all sync-oscar sync-oscar-all run-backend test test-db lint fmt usage eval-v2 quality-retrieval-dev quality-retrieval-change quality-retrieval-full quality-policy-oracle quality-policy-hierarchical quality-chat-dev quality-chat-change quality-chat-schedule quality-diagnose-dev

setup:
	pip install -e ".[dev]"

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

usage:
	@$(PYTHON) -c "from app.core.usage import get_usage; u=get_usage(); print(f'Usage: \$${u[\"total_cost\"]:.4f} / \$${u[\"limit\"]:.2f} ({(u[\"total_cost\"]/u[\"limit\"]*100):.1f}%)')"

run-backend:
	uvicorn app.main:app --reload --host 0.0.0.0 --port 8000

test:
	PYTHONPATH=$$PWD $(PYTHON) -m pytest -q

test-db:
	RUN_DB_TESTS=1 PYTHONPATH=$$PWD $(PYTHON) -m pytest -q tests/integration

eval-v2:
	PYTHONPATH=$$PWD $(PYTHON) eval/agentic_rag_eval.py

quality-retrieval-dev:
	PYTHONPATH=$$PWD $(PYTHON) -m eval.quality.runner \
		--manifest eval/quality/manifests/dev_100.json \
		--report-dir eval/quality/reports_retrieval_100

quality-retrieval-change:
	PYTHONPATH=$$PWD $(PYTHON) -m eval.quality.runner \
		--manifest eval/quality/manifests/change_200.json \
		--report-dir eval/quality/reports_retrieval_200

quality-retrieval-full:
	PYTHONPATH=$$PWD $(PYTHON) -m eval.quality.runner --dataset eval/quality/data_verified

quality-policy-oracle:
	PYTHONPATH=$$PWD $(PYTHON) -m eval.quality.policy_oracle_retrieval

quality-policy-hierarchical:
	PYTHONPATH=$$PWD $(PYTHON) -m eval.quality.policy_hierarchical_retrieval

quality-chat-dev:
	PYTHONPATH=$$PWD $(PYTHON) -m eval.quality.chat_runner \
		--manifest eval/quality/manifests/dev_100.json \
		--report-dir eval/quality/reports_chat_100

quality-chat-change:
	PYTHONPATH=$$PWD $(PYTHON) -m eval.quality.chat_runner \
		--manifest eval/quality/manifests/change_200.json \
		--report-dir eval/quality/reports_chat_200

quality-chat-schedule:
	PYTHONPATH=$$PWD $(PYTHON) -m eval.quality.chat_runner \
		--manifest eval/quality/manifests/schedule_5.json \
		--report-dir eval/quality/reports_chat_schedule_5

quality-diagnose-dev:
	PYTHONPATH=$$PWD $(PYTHON) -m eval.quality.diagnose_failures \
		--manifest eval/quality/manifests/dev_100.json \
		--retrieval-report "$(or $(retrieval_report),eval/quality/reports_retrieval_100/latest_cases.jsonl)" \
		--chat-report "$(or $(chat_report),eval/quality/reports_chat_100/latest_cases.jsonl)" \
		--report-dir eval/quality/reports_diagnosis_dev_100

lint:
	ruff check .
	ruff format --check .

fmt:
	ruff check --fix .
	ruff format .
