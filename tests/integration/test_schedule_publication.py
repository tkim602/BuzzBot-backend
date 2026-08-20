from __future__ import annotations

import os
import threading
import uuid
from concurrent.futures import ThreadPoolExecutor
from contextlib import suppress
from datetime import UTC, date, datetime, time

import pytest
from sqlalchemy import create_engine, delete, event, insert, select
from sqlalchemy.orm import Session

from app.core.config import settings
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


def _valid_collection():
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
    plan = CollectionPlan("202608", ("CS",), ("CS",), (), 1, 1)
    return snapshot, plan, [course], [section]


def _delete_provider(engine, provider: str) -> None:
    with Session(engine) as cleanup:
        cleanup.execute(delete(DataVersion).where(DataVersion.provider == provider))
        cleanup.commit()


def test_exception_before_commit_preserves_previous_published_version():
    engine = create_engine(settings.database_url_sync)
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

    snapshot, plan, courses, sections = _valid_collection()

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
                    plan,
                    courses,
                    sections,
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
        _delete_provider(engine, provider)
        engine.dispose()


def test_concurrent_publications_leave_exactly_one_published_version():
    engine = create_engine(settings.database_url_sync)
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

    start = threading.Barrier(2)
    after_current_read = threading.Barrier(2)

    class CoordinatedSession(Session):
        def scalars(self, *args, **kwargs):
            result = super().scalars(*args, **kwargs)
            with suppress(threading.BrokenBarrierError):
                after_current_read.wait(timeout=0.75)
            return result

    snapshot, plan, courses, sections = _valid_collection()

    def publish() -> uuid.UUID:
        with CoordinatedSession(engine) as session:
            start.wait(timeout=2)
            return publish_collection(
                session,
                provider,
                requested_unit,
                snapshot,
                plan,
                courses,
                sections,
                [],
                ValidationReport(True, 1.0, ()),
            )

    try:
        with ThreadPoolExecutor(max_workers=2) as executor:
            new_ids = {
                future.result() for future in (executor.submit(publish), executor.submit(publish))
            }

        with Session(engine) as verify:
            versions = verify.scalars(
                select(DataVersion).where(
                    DataVersion.provider == provider,
                    DataVersion.requested_unit == requested_unit,
                )
            ).all()
            published = [version for version in versions if version.status == "PUBLISHED"]
            assert len(published) == 1
            assert published[0].id in new_ids
            assert sum(version.status == "SUPERSEDED" for version in versions) == 2
    finally:
        _delete_provider(engine, provider)
        engine.dispose()


def test_different_collection_units_are_not_globally_serialized():
    engine = create_engine(settings.database_url_sync)
    provider = f"test-{uuid.uuid4()}"
    start = threading.Barrier(2)
    before_commit = threading.Barrier(2)
    snapshot, plan, courses, sections = _valid_collection()

    def publish(requested_unit: str) -> bool:
        met_other_publisher = False

        def coordinate_commit(_session) -> None:
            nonlocal met_other_publisher
            try:
                before_commit.wait(timeout=1.5)
                met_other_publisher = True
            except threading.BrokenBarrierError:
                pass

        with Session(engine) as session:
            event.listen(session, "before_commit", coordinate_commit, once=True)
            start.wait(timeout=2)
            publish_collection(
                session,
                provider,
                requested_unit,
                snapshot,
                plan,
                courses,
                sections,
                [],
                ValidationReport(True, 1.0, ()),
            )
        return met_other_publisher

    try:
        with ThreadPoolExecutor(max_workers=2) as executor:
            results = [
                future.result()
                for future in (
                    executor.submit(publish, "202608:CS"),
                    executor.submit(publish, "202608:CSE"),
                )
            ]
        assert results == [True, True]
    finally:
        _delete_provider(engine, provider)
        engine.dispose()
