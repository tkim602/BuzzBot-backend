"""GT Academic Calendar ingestion — parse PDF calendars and fallback JSON data."""

from __future__ import annotations

import hashlib
import json
from datetime import UTC, datetime
from pathlib import Path

import structlog

from db.models import Chunk, Document, Embedding, Source
from db.session import SyncSessionLocal
from ingestion.index import get_embedding_function

logger = structlog.get_logger(__name__)

SOURCE_NAME = "gt-calendar-events"
CALENDAR_DATA_DIR = Path(__file__).parent / "calendar_data"
PDF_DIR = CALENDAR_DATA_DIR / "pdfs"
FALLBACK_JSON = CALENDAR_DATA_DIR / "verified_calendars.json"


def parse_pdf_calendar(pdf_path: Path) -> list[dict]:
    """Parse a GT academic calendar PDF into structured events using pdfplumber."""
    try:
        import pdfplumber
    except ImportError:
        logger.warning("pdfplumber not installed, skipping PDF parsing")
        return []

    events = []
    try:
        with pdfplumber.open(pdf_path) as pdf:
            for page in pdf.pages:
                tables = page.extract_tables()
                for table in tables:
                    if not table:
                        continue
                    for row in table:
                        if not row or len(row) < 2:
                            continue
                        # Skip header rows
                        cells = [c.strip() if c else "" for c in row]
                        if not any(cells) or cells[0].lower() in ("date", "dates", ""):
                            continue
                        # Try to parse: typically Date | Event or Date | Category | Event
                        if len(cells) >= 3 and cells[1] and cells[2]:
                            events.append(
                                {
                                    "date": cells[0],
                                    "category": cells[1],
                                    "event": cells[2],
                                    "description": cells[3]
                                    if len(cells) > 3 and cells[3]
                                    else cells[2],
                                }
                            )
                        elif len(cells) >= 2 and cells[0] and cells[1]:
                            events.append(
                                {
                                    "date": cells[0],
                                    "category": "General",
                                    "event": cells[1],
                                    "description": cells[1],
                                }
                            )
    except Exception as e:
        logger.error("failed to parse PDF", path=str(pdf_path), error=str(e))

    logger.info("parsed PDF calendar", path=pdf_path.name, events=len(events))
    return events


def load_fallback_calendars() -> dict[str, list[dict]]:
    """Load verified calendar data from JSON fallback."""
    if not FALLBACK_JSON.exists():
        logger.warning("fallback calendar JSON not found", path=str(FALLBACK_JSON))
        return {}
    with open(FALLBACK_JSON) as f:
        return json.load(f)


def build_chunk_text(semester: str, event: dict) -> str:
    """Build rich-text chunk for a single calendar event."""
    return (
        f"Georgia Tech Academic Calendar — {semester}\n"
        f"Category: {event['category']}\n"
        f"Date: {event['date']}\n"
        f"Event: {event['event']}\n"
        f"{event.get('description', event['event'])}"
    )


def build_chunk_metadata(semester: str, event: dict) -> dict:
    """Build metadata dict for a calendar event chunk."""
    return {
        "semester": semester,
        "category": event["category"],
        "date": event["date"],
        "type": "calendar_event",
        "content_type": "academic_calendar",
    }


