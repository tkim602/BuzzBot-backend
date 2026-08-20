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


def test_db_coverage_audit_uses_shared_database_setting(monkeypatch):
    from eval import db_coverage_audit

    captured = []

    def fake_create_engine(url):
        captured.append(url)
        return object()

    monkeypatch.setattr(db_coverage_audit, "create_engine", fake_create_engine)
    monkeypatch.setattr(
        db_coverage_audit.settings,
        "database_url",
        "postgresql+asyncpg://buzzbot:secret@db:5432/buzzbot_v2",
    )

    db_coverage_audit._engine()

    assert captured == ["postgresql+psycopg://buzzbot:secret@db:5432/buzzbot_v2"]
