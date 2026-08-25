from pathlib import Path

ROOT = Path(__file__).resolve().parents[1]


def test_docker_runtime_is_nonroot_and_excludes_dev_dependencies():
    dockerfile = (ROOT / "Dockerfile").read_text()
    runtime = dockerfile.split(" AS runtime", 1)[1]

    assert "USER buzzbot" in runtime
    assert 'pip install --no-cache-dir -e ".[dev]"' not in runtime
    assert "HF_HOME=/home/buzzbot/.cache/huggingface" in runtime
    assert "HEALTHCHECK" in runtime


def test_backend_ci_runs_migrations_and_postgres_integration_tests():
    workflow = (ROOT / ".github/workflows/ci.yml").read_text()

    assert "pgvector/pgvector:" in workflow
    assert "alembic upgrade head" in workflow
    assert 'RUN_DB_TESTS: "1"' in workflow
    assert "tests/integration" in workflow
    assert "quality-chat" not in workflow
