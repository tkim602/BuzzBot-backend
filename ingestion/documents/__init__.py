"""Controlled ingestion for authoritative Georgia Tech documents."""

from ingestion.documents.registry import DocumentSource, load_document_sources

__all__ = ["DocumentSource", "load_document_sources"]
