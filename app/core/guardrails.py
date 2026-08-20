"""Request guardrails: rate limit, duplicate cooldown, and concurrency control."""

from __future__ import annotations

import asyncio
import hashlib
import re
import time
from collections import defaultdict, deque
from contextlib import asynccontextmanager
from threading import Lock

from fastapi import Request

from app.core.config import settings

_WS_RE = re.compile(r"\s+")


class GuardrailViolation(Exception):
    """Raised when a request guardrail is violated."""

    def __init__(self, message: str, *, retry_after_seconds: int | None = None):
        super().__init__(message)
        self.message = message
        self.retry_after_seconds = retry_after_seconds


class SlidingWindowLimiter:
    """In-memory sliding window limiter keyed by client fingerprint."""

    def __init__(self):
        self._lock = Lock()
        self._minute: dict[str, deque[float]] = defaultdict(deque)
        self._hour: dict[str, deque[float]] = defaultdict(deque)
        self._day: dict[str, deque[float]] = defaultdict(deque)
        self._last_request_at: dict[str, float] = {}
        self._recent_query_ts: dict[str, float] = {}

    def enforce(self, client_id: str, normalized_query: str) -> None:
        now = time.time()
        query_key = f"{client_id}:{normalized_query}"

        with self._lock:
            minute_q = self._minute[client_id]
            hour_q = self._hour[client_id]
            day_q = self._day[client_id]

            self._prune(minute_q, now - 60)
            self._prune(hour_q, now - 3600)
            self._prune(day_q, now - 86400)

            last_at = self._last_request_at.get(client_id)
            if last_at is not None and (now - last_at) < settings.chat_min_interval_seconds:
                retry = max(1, int(settings.chat_min_interval_seconds - (now - last_at)))
                raise GuardrailViolation(
                    "Too many requests in a short burst.",
                    retry_after_seconds=retry,
                )

            dup_at = self._recent_query_ts.get(query_key)
            if dup_at is not None and (now - dup_at) < settings.chat_duplicate_cooldown_seconds:
                retry = max(1, int(settings.chat_duplicate_cooldown_seconds - (now - dup_at)))
                raise GuardrailViolation(
                    "Duplicate query cooldown active. Please wait before sending the same query.",
                    retry_after_seconds=retry,
                )

            if len(minute_q) >= settings.chat_rate_limit_per_minute:
                raise GuardrailViolation(
                    "Per-minute request limit exceeded.",
                    retry_after_seconds=60,
                )
            if len(hour_q) >= settings.chat_rate_limit_per_hour:
                raise GuardrailViolation(
                    "Per-hour request limit exceeded.",
                    retry_after_seconds=300,
                )
            if len(day_q) >= settings.chat_rate_limit_per_day:
                raise GuardrailViolation(
                    "Daily request limit exceeded for this client.",
                    retry_after_seconds=3600,
                )

            minute_q.append(now)
            hour_q.append(now)
            day_q.append(now)
            self._last_request_at[client_id] = now
            self._recent_query_ts[query_key] = now

            # Best-effort cleanup of old duplicate keys to cap memory
            if len(self._recent_query_ts) > 20000:
                cutoff = now - settings.chat_duplicate_cooldown_seconds
                stale = [k for k, ts in self._recent_query_ts.items() if ts < cutoff]
                for key in stale:
                    self._recent_query_ts.pop(key, None)

    @staticmethod
    def _prune(queue: deque[float], cutoff: float) -> None:
        while queue and queue[0] < cutoff:
            queue.popleft()


_limiter = SlidingWindowLimiter()
_chat_semaphore = asyncio.Semaphore(max(1, settings.chat_max_concurrency))


def normalize_query(query: str) -> str:
    return _WS_RE.sub(" ", query.strip().lower())


def get_client_fingerprint(request: Request) -> str:
    forwarded = request.headers.get("x-forwarded-for", "")
    if forwarded:
        ip = forwarded.split(",")[0].strip()
    elif request.client:
        ip = request.client.host
    else:
        ip = "unknown"
    ua = request.headers.get("user-agent", "")[:160]
    raw = f"{ip}|{ua}"
    return hashlib.sha256(raw.encode("utf-8")).hexdigest()[:24]


def enforce_request_guardrails(request: Request, query: str) -> tuple[str, str]:
    client_id = get_client_fingerprint(request)
    normalized_query = normalize_query(query)
    _limiter.enforce(client_id, normalized_query)
    return client_id, normalized_query


@asynccontextmanager
async def acquire_chat_slot():
    timeout = max(0.1, settings.chat_queue_timeout_seconds)
    try:
        await asyncio.wait_for(_chat_semaphore.acquire(), timeout=timeout)
    except TimeoutError as exc:
        raise GuardrailViolation(
            "Server is busy. Please retry shortly.",
            retry_after_seconds=2,
        ) from exc
    try:
        yield
    finally:
        _chat_semaphore.release()
