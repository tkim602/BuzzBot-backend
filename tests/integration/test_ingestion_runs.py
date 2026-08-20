from __future__ import annotations

import os
import uuid

import pytest
from sqlalchemy import create_engine, delete, func, insert, select
from sqlalchemy.exc import IntegrityError
from sqlalchemy.orm import Session

from app.core.config import settings
from db.models import IngestionRun, IngestionRunUnit

pytestmark = pytest.mark.skipif(
    os.getenv("RUN_DB_TESTS") != "1", reason="set RUN_DB_TESTS=1 for PostgreSQL tests"
)


def test_run_units_are_ordered_unique_and_deleted_with_the_run():
    engine = create_engine(settings.database_url_sync)
    run_id = uuid.uuid4()
    try:
        with Session(engine) as session, session.begin():
            session.execute(
                insert(IngestionRun),
                {
                    "id": run_id,
                    "provider": "test-oscar",
                    "scope_json": {"term": "202608"},
                    "status": "PLANNED",
                    "concurrency": 2,
                    "retry_limit": 2,
                },
            )
            session.execute(
                insert(IngestionRunUnit),
                [
                    {
                        "id": uuid.uuid4(),
                        "run_id": run_id,
                        "unit_key": "AE",
                        "position": 0,
                        "status": "PENDING",
                        "attempts": 0,
                        "result_json": {},
                    },
                    {
                        "id": uuid.uuid4(),
                        "run_id": run_id,
                        "unit_key": "CS",
                        "position": 1,
                        "status": "PENDING",
                        "attempts": 0,
                        "result_json": {},
                    },
                ],
            )

        with Session(engine) as session:
            units = session.scalars(
                select(IngestionRunUnit)
                .where(IngestionRunUnit.run_id == run_id)
                .order_by(IngestionRunUnit.position)
            ).all()
            assert [unit.unit_key for unit in units] == ["AE", "CS"]

        with pytest.raises(IntegrityError), Session(engine) as session, session.begin():
            session.execute(
                insert(IngestionRunUnit),
                {
                    "id": uuid.uuid4(),
                    "run_id": run_id,
                    "unit_key": "CS",
                    "position": 2,
                    "status": "PENDING",
                    "attempts": 0,
                    "result_json": {},
                },
            )

        with Session(engine) as session, session.begin():
            session.execute(delete(IngestionRun).where(IngestionRun.id == run_id))
        with Session(engine) as session:
            count = session.scalar(
                select(func.count())
                .select_from(IngestionRunUnit)
                .where(IngestionRunUnit.run_id == run_id)
            )
            assert count == 0
    finally:
        with Session(engine) as cleanup, cleanup.begin():
            cleanup.execute(delete(IngestionRun).where(IngestionRun.id == run_id))
        engine.dispose()