def ingest_calendar() -> dict:
    """Ingest all calendar data (PDFs + fallback JSON) into the database."""
    session = SyncSessionLocal()
    stats = {"semesters": 0, "chunks": 0, "errors": 0}

    try:
        # Upsert source
        source = session.query(Source).filter(Source.name == SOURCE_NAME).first()
        if not source:
            source = Source(
                name=SOURCE_NAME,
                base_url="https://registrar.gatech.edu/calendar",
                allowed=True,
                reason="Georgia Tech academic calendar events — structured date data.",
            )
            session.add(source)
            session.flush()

        source_id = source.id

        # Upsert document
        doc_url = "https://registrar.gatech.edu/calendar/academic-calendar"
        doc = session.query(Document).filter(Document.canonical_url == doc_url).first()
        if not doc:
            doc = Document(
                source_id=source_id,
                canonical_url=doc_url,
                title="GT Academic Calendar — All Semesters",
                fetched_at=datetime.now(UTC),
            )
            session.add(doc)
            session.flush()
        else:
            doc.fetched_at = datetime.now(UTC)
            session.flush()

        doc_id = doc.doc_id

        # Delete existing chunks for this document
        session.query(Embedding).filter(
            Embedding.chunk_id.in_(session.query(Chunk.chunk_id).filter(Chunk.doc_id == doc_id))
        ).delete(synchronize_session=False)
        session.query(Chunk).filter(Chunk.doc_id == doc_id).delete()
        session.flush()

        # Collect all events: fallback JSON + any PDFs
        all_events: dict[str, list[dict]] = {}

        # Load fallback JSON
        fallback = load_fallback_calendars()
        for semester, events in fallback.items():
            all_events[semester] = events

        # Parse PDFs if directory exists
        if PDF_DIR.exists():
            for pdf_file in sorted(PDF_DIR.glob("*.pdf")):
                pdf_events = parse_pdf_calendar(pdf_file)
                if pdf_events:
                    # Infer semester from filename
                    name = pdf_file.stem.lower().replace("%20", " ").replace("_", " ")
                    semester_name = _infer_semester(name)
                    if semester_name and semester_name not in all_events:
                        all_events[semester_name] = pdf_events

        # Build chunks
        all_chunks = []
        for semester, events in sorted(all_events.items()):
            stats["semesters"] += 1
            for event in events:
                text = build_chunk_text(semester, event)
                metadata = build_chunk_metadata(semester, event)
                all_chunks.append({"text": text, "metadata": metadata, "semester": semester})

        # Embed and insert
        if all_chunks:
            embed_fn = get_embedding_function()
            texts = [c["text"] for c in all_chunks]
            batch_size = 100

            for i in range(0, len(all_chunks), batch_size):
                batch_chunks = all_chunks[i : i + batch_size]
                batch_texts = texts[i : i + batch_size]

                try:
                    embeddings = embed_fn(batch_texts)
                except Exception as e:
                    logger.error("embedding failed", error=str(e))
                    stats["errors"] += len(batch_chunks)
                    continue

                for chunk_data, embedding in zip(batch_chunks, embeddings, strict=True):
                    chunk_hash = hashlib.sha256(chunk_data["text"].encode()).hexdigest()[:16]

                    chunk = Chunk(
                        doc_id=doc_id,
                        source_id=source_id,
                        url=doc_url,
                        title=f"Academic Calendar — {chunk_data['semester']}",
                        chunk_text=chunk_data["text"],
                        chunk_hash=chunk_hash,
                        token_count=len(chunk_data["text"].split()),
                        fetched_at=datetime.now(UTC),
                        metadata_json=chunk_data["metadata"],
                    )
                    session.add(chunk)
                    session.flush()

                    emb = Embedding(chunk_id=chunk.chunk_id, embedding=embedding)
                    session.add(emb)
                    stats["chunks"] += 1

        session.commit()
        logger.info("calendar ingestion complete", **stats)

    except Exception as e:
        session.rollback()
        logger.error("calendar ingestion failed", error=str(e))
        stats["errors"] += 1
    finally:
        session.close()

    return stats


def _infer_semester(name: str) -> str | None:
    """Infer semester name like 'Spring 2025' from a PDF filename."""
    import re

    name = name.lower()
    semester = None
    if "spring" in name:
        semester = "Spring"
    elif "summer" in name:
        semester = "Summer"
    elif "fall" in name:
        semester = "Fall"

    m = re.search(r"20\d{2}", name)
    if semester and m:
        return f"{semester} {m.group()}"
    return None


if __name__ == "__main__":
    stats = ingest_calendar()
    print(f"Ingested {stats['chunks']} calendar chunks across {stats['semesters']} semesters")
    if stats["errors"]:
        print(f"  Errors: {stats['errors']}")
