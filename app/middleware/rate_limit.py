"""Redis fixed-window rate limiter with per-path limit overrides.

Global default comes from `RATE_LIMIT_PER_MINUTE`. Auth endpoints get a lower
limit (defaults to `RATE_LIMIT_AUTH_PER_MINUTE`) because they're the juiciest
target for credential-stuffing. Adding another override is a one-liner in the
`overrides` dict passed to the middleware at construction."""
from __future__ import annotations

import time

import jwt
from fastapi import Request, status
from fastapi.responses import JSONResponse
from redis.asyncio import Redis
from starlette.middleware.base import BaseHTTPMiddleware, RequestResponseEndpoint
from starlette.responses import Response

from app.config import get_settings
from app.core.metrics import rate_limit_rejects_total


def _identify_client(request: Request) -> str:
    settings = get_settings()
    auth = request.headers.get("authorization", "")
    if auth.lower().startswith("bearer "):
        token = auth.split(" ", 1)[1].strip()
        try:
            payload = jwt.decode(
                token, settings.jwt_secret, algorithms=[settings.jwt_algorithm]
            )
            sub = payload.get("sub")
            if sub:
                return f"user:{sub}"
        except jwt.PyJWTError:
            pass

    client = request.client
    ip = client.host if client else "unknown"
    xff = request.headers.get("x-forwarded-for")
    if xff:
        ip = xff.split(",")[0].strip()
    return f"ip:{ip}"


class RateLimitMiddleware(BaseHTTPMiddleware):
    EXEMPT_PATHS: tuple[str, ...] = (
        "/docs",
        "/redoc",
        "/openapi.json",
        "/health",
        "/metrics",
    )

    def __init__(
        self,
        app,
        redis: Redis,
        limit_per_minute: int,
        overrides: dict[str, int] | None = None,
    ) -> None:
        super().__init__(app)
        self.redis = redis
        self.default_limit = limit_per_minute
        self.overrides = overrides or {}

    def _limit_for(self, path: str) -> int:
        for prefix, limit in self.overrides.items():
            if path == prefix or path.startswith(prefix):
                return limit
        return self.default_limit

    async def dispatch(
        self, request: Request, call_next: RequestResponseEndpoint
    ) -> Response:
        path = request.url.path
        if any(path == p or path.startswith(p + "/") for p in self.EXEMPT_PATHS):
            return await call_next(request)

        limit = self._limit_for(path)
        identity = _identify_client(request)
        window = int(time.time() // 60)
        # bucket key includes the limit so a path move between limit tiers
        # doesn't accidentally share a bucket with its old tier.
        key = f"ratelimit:{identity}:{limit}:{window}"

        try:
            current = await self.redis.incr(key)
            if current == 1:
                await self.redis.expire(key, 65)
        except Exception:
            # Redis hiccup — fail open rather than break the API.
            return await call_next(request)

        if current > limit:
            rate_limit_rejects_total.inc()
            retry_after = 60 - (int(time.time()) % 60)
            return JSONResponse(
                status_code=status.HTTP_429_TOO_MANY_REQUESTS,
                content={
                    "error": "rate_limited",
                    "detail": f"rate limit of {limit}/minute exceeded",
                },
                headers={
                    "Retry-After": str(retry_after),
                    "X-RateLimit-Limit": str(limit),
                    "X-RateLimit-Remaining": "0",
                },
            )

        response = await call_next(request)
        response.headers["X-RateLimit-Limit"] = str(limit)
        response.headers["X-RateLimit-Remaining"] = str(max(0, limit - current))
        return response
