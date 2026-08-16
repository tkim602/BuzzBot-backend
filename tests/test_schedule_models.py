from __future__ import annotations

from sqlalchemy import UniqueConstraint, inspect

from db import models

SCHEDULE_TABLES = {
    "data_versions",
    "academic_terms",
    "courses",
    "sections",
    "meetings",
    "source_snapshots",
    "ingestion_errors",
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
            "academic_terms.id",
            "courses.id",
        },
        "meetings": {"data_versions.id", "sections.id"},
        "source_snapshots": {"data_versions.id"},
        "ingestion_errors": {"data_versions.id"},
    }

    for table_name, targets in expected.items():
        table = models.Base.metadata.tables[table_name]
        assert {foreign_key.target_fullname for foreign_key in table.foreign_keys} == targets


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
