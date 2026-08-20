import uuid

from ingestion.documents import cli
from ingestion.documents.registry import DocumentSource
from ingestion.orchestration import RunSummary


def _source() -> DocumentSource:
    return DocumentSource(
        "gt-registrar",
        "official_policy",
        "registrar",
        ("https://registrar.gatech.edu/registration",),
        ("https://registrar.gatech.edu/registration",),
        50,
    )


def test_sync_many_cli_keeps_verification_limit_explicit(monkeypatch, capsys):
    run_id = uuid.uuid4()
    captured: dict[str, object] = {}

    async def fake_sync(source, session_factory, embed_fn, **kwargs):
        captured.update(kwargs)
        return RunSummary(
            run_id,
            "official-documents",
            {"source": source.name, "verification_limit": 2},
            "COMPLETED",
            2,
            2,
            0,
            0,
            True,
            None,
            (source.seed_urls[0], "https://registrar.gatech.edu/registration/holds"),
        )

    monkeypatch.setattr(cli, "load_document_sources", lambda: (_source(),))
    monkeypatch.setattr("ingestion.documents.sync_source.sync_document_source_urls", fake_sync)
    monkeypatch.setattr("ingestion.index.get_embedding_function", lambda: object())

    exit_code = cli.main(["sync-many", "--source", "gt-registrar", "--verification-limit", "2"])

    assert exit_code == 0
    assert captured["verification_limit"] == 2
    assert '"planned":2' in capsys.readouterr().out


def test_sync_many_cli_resume_never_sets_verification_limit(monkeypatch):
    run_id = uuid.uuid4()
    captured: dict[str, object] = {}

    async def fake_sync(source, session_factory, embed_fn, **kwargs):
        captured.update(kwargs)
        return RunSummary(
            run_id,
            "official-documents",
            {"source": source.name},
            "COMPLETED",
            1,
            1,
            0,
            0,
            True,
            None,
            (source.seed_urls[0],),
        )

    monkeypatch.setattr(cli, "load_document_sources", lambda: (_source(),))
    monkeypatch.setattr("ingestion.documents.sync_source.sync_document_source_urls", fake_sync)
    monkeypatch.setattr("ingestion.index.get_embedding_function", lambda: object())

    exit_code = cli.main(
        [
            "sync-many",
            "--source",
            "gt-registrar",
            "--resume",
            "--run-id",
            str(run_id),
        ]
    )

    assert exit_code == 0
    assert captured["resume"] is True
    assert captured["run_id"] == run_id
    assert captured["verification_limit"] is None
