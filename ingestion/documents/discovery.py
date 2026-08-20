from __future__ import annotations

from collections.abc import Callable
from pathlib import PurePosixPath
from urllib.parse import urljoin, urlsplit, urlunsplit

from lxml import html as lxml_html

from ingestion.documents.registry import DocumentSource
from ingestion.normalize import normalize_url


class MaxUrlsExceededError(ValueError):
    pass


def _declared_path_allowed(source: DocumentSource, path: str) -> bool:
    normalized = path.rstrip("/") or "/"
    if normalized in {excluded.rstrip("/") or "/" for excluded in source.excluded_paths}:
        return False
    within_prefix = any(
        normalized == prefix.rstrip("/") or normalized.startswith(prefix.rstrip("/") + "/")
        for prefix in source.allowed_path_prefixes
    )
    if not within_prefix:
        return False
    suffix = PurePosixPath(normalized).suffix.lower()
    if suffix == ".pdf":
        return "application/pdf" in source.content_types
    return not suffix or suffix in {".html", ".htm"}


def discover_declared_urls(source: DocumentSource, body: str) -> tuple[str, ...]:
    if not source.allowed_path_prefixes:
        raise ValueError("declared path adapter requires allowed path prefixes")
    return bounded_urls(source, body, lambda path: _declared_path_allowed(source, path))


def bounded_urls(
    source: DocumentSource,
    body: str,
    accepts_path: Callable[[str], bool],
) -> tuple[str, ...]:
    root = lxml_html.fromstring(body)
    urls: list[str] = []
    for raw_url in (*source.seed_urls, *(root.xpath("//a[@href]/@href"))):
        absolute = urljoin(source.seed_urls[0], str(raw_url).strip())
        parsed = urlsplit(absolute)
        canonical = normalize_url(urlunsplit((parsed.scheme, parsed.netloc, parsed.path, "", "")))
        if (
            source.allows(canonical)
            and accepts_path(urlsplit(canonical).path)
            and canonical not in urls
        ):
            urls.append(canonical)
            if len(urls) > source.max_urls:
                raise MaxUrlsExceededError
    return tuple(urls)
