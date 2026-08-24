from __future__ import annotations

import asyncio
import random
import uuid
from collections import deque
from collections.abc import Awaitable, Callable, Sequence
from contextlib import AbstractContextManager
from dataclasses import dataclass
from datetime import UTC, datetime
from enum import StrEnum

from sqlalchemy import func, insert, select, update
from sqlalchemy.orm import Session

from app.db.models import IngestionRun, IngestionRunUnit

SessionFactory = Callable[[], AbstractContextManager[Session]]


class UnitOutcome(StrEnum):
    SUCCEEDED = "SUCCEEDED"
    RETRYABLE = "RETRYABLE"
    RATE_LIMITED = "RATE_LIMITED"
    AUTH_REQUIRED = "AUTH_REQUIRED"
    FAILED = "FAILED"


@dataclass(frozen=True)
class UnitResult:
    outcome: UnitOutcome
    summary: dict[str, object]
    retry_after_seconds: int | None = None
    published_version_id: uuid.UUID | None = None
    reason: str | None = None


@dataclass(frozen=True)
class RunSummary:
    run_id: uuid.UUID
    provider: str
    scope: dict[str, object]
    status: str
    planned: int
    succeeded: int
    failed: int
    remaining: int
    complete: bool
    stop_reason: str | None
    planned_units: tuple[str, ...]


@dataclass(frozen=True)
class _RunPolicy:
    concurrency: int
    retry_limit: int


def create_run(
    session_factory: SessionFactory,
    provider: str,
    scope: dict[str, object],
    *,
    concurrency: int = 2,
    retry_limit: int = 2,
) -> uuid.UUID:
    if not provider or not scope:
        raise ValueError("provider and scope are required")
    if concurrency < 1 or retry_limit < 0:
        raise ValueError("concurrency must be positive and retry_limit nonnegative")
    run_id = uuid.uuid4()
    with session_factory() as session, session.begin():
        session.execute(
            insert(IngestionRun),
            {
                "id": run_id,
                "provider": provider,
                "scope_json": scope,
                "status": "PLANNED",
                "concurrency": concurrency,
                "retry_limit": retry_limit,
            },
        )
    return run_id


def plan_run(
    session_factory: SessionFactory,
    run_id: uuid.UUID,
    units: Sequence[str],
) -> None:
    planned = tuple(unit.strip() for unit in units)
    if not planned or any(not unit for unit in planned) or len(set(planned)) != len(planned):
        raise ValueError("planned units must be nonempty and unique")
    with session_factory() as session, session.begin():
        run = session.get(IngestionRun, run_id)
        if run is None:
            raise ValueError("run not found")
        existing = session.scalar(
            select(func.count())
            .select_from(IngestionRunUnit)
            .where(IngestionRunUnit.run_id == run_id)
        )
        if run.status != "PLANNED" or existing:
            raise ValueError("run is already planned")
        session.execute(
            insert(IngestionRunUnit),
            [
                {
                    "id": uuid.uuid4(),
                    "run_id": run_id,
                    "unit_key": unit,
                    "position": position,
                    "status": "PENDING",
                    "attempts": 0,
                    "result_json": {},
                }
                for position, unit in enumerate(planned)
            ],
        )


def fail_run(session_factory: SessionFactory, run_id: uuid.UUID, reason: str) -> RunSummary:
    _set_run_status(session_factory, run_id, "FAILED", reason)
    return load_run_summary(session_factory, run_id)


def pause_run(session_factory: SessionFactory, run_id: uuid.UUID, reason: str) -> RunSummary:
    _set_run_status(session_factory, run_id, "PAUSED", reason)
    return load_run_summary(session_factory, run_id)


def reset_failed_units(
    session_factory: SessionFactory,
    run_id: uuid.UUID,
    units: Sequence[str],
) -> None:
    selected = tuple(unit.strip() for unit in units)
    if not selected:
        return
    with session_factory() as session, session.begin():
        failed = set(
            session.scalars(
                select(IngestionRunUnit.unit_key).where(
                    IngestionRunUnit.run_id == run_id,
                    IngestionRunUnit.status == "FAILED",
                )
            ).all()
        )
        if not set(selected) <= failed:
            raise ValueError("retry units must exist and be failed")
        session.execute(
            update(IngestionRunUnit)
            .where(
                IngestionRunUnit.run_id == run_id,
                IngestionRunUnit.unit_key.in_(selected),
            )
            .values(status="PENDING", result_json={})
        )


