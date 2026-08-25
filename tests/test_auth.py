from collections.abc import Mapping
from typing import Annotated

import httpx
import pytest
from fastapi import Depends, FastAPI

from app.core.auth import (
    RequestIdentity,
    get_request_identity,
    get_token_verifier,
    identity_from_claims,
)


def test_verified_gatech_identity_is_derived_from_verified_claims():
    identity = identity_from_claims(
        {
            "uid": "student-1",
            "email": "Student@GaTech.edu",
            "email_verified": True,
        }
    )

    assert identity == RequestIdentity(
        uid="student-1",
        email="Student@GaTech.edu",
        email_verified=True,
        gatech_eligible=True,
    )


@pytest.mark.parametrize(
    "claims",
    [
        {"uid": "student-1", "email": "student@gatech.edu", "email_verified": False},
        {"uid": "student-1", "email": "student@example.com", "email_verified": True},
    ],
)
def test_gatech_eligibility_requires_verified_gatech_email(claims):
    assert identity_from_claims(claims).gatech_eligible is False


@pytest.mark.asyncio
async def test_missing_authorization_is_anonymous():
    app = _identity_app(lambda _token: {})
    async with httpx.AsyncClient(
        transport=httpx.ASGITransport(app=app), base_url="http://test"
    ) as client:
        response = await client.get("/identity")

    assert response.status_code == 200
    assert response.json() == {"uid": None, "gatech_eligible": False}


@pytest.mark.asyncio
async def test_verified_bearer_token_resolves_identity():
    async def verifier(token: str) -> Mapping[str, object]:
        assert token == "valid-token"
        return {"uid": "student-1", "email": "student@gatech.edu", "email_verified": True}

    app = _identity_app(verifier)
    async with httpx.AsyncClient(
        transport=httpx.ASGITransport(app=app), base_url="http://test"
    ) as client:
        response = await client.get("/identity", headers={"Authorization": "Bearer valid-token"})

    assert response.status_code == 200
    assert response.json() == {"uid": "student-1", "gatech_eligible": True}


@pytest.mark.asyncio
@pytest.mark.parametrize("authorization", ["Basic value", "Bearer", "Bearer bad-token"])
async def test_malformed_or_invalid_bearer_token_returns_401(authorization):
    async def verifier(_token: str) -> Mapping[str, object]:
        raise ValueError("invalid token")

    app = _identity_app(verifier)
    async with httpx.AsyncClient(
        transport=httpx.ASGITransport(app=app), base_url="http://test"
    ) as client:
        response = await client.get("/identity", headers={"Authorization": authorization})

    assert response.status_code == 401
    assert response.headers["www-authenticate"] == "Bearer"


def _identity_app(verifier) -> FastAPI:
    app = FastAPI()
    app.dependency_overrides[get_token_verifier] = lambda: verifier

    @app.get("/identity")
    async def identity(value: Annotated[RequestIdentity, Depends(get_request_identity)]):
        return {"uid": value.uid, "gatech_eligible": value.gatech_eligible}

    return app
