"""Assigns a request id to every request, threads it through context for
structured logging, and echoes it back as `X-Request-ID`."""
from __future__ import annotations

import uuid

from fastapi import Request
from starlette.middleware.base import BaseHTTPMiddleware, RequestResponseEndpoint
from starlette.responses import Response

from app.core.logging import (
    method_var,
    path_var,
    request_id_var,
    user_id_var,
)


class RequestContextMiddleware(BaseHTTPMiddleware):
    async def dispatch(
        self, request: Request, call_next: RequestResponseEndpoint
    ) -> Response:
        rid = request.headers.get("x-request-id") or uuid.uuid4().hex
        r_tok = request_id_var.set(rid)
        p_tok = path_var.set(request.url.path)
        m_tok = method_var.set(request.method)
        u_tok = user_id_var.set(None)
        try:
            response = await call_next(request)
        finally:
            request_id_var.reset(r_tok)
            path_var.reset(p_tok)
            method_var.reset(m_tok)
            user_id_var.reset(u_tok)
        response.headers["X-Request-ID"] = rid
        return response
