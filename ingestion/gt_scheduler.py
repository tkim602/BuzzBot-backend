"""GT Scheduler JSON ingestion — fetch course data from gt-scheduler.github.io."""

from __future__ import annotations

import json
import uuid
from datetime import datetime, timezone
from pathlib import Path

import httpx
import structlog
from dotenv import load_dotenv

# Load .env before importing anything that needs API keys
load_dotenv()

from db.models import Chunk, Document, Embedding, Source
from db.session import SyncSessionLocal
from ingestion.index import get_embedding_function

logger = structlog.get_logger(__name__)

GT_SCHEDULER_INDEX_URL = "https://gt-scheduler.github.io/crawler-v2/index.json"
GT_SCHEDULER_TERM_URL = "https://gt-scheduler.github.io/crawler-v2/{term}.json"

# Term code to human-readable name
TERM_NAMES = {
    "02": "Spring",
    "05": "Summer", 
    "08": "Fall",
}


def term_to_name(term_code: str) -> str:
    """Convert term code like '202602' to 'Spring 2026'."""
    year = term_code[:4]
    semester = TERM_NAMES.get(term_code[4:], "Unknown")
    return f"{semester} {year}"


async def fetch_available_terms() -> list[str]:
    """Fetch list of available term codes from GT Scheduler."""
    async with httpx.AsyncClient(timeout=30) as client:
        resp = await client.get(GT_SCHEDULER_INDEX_URL)
        resp.raise_for_status()
        data = resp.json()
    
    terms = [t["term"] for t in data.get("terms", [])]
    logger.info("fetched available terms", count=len(terms))
    return terms


async def fetch_term_data(term: str) -> dict:
    """Fetch course data for a specific term."""
    url = GT_SCHEDULER_TERM_URL.format(term=term)
    async with httpx.AsyncClient(timeout=60) as client:
        resp = await client.get(url)
        resp.raise_for_status()
        return resp.json()


def parse_course_to_chunks(
    course_code: str,
    course_data: list,
    term: str,
    term_name: str,
) -> list[dict]:
    """Parse a single course into searchable text chunks."""
    if not course_data or len(course_data) < 2:
        return []
    
    course_name = course_data[0]  # e.g., "Principles of Accounting I"
    sections = course_data[1] if len(course_data) > 1 else {}
    prereqs = course_data[2] if len(course_data) > 2 else []
    description = course_data[3] if len(course_data) > 3 else ""
    
    chunks = []
    crns: list[str] = []
    instructors_seen: set[str] = set()
    section_ids: list[str] = []

    # Main course info chunk
    main_text = f"""Course: {course_code} - {course_name}
Term: {term_name}
Description: {description if description else 'No description available.'}
"""
    if prereqs:
        main_text += f"Prerequisites: {', '.join(str(p) for p in prereqs) if prereqs else 'None'}\n"
    
    chunks.append({
        "text": main_text.strip(),
        "metadata": {
            "course_code": course_code,
            "course_name": course_name,
            "term": term,
            "term_name": term_name,
            "type": "course_info",
            "content_type": "course_description",
        }
    })
    
    # Section chunks
    if isinstance(sections, dict):
        for section_id, section_data in sections.items():
            if not section_data or not isinstance(section_data, list):
                continue
            
            try:
                crn = section_data[0] if len(section_data) > 0 else "N/A"
                meetings = section_data[1] if len(section_data) > 1 else []
                credits = section_data[2] if len(section_data) > 2 else "N/A"
                section_ids.append(str(section_id))
                crns.append(str(crn))
                
                section_text = f"""Course: {course_code} - {course_name}
Section: {section_id}
Term: {term_name}
CRN: {crn}
Credits: {credits}
"""
                
                # Parse meetings
                for meeting in meetings:
                    if isinstance(meeting, list) and len(meeting) >= 5:
                        days = meeting[1] if meeting[1] else "TBA"
                        location = meeting[2] if meeting[2] else "TBA"
                        instructors = meeting[4] if isinstance(meeting[4], list) else []
                        instructor_str = ", ".join(instructors) if instructors else "TBA"
                        for inst in instructors:
                            if inst:
                                instructors_seen.add(str(inst))
                        
                        section_text += f"Schedule: {days}\n"
                        section_text += f"Location: {location}\n"
                        section_text += f"Instructor: {instructor_str}\n"
                
                chunks.append({
                    "text": section_text.strip(),
                    "metadata": {
                        "course_code": course_code,
                        "course_name": course_name,
                        "section": section_id,
                        "crn": str(crn),
                        "term": term,
                        "term_name": term_name,
                        "type": "section",
                        "content_type": "section_schedule",
                    }
                })
            except (IndexError, TypeError) as e:
                logger.debug("failed to parse section", course=course_code, section=section_id, error=str(e))
                continue

    section_count = len(section_ids)
    instructors_list = sorted(instructors_seen)
    summary_text = (
        f"Course: {course_code} - {course_name}\n"
        f"Term: {term_name}\n"
        f"Offering summary: {course_code} is offered in {term_name} with {section_count} section(s).\n"
        f"Sections: {', '.join(section_ids[:20]) if section_ids else 'None listed'}\n"
        f"CRNs: {', '.join(crns[:30]) if crns else 'None listed'}\n"
        f"Instructors: {', '.join(instructors_list[:20]) if instructors_list else 'TBA'}"
    )
    chunks.insert(
        0,
        {
            "text": summary_text.strip(),
            "metadata": {
                "course_code": course_code,
                "course_name": course_name,
                "term": term,
                "term_name": term_name,
                "type": "course_summary",
                "content_type": "course_summary",
                "section_count": section_count,
            },
        },
    )

    return chunks


