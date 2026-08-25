from typing import Annotated

import httpx
import pytest
from fastapi import Depends, FastAPI

from app.api.routes.health import require_operator
from app.core.config import settings


@pytest.mark.asyncio
async def test_operator_endpoint_is_open_when_no_token_is_configured(monkeypatch):
    monkeypatch.setattr(settings, "operator_api_token", "")
    response = await _request()
    assert response.status_code == 200


@pytest.mark.asyncio
async def test_operator_endpoint_requires_matching_token_when_configured(monkeypatch):
    monkeypatch.setattr(settings, "operator_api_token", "operator-secret")
    assert (await _request()).status_code == 404
    assert (await _request("wrong")).status_code == 404
    assert (await _request("operator-secret")).status_code == 200


async def _request(token: str | None = None) -> httpx.Response:
    app = FastAPI()

    @app.get("/operator")
    async def operator(_: Annotated[None, Depends(require_operator)]):
        return {"ok": True}

    headers = {"X-Operator-Token": token} if token else {}
    async with httpx.AsyncClient(
        transport=httpx.ASGITransport(app=app), base_url="http://test"
    ) as client:
        return await client.get("/operator", headers=headers)