def load_run_summary(session_factory: SessionFactory, run_id: uuid.UUID) -> RunSummary:
    with session_factory() as session:
        run = session.get(IngestionRun, run_id)
        if run is None:
            raise ValueError("run not found")
        units = session.scalars(
            select(IngestionRunUnit)
            .where(IngestionRunUnit.run_id == run_id)
            .order_by(IngestionRunUnit.position)
        ).all()
        succeeded = sum(unit.status == "SUCCEEDED" for unit in units)
        failed = sum(unit.status == "FAILED" for unit in units)
        remaining = sum(unit.status in {"PENDING", "RUNNING"} for unit in units)
        return RunSummary(
            run.id,
            run.provider,
            run.scope_json,
            run.status,
            len(units),
            succeeded,
            failed,
            remaining,
            run.status == "COMPLETED" and bool(units) and succeeded == len(units),
            run.stop_reason,
            tuple(unit.unit_key for unit in units),
        )


async def run_batch(
    run_id: uuid.UUID,
    session_factory: SessionFactory,
    run_unit: Callable[[str], Awaitable[UnitResult]],
    *,
    sleep: Callable[[float], Awaitable[None]] = asyncio.sleep,
    jitter: Callable[[float, float], float] = random.uniform,
) -> RunSummary:
    run, pending = _prepare_run(session_factory, run_id)
    if not pending:
        return _finish_from_counts(session_factory, run_id)

    queue = deque(pending)
    active: dict[asyncio.Future[UnitResult], str] = {}
    rate_limit_retries: dict[str, int] = {}
    transient_retries: dict[str, int] = {}
    retry_delays: dict[str, float] = {}
    hard_stop: str | None = None
    pause = False

    while queue or active:
        while queue and len(active) < run.concurrency and not hard_stop and not pause:
            unit = queue.popleft()
            if unit in retry_delays:
                await sleep(retry_delays.pop(unit))
            _start_unit(session_factory, run_id, unit)
            active[asyncio.ensure_future(run_unit(unit))] = unit
        if not active:
            break

        done, _ = await asyncio.wait(active, return_when=asyncio.FIRST_COMPLETED)
        retry_units: list[tuple[str, UnitResult, float]] = []
        for task in done:
            unit = active.pop(task)
            result = _task_result(task)
            if result.outcome is UnitOutcome.SUCCEEDED:
                _record_unit(session_factory, run_id, unit, "SUCCEEDED", result)
            elif result.outcome is UnitOutcome.AUTH_REQUIRED:
                _record_unit(session_factory, run_id, unit, "FAILED", result)
                hard_stop = "AUTH_REQUIRED"
            elif result.outcome is UnitOutcome.RATE_LIMITED:
                retries = rate_limit_retries.get(unit, 0)
                _record_unit(session_factory, run_id, unit, "PENDING", result)
                if retries >= run.retry_limit:
                    pause = True
                else:
                    rate_limit_retries[unit] = retries + 1
                    delay = _retry_delay(result.retry_after_seconds, retries, jitter)
                    retry_units.append((unit, result, delay))
            elif result.outcome is UnitOutcome.RETRYABLE:
                retries = transient_retries.get(unit, 0)
                if retries >= run.retry_limit:
                    _record_unit(session_factory, run_id, unit, "FAILED", result)
                else:
                    transient_retries[unit] = retries + 1
                    _record_unit(session_factory, run_id, unit, "PENDING", result)
                    retry_delays[unit] = _retry_delay(None, retries, jitter)
                    queue.append(unit)
            else:
                _record_unit(session_factory, run_id, unit, "FAILED", result)

        if hard_stop or pause:
            hard_stop = hard_stop or await _drain_active(active, session_factory, run_id)
            break
        if retry_units:
            await sleep(max(delay for _, _, delay in retry_units))
            for unit, _, _ in reversed(retry_units):
                queue.appendleft(unit)

    if hard_stop:
        _set_run_status(session_factory, run_id, "FAILED", hard_stop)
    elif pause:
        _set_run_status(session_factory, run_id, "PAUSED", "RATE_LIMITED")
    else:
        return _finish_from_counts(session_factory, run_id)
    return load_run_summary(session_factory, run_id)


