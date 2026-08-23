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
