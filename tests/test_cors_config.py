import pytest
from fastapi.testclient import TestClient

from app.core.config import Settings
from app.main import app


def test_cors_origins_are_trimmed_and_normalized():
    configured = Settings(
        _env_file=None,
        cors_origins=" https://web.example.edu/ , http://localhost:3000 ,,",
    )

    assert configured.cors_origin_list == [
        "https://web.example.edu",
        "http://localhost:3000",
    ]


def test_cors_origins_reject_wildcards_with_credentials():
    configured = Settings(_env_file=None, cors_origins="*")

    with pytest.raises(ValueError, match="wildcard"):
        _ = configured.cors_origin_list


def test_local_frontend_preflight_is_allowed():
    response = TestClient(app).options(
        "/chat",
        headers={
            "Origin": "http://localhost:3000",
            "Access-Control-Request-Method": "POST",
        },
    )

    assert response.status_code == 200
    assert response.headers["access-control-allow-origin"] == "http://localhost:3000"
