from __future__ import annotations

import re
from dataclasses import dataclass
from urllib.parse import urlencode

import httpx
from lxml import html as lxml_html
from lxml.etree import ParserError

from ingestion.probes.cli import USER_AGENT
from ingestion.probes.core import ProbeBudget, ProbeSession, ProbeStatus
from ingestion.probes.oscar import requires_auth

OSCAR_DISCOVERY_URL = "https://oscar.gatech.edu/bprod/bwckgens.p_proc_term_date"
SUBJECT_RE = re.compile(r"[A-Z][A-Z0-9]{1,7}")


@dataclass(frozen=True)
class DiscoveryResult:
    status: ProbeStatus
    subjects: tuple[str, ...]
    requests_used: int
    reason: str | None = None
    retry_after_seconds: int | None = None


def build_subject_discovery_url(term: str) -> str:
    if not re.fullmatch(r"\d{6}", term):
        raise ValueError("term must be six digits")
    return f"{OSCAR_DISCOVERY_URL}?{urlencode({'p_calling_proc': 'bwckschd.p_disp_dyn_sched', 'p_term': term})}"


def parse_subjects(body: str) -> tuple[str, ...]:
    try:
        tree = lxml_html.fromstring(body)
    except (ParserError, TypeError, ValueError) as exc:
        raise ValueError("subject discovery markup is invalid") from exc
    selects = tree.xpath("//select[@name='sel_subj']")
    if len(selects) != 1:
        raise ValueError("subject discovery select is missing")
    values = [value.strip().upper() for value in selects[0].xpath("./option/@value")]
    candidates = [value for value in values if value and value != "%"]
    if not candidates or any(SUBJECT_RE.fullmatch(value) is None for value in candidates):
        raise ValueError("subject discovery contains invalid subjects")
    return tuple(dict.fromkeys(candidates))


async def discover_subjects(
    term: str,
    transport: httpx.AsyncBaseTransport | None = None,
) -> DiscoveryResult:
    url = build_subject_discovery_url(term)
    budget = ProbeBudget(max_requests=1)
    async with httpx.AsyncClient(
        transport=transport,
        timeout=httpx.Timeout(budget.timeout_seconds),
        follow_redirects=False,
        headers={"User-Agent": USER_AGENT},
    ) as client:
        session = ProbeSession(client, budget)
        response = await session.get(url, retry_transient=False, follow_redirects=False)

    if requires_auth(response) or response.status_code in {401, 403}:
        return DiscoveryResult(
            ProbeStatus.AUTH_REQUIRED, (), session.requests_used, "AUTH_REQUIRED"
        )
    if response.status_code == 429:
        retry_after = (
            int(response.retry_after)
            if response.retry_after is not None and response.retry_after.isdigit()
            else None
        )
        return DiscoveryResult(
            ProbeStatus.RATE_LIMITED,
            (),
            session.requests_used,
            "HTTP_429",
            retry_after,
        )
    if response.status_code != 200:
        return DiscoveryResult(
            ProbeStatus.UNAVAILABLE,
            (),
            session.requests_used,
            response.error or f"HTTP_{response.status_code}",
        )
    try:
        subjects = parse_subjects(response.body)
    except ValueError as exc:
        return DiscoveryResult(
            ProbeStatus.PARSE_FAILED,
            (),
            session.requests_used,
            str(exc),
        )
    return DiscoveryResult(ProbeStatus.READY, subjects, session.requests_used)
