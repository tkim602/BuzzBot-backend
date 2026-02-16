"""RateMyProfessors user-provided mode — NO automated crawling."""

from __future__ import annotations

from app.schemas.chat import Citation


def build_rmp_context(excerpt: str) -> str:
    """Format a user-provided RMP excerpt for the RAG context.

    This is user-provided mode ONLY. BuzzBot does NOT crawl or scrape
    RateMyProfessors.com due to their Terms of Service.
    """
    return (
        "[User-Provided RateMyProfessors Excerpt — Unofficial and Unverified]\n\n"
        + excerpt.strip()
        + "\n\n"
        "[Note: This content was pasted by the user. BuzzBot cannot verify its accuracy "
        "or recency. Always check the original source for the latest information.]"
    )


def rmp_citation() -> Citation:
    """Standard citation for user-provided RMP content."""
    return Citation(
        url="user-provided:rmp",
        title="User-provided RateMyProfessors excerpt",
        fetched_at=None,
        quote="User-provided content — unofficial and unverified.",
    )
