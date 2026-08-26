import pytest
from pydantic import ValidationError

from app.core.config import Settings, sync_database_url


def test_sync_database_url_is_derived_from_the_single_async_url():
    configured = "postgresql+asyncpg://buzzbot:secret@db:5432/buzzbot"

    assert sync_database_url(configured) == ("postgresql+psycopg://buzzbot:secret@db:5432/buzzbot")
    settings = Settings(_env_file=None, database_url=configured)
    assert settings.database_url_sync.endswith("/buzzbot")


def test_psycopg_url_is_already_valid_for_sync_consumers():
    configured = "postgresql+psycopg://buzzbot:secret@db:5432/buzzbot"

    assert sync_database_url(configured) == configured


def test_non_postgresql_database_url_is_rejected():
    try:
        sync_database_url("sqlite:///buzzbot.db")
    except ValueError as exc:
        assert "PostgreSQL" in str(exc)
    else:
        raise AssertionError("non-PostgreSQL URL was accepted")


def test_database_url_is_an_explicit_environment_contract(monkeypatch):
    monkeypatch.delenv("DATABASE_URL", raising=False)

    with pytest.raises(ValidationError, match="database_url"):
        Settings(_env_file=None)


def test_active_term_must_be_a_six_digit_banner_code():
    configured = Settings(
        _env_file=None,
        database_url="postgresql+asyncpg://buzzbot:secret@db:5432/buzzbot",
        active_term_code="202608",
    )

    assert configured.active_term_code == "202608"
    with pytest.raises(ValidationError, match="active_term_code"):
        Settings(
            _env_file=None,
            database_url="postgresql+asyncpg://buzzbot:secret@db:5432/buzzbot",
            active_term_code="Fall 2026",
        )


def test_settings_repr_never_exposes_provider_secrets():
    configured = Settings(
        _env_file=None,
        database_url="postgresql+asyncpg://test:test@localhost/test",
        openai_api_key="openai-secret-value",
        anthropic_api_key="anthropic-secret-value",
    )

    rendered = repr(configured)
    assert "openai-secret-value" not in rendered
    assert "anthropic-secret-value" not in rendered