def _prepare_run(
    session_factory: SessionFactory,
    run_id: uuid.UUID,
) -> tuple[_RunPolicy, tuple[str, ...]]:
    with session_factory() as session, session.begin():
        run = session.get(IngestionRun, run_id)
        if run is None:
            raise ValueError("run not found")
        policy = _RunPolicy(run.concurrency, run.retry_limit)
        if run.status in {"COMPLETED", "FAILED"}:
            return policy, ()
        session.execute(
            update(IngestionRunUnit)
            .where(
                IngestionRunUnit.run_id == run_id,
                IngestionRunUnit.status == "RUNNING",
            )
            .values(status="PENDING")
        )
        run.status = "RUNNING"
        run.stop_reason = None
        run.started_at = run.started_at or datetime.now(UTC)
        pending = tuple(
            session.scalars(
                select(IngestionRunUnit.unit_key)
                .where(
                    IngestionRunUnit.run_id == run_id,
                    IngestionRunUnit.status == "PENDING",
                )
                .order_by(IngestionRunUnit.position)
            ).all()
        )
        return policy, pending


def _start_unit(session_factory: SessionFactory, run_id: uuid.UUID, unit: str) -> None:
    with session_factory() as session, session.begin():
        result = session.execute(
            update(IngestionRunUnit)
            .where(
                IngestionRunUnit.run_id == run_id,
                IngestionRunUnit.unit_key == unit,
                IngestionRunUnit.status == "PENDING",
            )
            .values(status="RUNNING", attempts=IngestionRunUnit.attempts + 1)
        )
        if result.rowcount != 1:
            raise RuntimeError(f"unit is not pending: {unit}")


def _record_unit(
    session_factory: SessionFactory,
    run_id: uuid.UUID,
    unit: str,
    status: str,
    result: UnitResult,
) -> None:
    payload = {
        "outcome": result.outcome.value,
        "reason": result.reason,
        "retry_after_seconds": result.retry_after_seconds,
        **result.summary,
    }
    with session_factory() as session, session.begin():
        session.execute(
            update(IngestionRunUnit)
            .where(
                IngestionRunUnit.run_id == run_id,
                IngestionRunUnit.unit_key == unit,
            )
            .values(
                status=status,
                result_json=payload,
                published_version_id=result.published_version_id,
            )
        )


async def _drain_active(
    active: dict[asyncio.Future[UnitResult], str],
    session_factory: SessionFactory,
    run_id: uuid.UUID,
) -> str | None:
    if not active:
        return None
    results = await asyncio.gather(*active, return_exceptions=True)
    hard_stop = None
    for (_task, unit), value in zip(active.items(), results, strict=True):
        result = _exception_result(value) if isinstance(value, BaseException) else value
        status = "SUCCEEDED" if result.outcome is UnitOutcome.SUCCEEDED else "FAILED"
        if result.outcome in {UnitOutcome.RATE_LIMITED, UnitOutcome.RETRYABLE}:
            status = "PENDING"
        elif result.outcome is UnitOutcome.AUTH_REQUIRED:
            hard_stop = "AUTH_REQUIRED"
        _record_unit(session_factory, run_id, unit, status, result)
    active.clear()
    return hard_stop


def _task_result(task: asyncio.Future[UnitResult]) -> UnitResult:
    try:
        return task.result()
    except Exception as exc:  # noqa: BLE001 - provider failure must remain unit-scoped
        return _exception_result(exc)


def _exception_result(exc: BaseException) -> UnitResult:
    return UnitResult(UnitOutcome.FAILED, {}, reason=type(exc).__name__)


def _retry_delay(
    retry_after_seconds: int | None,
    retries: int,
    jitter: Callable[[float, float], float],
) -> float:
    if retry_after_seconds is not None and retry_after_seconds >= 0:
        return float(retry_after_seconds)
    base = min(float(2**retries), 60.0)
    return base + jitter(0.0, min(1.0, base / 10))


def _finish_from_counts(session_factory: SessionFactory, run_id: uuid.UUID) -> RunSummary:
    summary = load_run_summary(session_factory, run_id)
    status = "PARTIAL" if summary.failed else "COMPLETED"
    _set_run_status(session_factory, run_id, status, None)
    return load_run_summary(session_factory, run_id)


def _set_run_status(
    session_factory: SessionFactory,
    run_id: uuid.UUID,
    status: str,
    reason: str | None,
) -> None:
    completed_at = None if status in {"PLANNED", "RUNNING", "PAUSED"} else datetime.now(UTC)
    with session_factory() as session, session.begin():
        result = session.execute(
            update(IngestionRun)
            .where(IngestionRun.id == run_id)
            .values(status=status, stop_reason=reason, completed_at=completed_at)
        )
        if result.rowcount != 1:
            raise ValueError("run not found")
