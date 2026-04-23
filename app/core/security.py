"""Password hashing + JWT encode/decode.

Uses PyJWT (python-jose is unmaintained). Access tokens are short-lived and
stateless; refresh tokens are long-lived and bound to a Redis record so they
can be rotated and revoked — the access token JWT alone is never enough to
re-authenticate indefinitely."""
from __future__ import annotations

import secrets
from datetime import datetime, timedelta, timezone
from typing import Any

import jwt
from passlib.context import CryptContext

from app.config import get_settings

settings = get_settings()

pwd_context = CryptContext(schemes=["bcrypt"], deprecated="auto")

TOKEN_TYPE_ACCESS = "access"
TOKEN_TYPE_REFRESH = "refresh"


def hash_password(password: str) -> str:
    return pwd_context.hash(password)


def verify_password(plain: str, hashed: str) -> bool:
    return pwd_context.verify(plain, hashed)


def _encode(payload: dict[str, Any]) -> str:
    return jwt.encode(payload, settings.jwt_secret, algorithm=settings.jwt_algorithm)


def _decode(token: str) -> dict[str, Any]:
    try:
        return jwt.decode(
            token, settings.jwt_secret, algorithms=[settings.jwt_algorithm]
        )
    except jwt.PyJWTError as exc:
        raise ValueError("invalid_token") from exc


def create_access_token(subject: str, extra_claims: dict[str, Any] | None = None) -> str:
    now = datetime.now(tz=timezone.utc)
    payload: dict[str, Any] = {
        "sub": subject,
        "iat": int(now.timestamp()),
        "exp": int((now + timedelta(minutes=settings.jwt_access_ttl_minutes)).timestamp()),
        "type": TOKEN_TYPE_ACCESS,
    }
    if extra_claims:
        payload.update(extra_claims)
    return _encode(payload)


def create_refresh_token(subject: str, jti: str) -> tuple[str, int]:
    """Returns (token, ttl_seconds). The `jti` claim is the handle stored in
    Redis — rotating the refresh token replaces this key, so old refresh
    tokens stop validating even if leaked."""
    now = datetime.now(tz=timezone.utc)
    ttl = timedelta(days=settings.jwt_refresh_ttl_days)
    payload = {
        "sub": subject,
        "iat": int(now.timestamp()),
        "exp": int((now + ttl).timestamp()),
        "type": TOKEN_TYPE_REFRESH,
        "jti": jti,
    }
    return _encode(payload), int(ttl.total_seconds())


def decode_access_token(token: str) -> dict[str, Any]:
    payload = _decode(token)
    if payload.get("type") != TOKEN_TYPE_ACCESS:
        raise ValueError("wrong_token_type")
    return payload


def decode_refresh_token(token: str) -> dict[str, Any]:
    payload = _decode(token)
    if payload.get("type") != TOKEN_TYPE_REFRESH:
        raise ValueError("wrong_token_type")
    return payload


def new_jti() -> str:
    return secrets.token_urlsafe(24)
