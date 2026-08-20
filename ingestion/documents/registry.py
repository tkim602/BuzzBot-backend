from __future__ import annotations

from dataclasses import dataclass
from pathlib import Path
from urllib.parse import urlparse

import yaml  # type: ignore[import-untyped]


def _url_within_root(url: str, root: str) -> bool:
    candidate = urlparse(url)
    allowed = urlparse(root)
    root_path = allowed.path.rstrip("/") + "/"
    candidate_path = candidate.path.rstrip("/") + "/"
    return (
        candidate.scheme == allowed.scheme == "https"
        and candidate.netloc == allowed.netloc
        and candidate_path.startswith(root_path)
    )


@dataclass(frozen=True)
class DocumentSource:
    name: str
    source_type: str
    authority: str
    allowed_roots: tuple[str, ...]
    seed_urls: tuple[str, ...]
    max_urls: int
    vertical: str = "general"
    adapter: str = ""
    allowed_path_prefixes: tuple[str, ...] = ()
    content_types: tuple[str, ...] = ("text/html",)
    freshness_class: str = "medium"
    profiles: tuple[str, ...] = ()

    def __post_init__(self) -> None:
        if not self.name or not self.source_type or not self.authority:
            raise ValueError("name, source_type, and authority are required")
        if not self.allowed_roots or not self.seed_urls:
            raise ValueError("allowed roots and seed URLs are required")
        if not 1 <= self.max_urls <= 500:
            raise ValueError("max_urls must be between 1 and 500")
        if any(not root.startswith("https://") for root in self.allowed_roots):
            raise ValueError("allowed roots must use HTTPS")
        if any(
            not any(_url_within_root(seed, root) for root in self.allowed_roots)
            for seed in self.seed_urls
        ):
            raise ValueError("every seed URL must be within an allowed root")
        if self.allowed_path_prefixes and any(
            not path.startswith("/") for path in self.allowed_path_prefixes
        ):
            raise ValueError("allowed path prefixes must start with /")
        if not self.content_types or not set(self.content_types) <= {
            "text/html",
            "application/pdf",
        }:
            raise ValueError("unsupported document content type")
        if self.freshness_class not in {"low", "medium", "high"}:
            raise ValueError("freshness class must be low, medium, or high")

    def allows(self, url: str) -> bool:
        return any(_url_within_root(url, root) for root in self.allowed_roots)


def load_document_sources(path: Path | None = None) -> tuple[DocumentSource, ...]:
    registry_path = path or Path(__file__).resolve().parents[1] / "sources.yaml"
    with registry_path.open(encoding="utf-8") as file:
        data = yaml.safe_load(file) or {}
    return tuple(
        DocumentSource(
            name=item["name"],
            source_type=item["source_type"],
            authority=item["authority"],
            allowed_roots=tuple(item["allowed_roots"]),
            seed_urls=tuple(item["seed_urls"]),
            max_urls=int(item["max_urls"]),
            vertical=item.get("vertical", "general"),
            adapter=item.get("adapter", item["authority"]),
            allowed_path_prefixes=tuple(item.get("allowed_path_prefixes", ())),
            content_types=tuple(item.get("content_types", ("text/html",))),
            freshness_class=item.get("freshness_class", "medium"),
            profiles=tuple(item.get("profiles", ())),
        )
        for item in data.get("documents", ())
    )
