"""Optional Firebase bearer-token verification."""

from __future__ import annotations

import asyncio
from collections.abc import Awaitable, Callable, Mapping
from dataclasses import dataclass
from typing import Annotated

from fastapi import Depends, HTTPException, Request, status

from app.core.config import settings

TokenVerifier = Callable[[str], Awaitable[Mapping[str, object]]]


@dataclass(frozen=True)
class RequestIdentity:
    uid: str | None = None
    email: str | None = None
    email_verified: bool = False
    gatech_eligible: bool = False

    @property
    def authenticated(self) -> bool:
        return self.uid is not None


ANONYMOUS_IDENTITY = RequestIdentity()


class AuthUnavailableError(RuntimeError):
    pass


def identity_from_claims(claims: Mapping[str, object]) -> RequestIdentity:
    uid = claims.get("uid") or claims.get("sub")
    if not isinstance(uid, str) or not uid:
        raise ValueError("verified token has no uid")
    email_value = claims.get("email")
    email = email_value if isinstance(email_value, str) else None
    email_verified = claims.get("email_verified") is True
    return RequestIdentity(
        uid=uid,
        email=email,
        email_verified=email_verified,
        gatech_eligible=bool(email_verified and email and email.lower().endswith("@gatech.edu")),
    )


def _verify_with_firebase(token: str) -> Mapping[str, object]:
    try:
        import firebase_admin
        from firebase_admin import auth
    except ImportError as exc:  # pragma: no cover - packaging failure
        raise AuthUnavailableError("Firebase Admin is unavailable") from exc

    try:
        firebase_admin.get_app()
    except ValueError:
        options = (
            {"projectId": settings.firebase_project_id} if settings.firebase_project_id else None
        )
        firebase_admin.initialize_app(options=options)
    return auth.verify_id_token(token, check_revoked=settings.firebase_check_revoked)


async def verify_firebase_token(token: str) -> Mapping[str, object]:
    if not settings.firebase_auth_enabled:
        raise AuthUnavailableError("Firebase authentication is not configured")
    return await asyncio.to_thread(_verify_with_firebase, token)


def get_token_verifier() -> TokenVerifier:
    return verify_firebase_token


async def get_request_identity(
    request: Request,
    verifier: Annotated[TokenVerifier, Depends(get_token_verifier)],
) -> RequestIdentity:
    authorization = request.headers.get("authorization")
    if authorization is None:
        return ANONYMOUS_IDENTITY
    parts = authorization.split()
    if len(parts) != 2 or parts[0].lower() != "bearer" or not parts[1]:
        raise _unauthorized()
    try:
        return identity_from_claims(await verifier(parts[1]))
    except AuthUnavailableError as exc:
        raise HTTPException(
            status_code=status.HTTP_503_SERVICE_UNAVAILABLE, detail=str(exc)
        ) from exc
    except Exception as exc:
        raise _unauthorized() from exc


def _unauthorized() -> HTTPException:
    return HTTPException(
        status_code=status.HTTP_401_UNAUTHORIZED,
        detail="Invalid or expired authentication token.",
        headers={"WWW-Authenticate": "Bearer"},
    )
