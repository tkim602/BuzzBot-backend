from app.core.config import Settings, sync_database_url


def test_sync_database_url_is_derived_from_the_single_async_url():
    configured = "postgresql+asyncpg://buzzbot:secret@db:5432/buzzbot_v2"

    assert sync_database_url(configured) == (
        "postgresql+psycopg://buzzbot:secret@db:5432/buzzbot_v2"
    )
    settings = Settings(_env_file=None, database_url=configured)
    assert settings.database_url_sync.endswith("/buzzbot_v2")


def test_psycopg_url_is_already_valid_for_sync_consumers():
    configured = "postgresql+psycopg://buzzbot:secret@db:5432/buzzbot_v2"

    assert sync_database_url(configured) == configured


def test_non_postgresql_database_url_is_rejected():
    try:
        sync_database_url("sqlite:///buzzbot.db")
    except ValueError as exc:
        assert "PostgreSQL" in str(exc)
    else:
        raise AssertionError("non-PostgreSQL URL was accepted")
