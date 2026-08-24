import ast
import importlib
from pathlib import Path

ROOT = Path(__file__).parents[1]


def test_database_runtime_and_migrations_have_backend_ownership():
    assert importlib.import_module("app.db.models")
    assert importlib.import_module("app.db.session")
    assert (ROOT / "migrations" / "env.py").is_file()
    assert not list((ROOT / "db").rglob("*.py"))


def test_ingestion_jobs_import_without_starting_the_api():
    assert importlib.import_module("ingestion.documents.cli")
    assert importlib.import_module("ingestion.schedule.cli")
    assert importlib.import_module("ingestion.schedule.sync_term")


def test_production_packages_do_not_import_evaluation_experiments():
    offenders: list[str] = []
    for package in (ROOT / "app", ROOT / "ingestion"):
        for path in package.rglob("*.py"):
            tree = ast.parse(path.read_text(encoding="utf-8"))
            for node in ast.walk(tree):
                if (
                    isinstance(node, ast.ImportFrom)
                    and (node.module or "").startswith("eval")
                    or isinstance(node, ast.Import)
                    and any(alias.name.startswith("eval") for alias in node.names)
                ):
                    offenders.append(str(path.relative_to(ROOT)))

    assert offenders == []
