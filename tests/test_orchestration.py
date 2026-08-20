from __future__ import annotations

import asyncio

import pytest
from sqlalchemy import create_engine, update
from sqlalchemy.orm import Session, sessionmaker
from sqlalchemy.pool import StaticPool

from db.models import IngestionRun, IngestionRunUnit
from ingestion.orchestration import (
    UnitOutcome,
    UnitResult,
    create_run,
    load_run_summary,
    plan_run,
    reset_failed_units,
    run_batch,
)


@pytest.fixture
def sessions():
    engine = create_engine(
        "sqlite://",
        connect_args={"check_same_thread": False},
        poolclass=StaticPool,
    )
    IngestionRun.__table__.create(engine)
    IngestionRunUnit.__table__.create(engine)
    try:
        yield sessionmaker(engine, class_=Session)
    finally:
        engine.dispose()


def _planned_run(sessions, *units: str, concurrency: int = 2, retry_limit: int = 2):
    run_id = create_run(
        sessions,
        "public-oscar",
        {"term": "202608"},
        concurrency=concurrency,
        retry_limit=retry_limit,
    )
    plan_run(sessions, run_id, units)
    return run_id


def test_manifest_is_immutable_ordered_and_isolated(sessions):
    first = _planned_run(sessions, "AE", "CS")
    second = _planned_run(sessions, "MATH")

    with pytest.raises(ValueError, match="already planned"):
        plan_run(sessions, first, ("ECE",))

    assert first != second
    assert load_run_summary(sessions, first).planned_units == ("AE", "CS")
    assert load_run_summary(sessions, second).planned_units == ("MATH",)


@pytest.mark.asyncio
async def test_scheduler_respects_bounded_concurrency(sessions):
    run_id = _planned_run(sessions, "AE", "CS", "ECE", concurrency=2)
    active = 0
    maximum = 0

    async def run_unit(unit: str) -> UnitResult:
        nonlocal active, maximum
        active += 1
        maximum = max(maximum, active)
        await asyncio.sleep(0)
        active -= 1
        return UnitResult(UnitOutcome.SUCCEEDED, {"unit": unit})

    summary = await run_batch(run_id, sessions, run_unit)

    assert maximum == 2
    assert summary.status == "COMPLETED"
    assert (summary.planned, summary.succeeded, summary.failed, summary.remaining) == (3, 3, 0, 0)


@pytest.mark.asyncio
async def test_rate_limit_pauses_new_scheduling_then_recovers(sessions):
    run_id = _planned_run(sessions, "AE", "CS", concurrency=1, retry_limit=2)
    calls: list[str] = []
    attempts = 0

    async def run_unit(unit: str) -> UnitResult:
        nonlocal attempts
        calls.append(unit)
        if unit == "AE":
            attempts += 1
            if attempts == 1:
                return UnitResult(UnitOutcome.RATE_LIMITED, {}, retry_after_seconds=7)
        return UnitResult(UnitOutcome.SUCCEEDED, {})

    async def fake_sleep(delay: float) -> None:
        calls.append(f"sleep:{delay:g}")

    summary = await run_batch(run_id, sessions, run_unit, sleep=fake_sleep)

    assert calls == ["AE", "sleep:7", "AE", "CS"]
    assert summary.status == "COMPLETED"


@pytest.mark.asyncio
async def test_repeated_rate_limit_pauses_a_resumable_run(sessions):
    run_id = _planned_run(sessions, "AE", "CS", concurrency=1, retry_limit=1)

    async def rate_limited(_unit: str) -> UnitResult:
        return UnitResult(UnitOutcome.RATE_LIMITED, {}, retry_after_seconds=1)

    summary = await run_batch(run_id, sessions, rate_limited, sleep=lambda _delay: asyncio.sleep(0))

    assert summary.status == "PAUSED"
    assert summary.stop_reason == "RATE_LIMITED"
    assert (summary.succeeded, summary.failed, summary.remaining) == (0, 0, 2)


@pytest.mark.asyncio
async def test_auth_failure_is_a_global_stop(sessions):
    run_id = _planned_run(sessions, "AE", "CS", concurrency=1)
    called: list[str] = []

    async def run_unit(unit: str) -> UnitResult:
        called.append(unit)
        return UnitResult(UnitOutcome.AUTH_REQUIRED, {}, reason="LOGIN_REDIRECT")

    summary = await run_batch(run_id, sessions, run_unit)

    assert called == ["AE"]
    assert summary.status == "FAILED"
    assert summary.stop_reason == "AUTH_REQUIRED"
    assert summary.remaining == 1


@pytest.mark.asyncio
async def test_auth_from_an_already_running_unit_overrides_rate_limit_pause(sessions):
    run_id = _planned_run(sessions, "AE", "CS", "ECE", concurrency=2, retry_limit=0)

    async def run_unit(unit: str) -> UnitResult:
        if unit == "AE":
            return UnitResult(UnitOutcome.RATE_LIMITED, {})
        await asyncio.sleep(0.01)
        return UnitResult(UnitOutcome.AUTH_REQUIRED, {}, reason="LOGIN_REDIRECT")

    summary = await run_batch(run_id, sessions, run_unit)

    assert summary.status == "FAILED"
    assert summary.stop_reason == "AUTH_REQUIRED"


@pytest.mark.asyncio
async def test_deterministic_failure_does_not_stop_other_units(sessions):
    run_id = _planned_run(sessions, "AE", "CS", concurrency=1)

    async def run_unit(unit: str) -> UnitResult:
        if unit == "AE":
            return UnitResult(UnitOutcome.FAILED, {}, reason="PARSE_FAILED")
        return UnitResult(UnitOutcome.SUCCEEDED, {})

    summary = await run_batch(run_id, sessions, run_unit)

    assert summary.status == "PARTIAL"
    assert (summary.succeeded, summary.failed, summary.remaining) == (1, 1, 0)


@pytest.mark.asyncio
async def test_resume_recovers_running_and_selected_failed_units(sessions):
    run_id = _planned_run(sessions, "AE", "CS", concurrency=1)
    with sessions() as session, session.begin():
        session.execute(
            update(IngestionRunUnit)
            .where(IngestionRunUnit.run_id == run_id, IngestionRunUnit.unit_key == "AE")
            .values(status="RUNNING")
        )
        session.execute(
            update(IngestionRunUnit)
            .where(IngestionRunUnit.run_id == run_id, IngestionRunUnit.unit_key == "CS")
            .values(status="FAILED")
        )

    reset_failed_units(sessions, run_id, ("CS",))
    called: list[str] = []

    async def run_unit(unit: str) -> UnitResult:
        called.append(unit)
        return UnitResult(UnitOutcome.SUCCEEDED, {})

    summary = await run_batch(run_id, sessions, run_unit)

    assert called == ["AE", "CS"]
    assert summary.status == "COMPLETED"
