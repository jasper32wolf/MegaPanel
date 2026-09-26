from __future__ import annotations

import secrets

from app.core.logging import new_correlation_id
from app.core.security import ACCESS_COOKIE_NAME, CSRF_COOKIE_NAME
from starlette.middleware.base import BaseHTTPMiddleware
from starlette.requests import Request
from starlette.responses import JSONResponse, Response


class CorrelationIdMiddleware(BaseHTTPMiddleware):
    async def dispatch(self, request: Request, call_next) -> Response:
        cid = request.headers.get("X-Correlation-ID") or new_correlation_id()
        request.state.correlation_id = cid
        response = await call_next(request)
        response.headers["X-Correlation-ID"] = cid
        return response


class CsrfMiddleware(BaseHTTPMiddleware):
    _safe_methods = {"GET", "HEAD", "OPTIONS"}
    _exempt_paths = {"/api/v1/auth/login"}

    async def dispatch(self, request: Request, call_next) -> Response:
        if (
            request.method in self._safe_methods
            or not request.url.path.startswith("/api/v1/")
            or request.url.path in self._exempt_paths
            or ACCESS_COOKIE_NAME not in request.cookies
        ):
            return await call_next(request)

        cookie_token = request.cookies.get(CSRF_COOKIE_NAME, "")
        header_token = request.headers.get("X-CSRF-Token", "")
        if (
            not cookie_token
            or not header_token
            or not secrets.compare_digest(cookie_token, header_token)
        ):
            return JSONResponse({"detail": "CSRF validation failed"}, status_code=403)
        return await call_next(request)


class SecurityHeadersMiddleware(BaseHTTPMiddleware):
    async def dispatch(self, request: Request, call_next) -> Response:
        response = await call_next(request)
        response.headers.setdefault("X-Content-Type-Options", "nosniff")
        response.headers.setdefault("X-Frame-Options", "DENY")
        response.headers.setdefault("Referrer-Policy", "strict-origin-when-cross-origin")
        response.headers.setdefault(
            "Permissions-Policy",
            "geolocation=(), microphone=(), camera=()",
        )
        response.headers.setdefault(
            "Content-Security-Policy",
            "default-src 'self'; object-src 'none'; base-uri 'self'; frame-ancestors 'none'",
        )
        if request.url.scheme == "https":
            response.headers.setdefault(
                "Strict-Transport-Security", "max-age=31536000; includeSubDomains"
            )
        return response
