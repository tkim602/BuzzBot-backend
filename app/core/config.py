"""Application configuration loaded from environment."""

from __future__ import annotations

from dotenv import load_dotenv
from pydantic import Field
from pydantic_settings import BaseSettings

load_dotenv()


def sync_database_url(database_url: str) -> str:
    replacements = {
        "postgresql+asyncpg://": "postgresql+psycopg://",
        "postgresql+psycopg_async://": "postgresql+psycopg://",
    }
    for async_scheme, sync_scheme in replacements.items():
        if database_url.startswith(async_scheme):
            return database_url.replace(async_scheme, sync_scheme, 1)
    if database_url.startswith(("postgresql+psycopg://", "postgresql://")):
        return database_url
    raise ValueError("DATABASE_URL must use PostgreSQL")


class Settings(BaseSettings):
    # Web
    cors_origins: str = (
        "http://localhost:3000,http://127.0.0.1:3000,http://localhost:3100,http://127.0.0.1:3100"
    )

    @property
    def cors_origin_list(self) -> list[str]:
        origins = list(
            dict.fromkeys(
                origin.strip().rstrip("/")
                for origin in self.cors_origins.split(",")
                if origin.strip()
            )
        )
        if "*" in origins:
            raise ValueError("CORS origins cannot contain a wildcard when credentials are enabled")
        return origins

    # Database
    database_url: str

    @property
    def database_url_sync(self) -> str:
        return sync_database_url(self.database_url)

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
    rag_skip_fts_for_exact_schedule: bool = True
    rag_skip_fts_when_vector_sufficient: bool = False
    rag_enable_embedding_cache: bool = True
    rag_embedding_cache_ttl_seconds: int = 3600
    rag_embedding_cache_max_size: int = 3000
    rag_fts_top_k: int = 5
    rag_enable_reranking: bool = True
    rag_rerank_model: str = "cross-encoder/ms-marco-MiniLM-L-6-v2"

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
    active_term_code: str = Field(default="202608", pattern=r"^\d{6}$")

    # Usage tracking
    usage_limit: float = 3.0  # Maximum API cost in USD

    # LangGraph
    langgraph_checkpoint_enabled: bool = True

    model_config = {"env_file": ".env", "extra": "ignore"}


settings = Settings()
