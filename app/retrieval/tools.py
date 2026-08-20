from __future__ import annotations

from dataclasses import dataclass
from typing import cast

from sqlalchemy.ext.asyncio import AsyncSession

from app.retrieval.documents import DocumentEvidence, PolicyQuery, search_policy_docs


@dataclass(frozen=True)
class CourseDetailsQuery:
    subject: str
    course_number: str
    top_k: int = 5

    def __post_init__(self) -> None:
        subject = self.subject.strip().upper()
        course_number = self.course_number.strip().upper()
        if not subject:
            raise ValueError("subject is required")
        if not course_number:
            raise ValueError("course_number is required")
        if not 1 <= self.top_k <= 20:
            raise ValueError("top_k must be between 1 and 20")
        object.__setattr__(self, "subject", subject)
        object.__setattr__(self, "course_number", course_number)


@dataclass(frozen=True)
class RegistrationCalendarQuery:
    text: str
    top_k: int = 5

    def __post_init__(self) -> None:
        text = self.text.strip()
        if not text:
            raise ValueError("text is required")
        if not 1 <= self.top_k <= 20:
            raise ValueError("top_k must be between 1 and 20")
        object.__setattr__(self, "text", text)


async def lookup_course_details(
    session: AsyncSession,
    query: CourseDetailsQuery,
    query_embedding: list[float],
) -> list[DocumentEvidence]:
    return cast(
        list[DocumentEvidence],
        await search_policy_docs(
            session,
            PolicyQuery(
                f"{query.subject} {query.course_number} course description credits prerequisites",
                source_types=("course_catalog",),
                top_k=query.top_k,
            ),
            query_embedding,
        ),
    )


async def lookup_registration_calendar(
    session: AsyncSession,
    query: RegistrationCalendarQuery,
    query_embedding: list[float],
) -> list[DocumentEvidence]:
    return cast(
        list[DocumentEvidence],
        await search_policy_docs(
            session,
            PolicyQuery(
                query.text,
                source_types=("academic_calendar",),
                top_k=query.top_k,
            ),
            query_embedding,
        ),
    )
