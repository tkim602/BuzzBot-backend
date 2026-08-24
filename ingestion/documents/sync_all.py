from __future__ import annotations

import uuid
from collections.abc import Callable, Sequence
from contextlib import AbstractContextManager

import httpx
from sqlalchemy import select
from sqlalchemy.orm import Session

from app.db.models import IngestionRun, IngestionRunUnit
from ingestion.documents.registry import DocumentSource
from ingestion.documents.sync import sync_document_url
from ingestion.documents.sync_source import PROVIDER, _discover, _unit_result
from ingestion.orchestration import (
    RunSummary,
    create_run,
    fail_run,
    load_run_summary,
    plan_run,
    reset_failed_units,
    run_batch,
)

SessionFactory = Callable[[], AbstractContextManager[Session]]


async def sync_document_profile(
    profile: str,
    sources: Sequence[DocumentSource],
    session_factory: SessionFactory,
    embed_fn,
    transport: httpx.AsyncBaseTransport | None = None,
    *,
    run_id: uuid.UUID | None = None,
    resume: bool = False,
    verification_limit: int | None = None,
    concurrency: int = 2,
    retry_limit: int = 2,
) -> RunSummary:
    selected = {source.name: source for source in sources if profile in source.profiles}
    if not selected:
        raise ValueError(f"profile has no sources: {profile}")
    manifest: list[dict[str, str]]

    if resume:
        if run_id is None or verification_limit is not None:
            raise ValueError("resume requires run_id and no verification limit")
        summary = load_run_summary(session_factory, run_id)
        if summary.provider != PROVIDER or summary.scope.get("profile") != profile:
            raise ValueError("run does not belong to this document profile")
        manifest = _manifest(summary.scope)
        missing = {item["source"] for item in manifest} - selected.keys()
        if missing:
            raise ValueError(f"profile sources are unavailable: {', '.join(sorted(missing))}")
        failed = _failed_units(session_factory, run_id)
        if failed:
            reset_failed_units(session_factory, run_id, failed)
    else:
        if run_id is not None or verification_limit is not None and verification_limit < 1:
            raise ValueError("fresh run requires no run_id and a positive verification limit")
        run_id = create_run(
            session_factory,
            PROVIDER,
            {"profile": profile},
            concurrency=concurrency,
            retry_limit=retry_limit,
        )
        manifest = []
        for source in selected.values():
            urls, error = await _discover(source, transport, None)
            if error is not None:
                return fail_run(session_factory, run_id, f"{source.name}:{error}")
            manifest.extend(
                {
                    "unit_key": f"{source.name}:{index:04d}",
                    "source": source.name,
                    "url": url,
                    "adapter": source.adapter or source.authority,
                    "vertical": source.vertical,
                }
                for index, url in enumerate(urls)
            )
        if verification_limit is not None:
            manifest = manifest[:verification_limit]
        if not manifest:
            return fail_run(session_factory, run_id, "NO_DOCUMENT_URLS")
        _store_manifest(session_factory, run_id, profile, manifest)
        plan_run(session_factory, run_id, [item["unit_key"] for item in manifest])

    entries = {item["unit_key"]: item for item in manifest}

    async def run_unit(unit_key: str):
        item = entries[unit_key]
        source = selected[item["source"]]
        return _unit_result(
            await sync_document_url(
                source,
                item["url"],
                session_factory,
                embed_fn,
                transport,
            )
        )

    return await run_batch(run_id, session_factory, run_unit)


def profile_coverage(
    session_factory: SessionFactory, run_id: uuid.UUID
) -> dict[str, dict[str, int]]:
    with session_factory() as session:
        run = session.get(IngestionRun, run_id)
        if run is None:
            raise ValueError("run not found")
        if not run.scope_json.get("manifest"):
            return {}
        vertical_by_unit = {
            item["unit_key"]: item["vertical"] for item in _manifest(run.scope_json)
        }
        units = session.scalars(
            select(IngestionRunUnit).where(IngestionRunUnit.run_id == run_id)
        ).all()
    coverage: dict[str, dict[str, int]] = {}
    for unit in units:
        counts = coverage.setdefault(
            vertical_by_unit[unit.unit_key],
            {"planned": 0, "succeeded": 0, "failed": 0, "remaining": 0},
        )
        counts["planned"] += 1
        if unit.status == "SUCCEEDED":
            counts["succeeded"] += 1
        elif unit.status == "FAILED":
            counts["failed"] += 1
        else:
            counts["remaining"] += 1
    return dict(sorted(coverage.items()))


def _manifest(scope: dict[str, object]) -> list[dict[str, str]]:
    raw = scope.get("manifest")
    if not isinstance(raw, list) or not raw:
        raise ValueError("run has no immutable document manifest")
    required = {"unit_key", "source", "url", "adapter", "vertical"}
    if any(not isinstance(item, dict) or not required <= item.keys() for item in raw):
        raise ValueError("run document manifest is invalid")
    return raw


def _store_manifest(
    session_factory: SessionFactory,
    run_id: uuid.UUID,
    profile: str,
    manifest: list[dict[str, str]],
) -> None:
    with session_factory() as session, session.begin():
        run = session.get(IngestionRun, run_id)
        if run is None or run.status != "PLANNED":
            raise ValueError("run is unavailable for planning")
        run.scope_json = {"profile": profile, "manifest": manifest}


def _failed_units(session_factory: SessionFactory, run_id: uuid.UUID) -> tuple[str, ...]:
    with session_factory() as session:
        return tuple(
            session.scalars(
                select(IngestionRunUnit.unit_key).where(
                    IngestionRunUnit.run_id == run_id,
                    IngestionRunUnit.status == "FAILED",
                )
            ).all()
        )
