from app.core.clients import get_openai_client


def test_openai_client_is_reused(monkeypatch):
    created = []

    def client(*, api_key):
        created.append(api_key)
        return object()

    monkeypatch.setattr("openai.AsyncOpenAI", client)
    get_openai_client.cache_clear()

    first = get_openai_client("test-key")
    second = get_openai_client("test-key")

    assert first is second
    assert created == ["test-key"]
    get_openai_client.cache_clear()


def test_openai_client_is_wrapped_only_when_langsmith_tracing_is_enabled(monkeypatch):
    raw_client = object()
    wrapped_client = object()
    monkeypatch.setenv("LANGSMITH_TRACING", "true")
    monkeypatch.setattr("openai.AsyncOpenAI", lambda *, api_key: raw_client)
    monkeypatch.setattr("langsmith.wrappers.wrap_openai", lambda client: wrapped_client)
    get_openai_client.cache_clear()

    assert get_openai_client("test-key") is wrapped_client

    get_openai_client.cache_clear()
    monkeypatch.setenv("LANGSMITH_TRACING", "false")
    assert get_openai_client("test-key") is raw_client
