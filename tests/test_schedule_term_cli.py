from __future__ import annotations

import json
import uuid
from pathlib import Path

import pytest
from sqlalchemy import create_engine, update
from sqlalchemy.orm import Session, sessionmaker
from sqlalchemy.pool import StaticPool

from db.models import IngestionRun, IngestionRunUnit
from ingestion.orchestration import create_run, plan_run
from ingestion.probes.core import ProbeStatus
from ingestion.schedule import sync_term
from ingestion.schedule.oscar import DiscoveryResult
from ingestion.schedule.sync import SyncOutcome, SyncResult


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


async def _ready_probe(*args, **kwargs):
    return ProbeStatus.READY, None


@pytest.mark.asyncio
async def test_fresh_explicit_run_discovers_once_and_plans_only_selected_subjects(
    sessions, monkeypatch, tmp_path: Path
):
    discoveries = 0
    collected: list[str] = []

    async def discover(*args, **kwargs):
        nonlocal discoveries
        discoveries += 1
        return DiscoveryResult(ProbeStatus.READY, ("AE", "CS", "ECE"), 1)

    async def collect(_term, subject, *args, **kwargs):
        collected.append(subject)
        return SyncResult(SyncOutcome.PUBLISHED, ProbeStatus.READY, 1, version_id=uuid.uuid4())

    monkeypatch.setattr(sync_term, "probe_provider", _ready_probe)
    monkeypatch.setattr(sync_term, "discover_subjects", discover)
    monkeypatch.setattr(sync_term, "collect_subject", collect)

    summary = await sync_term.sync_oscar_term(
        term="202608",
        subjects=("CS",),
        output_dir=tmp_path,
        session_factory=sessions,
    )

    assert discoveries == 1
    assert collected == ["CS"]
    assert summary.scope == {"term": "202608", "selection": "explicit"}
    assert summary.planned_units == ("CS",)
    assert summary.status == "COMPLETED"


@pytest.mark.asyncio
async def test_resume_uses_stored_manifest_without_discovery(sessions, monkeypatch, tmp_path: Path):
    run_id = create_run(
        sessions,
        "public-oscar",
        {"term": "202608", "selection": "all"},
        concurrency=1,
    )
    plan_run(sessions, run_id, ("AE", "CS"))
    with sessions() as session, session.begin():
        session.execute(
            update(IngestionRunUnit)
            .where(IngestionRunUnit.run_id == run_id, IngestionRunUnit.unit_key == "AE")
            .values(status="SUCCEEDED")
        )

    async def no_discovery(*args, **kwargs):
        raise AssertionError("resume must not rediscover subjects")

    collected: list[str] = []

    async def collect(_term, subject, *args, **kwargs):
        collected.append(subject)
        return SyncResult(SyncOutcome.PUBLISHED, ProbeStatus.READY, 1, version_id=uuid.uuid4())

    monkeypatch.setattr(sync_term, "probe_provider", _ready_probe)
    monkeypatch.setattr(sync_term, "discover_subjects", no_discovery)
    monkeypatch.setattr(sync_term, "collect_subject", collect)

    summary = await sync_term.sync_oscar_term(
        run_id=run_id,
        resume=True,
        output_dir=tmp_path,
        session_factory=sessions,
    )

    assert collected == ["CS"]
    assert summary.planned_units == ("AE", "CS")
    assert summary.status == "COMPLETED"


@pytest.mark.asyncio
async def test_explicit_subject_must_exist_in_discovered_manifest(sessions, monkeypatch, tmp_path):
    monkeypatch.setattr(sync_term, "probe_provider", _ready_probe)
    monkeypatch.setattr(
        sync_term,
        "discover_subjects",
        lambda *args, **kwargs: _async_result(DiscoveryResult(ProbeStatus.READY, ("AE", "CS"), 1)),
    )

    summary = await sync_term.sync_oscar_term(
        term="202608",
        subjects=("MATH",),
        output_dir=tmp_path,
        session_factory=sessions,
    )

    assert summary.status == "FAILED"
    assert summary.stop_reason == "SUBJECT_NOT_OFFERED"
    assert summary.planned == 0


def _async_result(value):
    async def result():
        return value

    return result()


def test_cli_prints_one_compact_summary_line(monkeypatch, capsys):
    run_id = uuid.UUID("840b880e-e28a-49de-9550-5ff42f976b6a")

    async def fake_sync(**kwargs):
        from ingestion.orchestration import RunSummary

        return RunSummary(
            run_id,
            "public-oscar",
            {"term": "202608", "selection": "explicit"},
            "COMPLETED",
            1,
            1,
            0,
            0,
            True,
            None,
            ("CS",),
        )

    monkeypatch.setattr(sync_term, "sync_oscar_term", fake_sync)

    exit_code = sync_term.main(["--term", "202608", "--subjects", "CS", "--probe-course", "7650"])

    lines = capsys.readouterr().out.splitlines()
    assert exit_code == 0
    assert len(lines) == 1
    assert json.loads(lines[0]) == {
        "run_id": str(run_id),
        "provider": "public-oscar",
        "scope": {"term": "202608", "selection": "explicit"},
        "status": "COMPLETED",
        "planned": 1,
        "succeeded": 1,
        "failed": 0,
        "remaining": 0,
        "complete": True,
        "stop_reason": None,
        "planned_units": ["CS"],
    }
