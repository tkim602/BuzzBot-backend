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
    rag_similarity_threshold: float = 0.2
    rag_regenerate_on_ungrounded: bool = False
    rag_skip_fts_for_exact_schedule: bool = True
    rag_skip_fts_when_vector_sufficient: bool = False
    rag_force_fts_for_date_sensitive: bool = True
    rag_enable_embedding_cache: bool = True
    rag_embedding_cache_ttl_seconds: int = 3600
    rag_embedding_cache_max_size: int = 3000
    rag_response_cache_ttl_seconds: int = 180
    rag_response_cache_max_size: int = 1000
    rag_fts_top_k: int = 5
    rag_enable_query_rewrite: bool = True
    rag_query_rewrite_mode: str = "auto"  # rule | llm | auto
    rag_user_timezone: str = "America/New_York"

    # Live fetch
    enable_live_fetch: bool = True
    live_fetch_timeout: int = 10
    live_fetch_max_urls: int = 3
    live_fetch_max_chunks: int = 8

    # Abuse safeguards (additional to usage_limit)
    chat_rate_limit_per_minute: int = 24
    chat_rate_limit_per_hour: int = 240
    chat_rate_limit_per_day: int = 400
    chat_min_interval_seconds: float = 0.8
    chat_duplicate_cooldown_seconds: int = 20
    chat_max_concurrency: int = 12
    chat_queue_timeout_seconds: float = 5.0

    # Ingestion
    ingest_max_urls_per_source: int = 200
    ingest_concurrency: int = 5

    # Usage tracking
    usage_limit: float = 20.0  # Maximum API cost in USD

    model_config = {"env_file": ".env", "extra": "ignore"}


settings = Settings()
