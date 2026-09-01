from __future__ import annotations

import importlib

from sqlalchemy import CheckConstraint, ForeignKeyConstraint, UniqueConstraint, inspect

from app.db import models

SCHEDULE_TABLES = {
    "data_versions",
    "academic_terms",
    "courses",
    "sections",
    "meetings",
    "source_snapshots",
    "ingestion_errors",
    "ingestion_runs",
    "ingestion_run_units",
}


def test_schedule_tables_and_versioned_unique_constraints():
    assert set(models.Base.metadata.tables) >= SCHEDULE_TABLES

    course_constraints = {
        tuple(constraint.columns.keys())
        for constraint in models.Course.__table__.constraints
        if isinstance(constraint, UniqueConstraint)
    }
    section_constraints = {
        tuple(constraint.columns.keys())
        for constraint in models.Section.__table__.constraints
        if isinstance(constraint, UniqueConstraint)
    }

    assert ("data_version_id", "subject", "course_number") in course_constraints
    assert ("data_version_id", "term_code", "crn") in section_constraints


def test_schedule_foreign_keys_are_version_scoped():
    expected = {
        "academic_terms": {"data_versions.id"},
        "courses": {"data_versions.id"},
        "sections": {
            "data_versions.id",
            "academic_terms.data_version_id",
            "academic_terms.id",
            "courses.data_version_id",
            "courses.id",
        },
        "meetings": {"data_versions.id", "sections.data_version_id", "sections.id"},
        "source_snapshots": {"data_versions.id"},
        "ingestion_errors": {"data_versions.id"},
    }

    for table_name, targets in expected.items():
        table = models.Base.metadata.tables[table_name]
        assert {foreign_key.target_fullname for foreign_key in table.foreign_keys} == targets


def test_child_references_require_the_same_data_version():
    expected = {
        "sections": {
            (
                ("data_version_id", "academic_term_id"),
                ("academic_terms.data_version_id", "academic_terms.id"),
            ),
            (
                ("data_version_id", "course_id"),
                ("courses.data_version_id", "courses.id"),
            ),
        },
        "meetings": {
            (
                ("data_version_id", "section_id"),
                ("sections.data_version_id", "sections.id"),
            ),
        },
    }

    for table_name, required_constraints in expected.items():
        table = models.Base.metadata.tables[table_name]
        actual = {
            (
                tuple(constraint.column_keys),
                tuple(element.target_fullname for element in constraint.elements),
            )
            for constraint in table.constraints
            if isinstance(constraint, ForeignKeyConstraint)
        }
        assert actual >= required_constraints


def test_parent_rows_expose_versioned_composite_keys():
    for model in (models.AcademicTerm, models.Course, models.Section):
        constraints = {
            tuple(constraint.columns.keys())
            for constraint in model.__table__.constraints
            if isinstance(constraint, UniqueConstraint)
        }
        assert ("data_version_id", "id") in constraints


def test_model_and_migration_enforce_the_same_version_states(monkeypatch):
    model_checks = {
        constraint.name: str(constraint.sqltext)
        for constraint in models.DataVersion.__table__.constraints
        if isinstance(constraint, CheckConstraint)
    }
    assert "ck_data_versions_status" in model_checks

    migration = importlib.import_module("migrations.versions.003_structured_schedule")
    migration_checks: dict[str | None, str] = {}

    def capture_table(name, *items):
        if name == "data_versions":
            migration_checks.update(
                {
                    item.name: str(item.sqltext)
                    for item in items
                    if isinstance(item, CheckConstraint)
                }
            )

    monkeypatch.setattr(migration.op, "create_table", capture_table)
    monkeypatch.setattr(migration.op, "create_index", lambda *args, **kwargs: None)
    migration.upgrade()

    assert migration_checks["ck_data_versions_status"] == model_checks["ck_data_versions_status"]


def test_tba_meeting_fields_are_nullable():
    meeting = models.Meeting.__table__
    for column_name in ("days", "start_time", "end_time", "building", "room"):
        assert meeting.c[column_name].nullable is True
    assert meeting.c.is_tba.nullable is False


def test_schedule_relationships_target_the_expected_models():
    expected = {
        models.DataVersion: {
            "academic_terms": models.AcademicTerm,
            "courses": models.Course,
            "sections": models.Section,
            "meetings": models.Meeting,
            "source_snapshots": models.SourceSnapshot,
            "ingestion_errors": models.IngestionError,
        },
        models.Section: {
            "data_version": models.DataVersion,
            "academic_term": models.AcademicTerm,
            "course": models.Course,
            "meetings": models.Meeting,
        },
        models.Meeting: {
            "data_version": models.DataVersion,
            "section": models.Section,
        },
    }

    for model, relationships in expected.items():
        mapper = inspect(model)
        for name, target in relationships.items():
            assert mapper.relationships[name].mapper.class_ is target


def test_only_version_relationships_delete_owned_rows():
    version_relationships = inspect(models.DataVersion).relationships
    assert all("delete-orphan" in relationship.cascade for relationship in version_relationships)

    section_relationships = inspect(models.Section).relationships
    assert "delete-orphan" not in section_relationships["meetings"].cascade


def test_ingestion_run_manifest_constraints_match_migration(monkeypatch):
    run_checks = {
        constraint.name: str(constraint.sqltext)
        for constraint in models.IngestionRun.__table__.constraints
        if isinstance(constraint, CheckConstraint)
    }
    unit_checks = {
        constraint.name: str(constraint.sqltext)
        for constraint in models.IngestionRunUnit.__table__.constraints
        if isinstance(constraint, CheckConstraint)
    }
    unit_uniques = {
        tuple(constraint.columns.keys())
        for constraint in models.IngestionRunUnit.__table__.constraints
        if isinstance(constraint, UniqueConstraint)
    }

    assert set(run_checks) == {
        "ck_ingestion_runs_status",
        "ck_ingestion_runs_concurrency",
        "ck_ingestion_runs_retry_limit",
    }
    assert set(unit_checks) == {
        "ck_ingestion_run_units_status",
        "ck_ingestion_run_units_attempts",
        "ck_ingestion_run_units_position",
    }
    assert unit_uniques >= {("run_id", "unit_key"), ("run_id", "position")}
    run_fk = next(iter(models.IngestionRunUnit.__table__.c.run_id.foreign_keys))
    assert run_fk.target_fullname == "ingestion_runs.id"
    assert run_fk.ondelete == "CASCADE"

    migration = importlib.import_module("migrations.versions.004_ingestion_runs")
    migration_checks: dict[str, str] = {}

    def capture_table(name, *items):
        if name in {"ingestion_runs", "ingestion_run_units"}:
            migration_checks.update(
                {
                    item.name: str(item.sqltext)
                    for item in items
                    if isinstance(item, CheckConstraint)
                }
            )

    monkeypatch.setattr(migration.op, "create_table", capture_table)
    monkeypatch.setattr(migration.op, "create_index", lambda *args, **kwargs: None)
    migration.upgrade()

    assert migration_checks == {**run_checks, **unit_checks}
