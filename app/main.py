from __future__ import annotations

import logging
from contextlib import asynccontextmanager
from pathlib import Path

from fastapi import FastAPI, Request, status
from fastapi.exceptions import RequestValidationError
from fastapi.middleware.cors import CORSMiddleware
from fastapi.responses import FileResponse, JSONResponse, Response
from fastapi.staticfiles import StaticFiles
from prometheus_client import CONTENT_TYPE_LATEST, generate_latest
from sqlalchemy.exc import IntegrityError

from app.config import get_settings
from app.core.logging import configure_logging
from app.core.redis import close_redis, get_redis
from app.middleware.metrics import MetricsMiddleware
from app.middleware.rate_limit import RateLimitMiddleware
from app.middleware.request_context import RequestContextMiddleware
from app.routers import accounts, admin, auth, transactions, webhooks
from app.services.exceptions import LedgerError
from app.services.outbox import OutboxSweeper
from app.services.webhooks import outbox_handler

settings = get_settings()
configure_logging(settings.log_level)
logger = logging.getLogger(__name__)

sweeper = OutboxSweeper(handler=outbox_handler)


@asynccontextmanager
async def lifespan(app: FastAPI):
    redis = get_redis()
    try:
        await redis.ping()
        logger.info("redis connection ok")
    except Exception as exc:
        logger.warning("redis ping failed at startup: %s", exc)
    sweeper.start()
    try:
        yield
    finally:
        await sweeper.stop()
        await close_redis()


app = FastAPI(
    title=settings.app_name,
    description=(
        "Production-grade double-entry payment ledger. Every money movement "
        "produces per-currency-balanced ledger rows in a single atomic DB "
        "transaction; write endpoints require an Idempotency-Key header so "
        "retries never double-charge."
    ),
    version="1.1.0",
    lifespan=lifespan,
    docs_url="/docs",
    redoc_url="/redoc",
    openapi_url="/openapi.json",
)

app.add_middleware(
    CORSMiddleware,
    allow_origins=settings.cors_origins_list(),
    allow_credentials=True,
    allow_methods=["*"],
    allow_headers=["*"],
    expose_headers=["X-Request-ID", "X-RateLimit-Limit", "X-RateLimit-Remaining"],
)
app.add_middleware(
    RateLimitMiddleware,
    redis=get_redis(),
    limit_per_minute=settings.rate_limit_per_minute,
    overrides={
        "/auth/login": settings.rate_limit_auth_per_minute,
        "/auth/register": settings.rate_limit_auth_per_minute,
        "/auth/refresh": settings.rate_limit_auth_per_minute,
    },
)
app.add_middleware(MetricsMiddleware)
app.add_middleware(RequestContextMiddleware)


@app.exception_handler(LedgerError)
async def ledger_error_handler(request: Request, exc: LedgerError) -> JSONResponse:
    return JSONResponse(
        status_code=exc.status_code,
        content={"error": exc.code, "detail": exc.detail or exc.code},
    )


@app.exception_handler(IntegrityError)
async def integrity_error_handler(request: Request, exc: IntegrityError) -> JSONResponse:
    logger.warning("integrity error: %s", exc)
    return JSONResponse(
        status_code=status.HTTP_409_CONFLICT,
        content={"error": "integrity_error", "detail": "a database constraint was violated"},
    )


@app.exception_handler(RequestValidationError)
async def validation_error_handler(
    request: Request, exc: RequestValidationError
) -> JSONResponse:
    return JSONResponse(
        status_code=status.HTTP_422_UNPROCESSABLE_ENTITY,
        content={"error": "validation_error", "detail": exc.errors()},
    )


@app.get("/health", tags=["meta"])
async def health() -> dict[str, str]:
    return {"status": "ok"}


@app.get("/metrics", tags=["meta"], include_in_schema=False)
async def metrics() -> Response:
    return Response(content=generate_latest(), media_type=CONTENT_TYPE_LATEST)


app.include_router(auth.router)
app.include_router(accounts.router)
app.include_router(transactions.router)
app.include_router(webhooks.router)
app.include_router(admin.router)


# ---------------------------------------------------------------------------
# Dashboard (single-page web UI). Served from app/static/index.html.
# Mounted AFTER the routers so nothing collides.
# ---------------------------------------------------------------------------
_STATIC_DIR = Path(__file__).parent / "static"
if _STATIC_DIR.exists():
    app.mount("/static", StaticFiles(directory=str(_STATIC_DIR)), name="static")

    @app.get("/", include_in_schema=False)
    async def dashboard() -> FileResponse:
        return FileResponse(_STATIC_DIR / "index.html")

    @app.get("/favicon.ico", include_in_schema=False)
    async def favicon() -> Response:
        return Response(status_code=204)
