"""Application configuration loaded from environment."""

from __future__ import annotations

import os

from pydantic_settings import BaseSettings


class Settings(BaseSettings):
    # Database
    database_url: str = "postgresql+asyncpg://buzzbot:buzzbot_dev@localhost:5432/buzzbot"
    database_url_sync: str = "postgresql://buzzbot:buzzbot_dev@localhost:5432/buzzbot"

    # LLM
    llm_provider: str = "openai"
    openai_api_key: str = ""
    openai_model: str = "gpt-4o-mini"
    openai_embed_model: str = "text-embedding-3-small"
    anthropic_api_key: str = ""
    anthropic_model: str = "claude-haiku-4-5-20251001"
    ollama_base_url: str = "http://localhost:11434"
    ollama_model: str = "llama3"

    # RAG
    rag_top_k: int = 8
    rag_max_context_tokens: int = 3000
    rag_similarity_threshold: float = 0.3

    # Live fetch
    enable_live_fetch: bool = True
    live_fetch_timeout: int = 10
    live_fetch_max_urls: int = 3

    # Ingestion
    ingest_max_urls_per_source: int = 200
    ingest_concurrency: int = 5

    model_config = {"env_file": ".env", "extra": "ignore"}


settings = Settings()
