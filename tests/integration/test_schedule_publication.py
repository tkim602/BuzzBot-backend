from __future__ import annotations

import os
import uuid
from datetime import UTC, date, datetime, time

import pytest
from sqlalchemy import create_engine, event, insert, select
from sqlalchemy.orm import Session

from db.models import DataVersion
from ingestion.schedule.repository import (
    SafeSnapshot,
    latest_published_version,
    publish_collection,
)
from ingestion.schedule.types import NormalizedCourse, NormalizedMeeting, NormalizedSection
from ingestion.schedule.validate import CollectionPlan, ValidationReport

pytestmark = pytest.mark.skipif(
    os.getenv("RUN_DB_TESTS") != "1", reason="set RUN_DB_TESTS=1 for PostgreSQL tests"
)


def test_exception_before_commit_preserves_previous_published_version():
    engine = create_engine(
        os.getenv(
            "DATABASE_URL_SYNC",
            "postgresql://buzzbot:buzzbot_dev@localhost:5432/buzzbot",
        )
    )
    provider = f"test-{uuid.uuid4()}"
    requested_unit = "202608:CS"
    old_id = uuid.uuid4()
    with Session(engine) as seed:
        seed.execute(
            insert(DataVersion),
            {
                "id": old_id,
                "provider": provider,
                "requested_unit": requested_unit,
                "status": "PUBLISHED",
                "row_counts_json": {},
                "published_at": datetime.now(UTC),
            },
        )
        seed.commit()

    course = NormalizedCourse("CS", "7650", "Natural Language", 3.0)
    meeting = NormalizedMeeting(
        "Class",
        "TR",
        time(15, 30),
        time(16, 45),
        "Klaus",
        "1447",
        date(2026, 8, 24),
        date(2026, 12, 17),
        False,
    )
    section = NormalizedSection(
        "202608",
        "Fall 2026",
        "12345",
        ("CS", "7650"),
        "A",
        "Georgia Tech-Atlanta Campus",
        "Lecture",
        ("Ada Lovelace",),
        (meeting,),
    )
    snapshot = SafeSnapshot(
        "https://oscar.gatech.edu/schedule",
        datetime.now(UTC),
        200,
        "text/html",
        "a" * 64,
        "oscar-v1",
        "artifacts/oscar/sample.html",
    )

    try:
        with Session(engine) as publishing:
            event.listen(
                publishing,
                "before_commit",
                lambda _session: (_ for _ in ()).throw(RuntimeError("forced pre-commit failure")),
                once=True,
            )
            with pytest.raises(RuntimeError, match="forced pre-commit failure"):
                publish_collection(
                    publishing,
                    provider,
                    requested_unit,
                    snapshot,
                    CollectionPlan("202608", ("CS",), ("CS",), (), 1, 1),
                    [course],
                    [section],
                    [],
                    ValidationReport(True, 1.0, ()),
                )

        with Session(engine) as verify:
            latest = latest_published_version(verify, provider, requested_unit)
            assert latest is not None
            assert latest.id == old_id
            versions = verify.scalars(
                select(DataVersion).where(
                    DataVersion.provider == provider,
                    DataVersion.requested_unit == requested_unit,
                )
            ).all()
            assert [(version.id, version.status) for version in versions] == [(old_id, "PUBLISHED")]
    finally:
        with Session(engine) as cleanup:
            for version in cleanup.scalars(
                select(DataVersion).where(DataVersion.provider == provider)
            ):
                cleanup.delete(version)
            cleanup.commit()
        engine.dispose()
