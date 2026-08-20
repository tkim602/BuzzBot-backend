from __future__ import annotations

from collections.abc import Callable
from urllib.parse import urljoin, urlsplit, urlunsplit

from lxml import html as lxml_html

from ingestion.documents.registry import DocumentSource
from ingestion.normalize import normalize_url


class MaxUrlsExceededError(ValueError):
    pass


def bounded_urls(
    source: DocumentSource,
    body: str,
    accepts_path: Callable[[str], bool],
) -> tuple[str, ...]:
    root = lxml_html.fromstring(body)
    urls: list[str] = []
    for raw_url in (*source.seed_urls, *(root.xpath("//a[@href]/@href"))):
        absolute = urljoin(source.seed_urls[0], str(raw_url))
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
