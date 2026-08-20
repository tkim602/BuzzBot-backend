"""Database coverage audit for source completeness and deadline entities."""

from __future__ import annotations

import json
from datetime import UTC, datetime
from pathlib import Path

from sqlalchemy import create_engine, text

from app.core.config import settings

ARTIFACT_PATH = Path(__file__).resolve().parent.parent / "artifacts" / "db_coverage_audit.json"


def _engine():
    return create_engine(settings.database_url_sync)


def run() -> dict:
    report: dict = {
        "generated_at": datetime.now(UTC).isoformat(),
        "source_summary": [],
        "entity_presence": {},
    }

    source_query = text(
        """
        select
            s.name,
            coalesce((select count(*) from documents d where d.source_id = s.id), 0) as docs,
            coalesce((select count(*) from chunks c where c.source_id = s.id), 0) as chunks,
            coalesce((select sum(case when d.title is null or d.title = '' then 1 else 0 end)
                      from documents d where d.source_id = s.id), 0) as docs_null_title,
            coalesce((select sum(case when c.title is null or c.title = '' then 1 else 0 end)
                      from chunks c where c.source_id = s.id), 0) as chunks_null_title,
            (select max(d.fetched_at) from documents d where d.source_id = s.id) as latest_doc_fetch
        from sources s
        order by s.name
        """
    )

    checks = {
        "has_omscs_documents": text(
            """
            select count(*)
            from documents
            where canonical_url ilike '%omscs%' or title ilike '%omscs%'
            """
        ),
        "has_admission_domain_documents": text(
            """
            select count(*)
            from documents
            where canonical_url ilike 'https://admission.gatech.edu%'
            """
        ),
        "has_mscs_feb1_chunk": text(
            """
            select count(*)
            from chunks
            where url ilike '%computer-science-ms%'
              and chunk_text ilike '%application deadline%'
              and chunk_text ilike '%February 1%'
            """
        ),
        "has_omscs_deadline_chunk": text(
            """
            select count(*)
            from chunks
            where (url ilike '%omscs%' or chunk_text ilike '%omscs%')
              and chunk_text ilike '%deadline%'
            """
        ),
        "has_application_deadline_chunks": text(
            """
            select count(*)
            from chunks
            where chunk_text ilike '%application deadline%'
            """
        ),
    }

    with _engine().connect() as conn:
        rows = conn.execute(source_query).fetchall()
        for row in rows:
            report["source_summary"].append(
                {
                    "source": row.name,
                    "documents": int(row.docs),
                    "chunks": int(row.chunks),
                    "documents_missing_title": int(row.docs_null_title),
                    "chunks_missing_title": int(row.chunks_null_title),
                    "latest_doc_fetch": row.latest_doc_fetch.isoformat()
                    if row.latest_doc_fetch
                    else None,
                }
            )

        for check_name, query in checks.items():
            count = int(conn.execute(query).scalar() or 0)
            report["entity_presence"][check_name] = {
                "count": count,
                "present": count > 0,
            }

    ARTIFACT_PATH.parent.mkdir(parents=True, exist_ok=True)
    ARTIFACT_PATH.write_text(json.dumps(report, indent=2, ensure_ascii=False))
    return report


if __name__ == "__main__":
    result = run()
    print(json.dumps(result, indent=2, ensure_ascii=False))
    print(f"\nWrote: {ARTIFACT_PATH}")