def ingest_term(term: str, course_data: dict, embed_fn) -> dict:
    """Ingest all courses for a term into the database."""
    session = SyncSessionLocal()
    term_name = term_to_name(term)
    stats = {"term": term, "term_name": term_name, "courses": 0, "chunks": 0, "errors": 0}
    
    try:
        # Upsert source
        source = session.query(Source).filter(Source.name == "gt-scheduler").first()
        if not source:
            source = Source(
                name="gt-scheduler",
                base_url="https://gt-scheduler.github.io/crawler-v2/",
                allowed=True,
                reason="GT Scheduler course data — public JSON from GitHub Pages",
            )
            session.add(source)
            session.flush()
        
        source_id = source.id
        
        # Upsert document for term
        doc_url = f"https://gt-scheduler.github.io/crawler-v2/{term}.json"
        doc = session.query(Document).filter(Document.canonical_url == doc_url).first()
        if not doc:
            doc = Document(
                source_id=source_id,
                canonical_url=doc_url,
                title=f"GT Course Schedule - {term_name}",
                fetched_at=datetime.now(timezone.utc),
            )
            session.add(doc)
            session.flush()
        else:
            doc.fetched_at = datetime.now(timezone.utc)
            session.flush()
        
        doc_id = doc.doc_id
        
        # Delete existing chunks for this document
        session.query(Embedding).filter(
            Embedding.chunk_id.in_(
                session.query(Chunk.chunk_id).filter(Chunk.doc_id == doc_id)
            )
        ).delete(synchronize_session=False)
        session.query(Chunk).filter(Chunk.doc_id == doc_id).delete()
        session.flush()
        
        # Parse courses
        courses = course_data.get("courses", {})
        all_chunks = []
        
        for course_code, course_info in courses.items():
            try:
                chunks = parse_course_to_chunks(course_code, course_info, term, term_name)
                all_chunks.extend(chunks)
                stats["courses"] += 1
            except Exception as e:
                logger.warning("failed to parse course", course=course_code, error=str(e))
                stats["errors"] += 1
        
        # Batch embed and insert chunks
        if all_chunks:
            texts = [c["text"] for c in all_chunks]
            
            # Batch in groups of 100
            batch_size = 100
            for i in range(0, len(all_chunks), batch_size):
                batch_chunks = all_chunks[i:i + batch_size]
                batch_texts = texts[i:i + batch_size]
                
                try:
                    embeddings = embed_fn(batch_texts)
                except Exception as e:
                    logger.error("embedding failed", error=str(e))
                    stats["errors"] += len(batch_chunks)
                    continue
                
                for chunk_data, embedding in zip(batch_chunks, embeddings):
                    import hashlib
                    chunk_hash = hashlib.sha256(chunk_data["text"].encode()).hexdigest()[:16]
                    
                    chunk = Chunk(
                        doc_id=doc_id,
                        source_id=source_id,
                        url=doc_url,
                        title=f"{chunk_data['metadata']['course_code']} - {term_name}",
                        chunk_text=chunk_data["text"],
                        chunk_hash=chunk_hash,
                        token_count=len(chunk_data["text"].split()),
                        fetched_at=datetime.now(timezone.utc),
                        metadata_json=chunk_data["metadata"],
                    )
                    session.add(chunk)
                    session.flush()
                    
                    emb = Embedding(chunk_id=chunk.chunk_id, embedding=embedding)
                    session.add(emb)
                    stats["chunks"] += 1
        
        session.commit()
        logger.info("term ingestion complete", **stats)
        
    except Exception as e:
        session.rollback()
        logger.error("term ingestion failed", term=term, error=str(e))
        stats["errors"] += 1
    finally:
        session.close()
    
    return stats


async def ingest_all_terms(terms: list[str] | None = None) -> list[dict]:
    """Ingest all (or specified) terms from GT Scheduler."""
    available_terms = await fetch_available_terms()
    
    if terms:
        # Filter to requested terms
        terms_to_ingest = [t for t in terms if t in available_terms]
    else:
        # All available terms
        terms_to_ingest = available_terms
    
    logger.info("starting GT Scheduler ingestion", terms=len(terms_to_ingest))
    
    embed_fn = get_embedding_function()
    results = []
    
    for term in terms_to_ingest:
        logger.info("fetching term data", term=term, name=term_to_name(term))
        try:
            data = await fetch_term_data(term)
            stats = ingest_term(term, data, embed_fn)
            results.append(stats)
        except Exception as e:
            logger.error("failed to fetch term", term=term, error=str(e))
            results.append({"term": term, "error": str(e)})
    
    return results


async def ingest_current_and_recent(num_recent: int = 4) -> list[dict]:
    """Ingest current semester and recent N semesters."""
    available_terms = await fetch_available_terms()
    
    # Sort by term code (most recent last)
    sorted_terms = sorted(available_terms)
    
    # Get most recent terms
    recent_terms = sorted_terms[-num_recent:] if len(sorted_terms) >= num_recent else sorted_terms
    
    logger.info("ingesting recent terms", terms=recent_terms)
    return await ingest_all_terms(recent_terms)


if __name__ == "__main__":
    import asyncio
    import sys
    
    if len(sys.argv) > 1 and sys.argv[1] == "--all":
        asyncio.run(ingest_all_terms())
    else:
        # Default: recent 4 semesters
        asyncio.run(ingest_current_and_recent(4))
