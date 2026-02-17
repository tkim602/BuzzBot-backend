"""Request/response schemas for the chat API."""

from __future__ import annotations

from typing import Literal

from pydantic import BaseModel, Field


class UserContext(BaseModel):
    term: str | None = None
    major: str | None = None


class ChatRequest(BaseModel):
    query: str = Field(..., min_length=1, max_length=2000)
    user_context: UserContext | None = None
    rmp_excerpt: str | None = Field(None, max_length=5000)
    history: list["ChatTurn"] = Field(default_factory=list)


class ChatTurn(BaseModel):
    role: Literal["user", "assistant"]
    content: str = Field(..., min_length=1, max_length=4000)


class Citation(BaseModel):
    url: str
    title: str | None = None
    fetched_at: str | None = None
    quote: str


class FreshnessInfo(BaseModel):
    strategy: str = "indexed"  # indexed | live_fetch | hybrid
    as_of: str | None = None


class DebugInfo(BaseModel):
    intent: str | None = None
    live_fetch_used: bool = False
    retrieval_top_k: int = 0
    rewritten_query: str | None = None
    current_date: str | None = None
    current_term: str | None = None


class ChatResponse(BaseModel):
    answer: str
    citations: list[Citation] = []
    confidence: float = Field(0.0, ge=0.0, le=1.0)
    freshness: FreshnessInfo = FreshnessInfo()
    notes: list[str] = []
    debug: DebugInfo = DebugInfo()
