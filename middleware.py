import os
import time
import logging
import uuid
from cachetools import TTLCache
from fastapi import Request
from fastapi.responses import JSONResponse
from starlette.middleware.base import BaseHTTPMiddleware

logger = logging.getLogger(__name__)


class RateLimitMiddleware(BaseHTTPMiddleware):
    def __init__(self, app, max_requests: int = 30, window_seconds: int = 60, max_ips: int = 10_000, rag_max_requests: int = 10):
        super().__init__(app)
        self.max_requests = max_requests
        self.window_seconds = window_seconds
        self.rag_max_requests = rag_max_requests
        self.trust_proxy = os.getenv("TRUST_PROXY", "false").lower() in ("true", "1")
        # TTLCache para limitar IPs rastreadas y su TTL
        self.request_history: TTLCache[str, list[float]] = TTLCache(maxsize=max_ips, ttl=window_seconds)
        self.rag_request_history: TTLCache[str, list[float]] = TTLCache(maxsize=max_ips, ttl=window_seconds)

    def _extract_client_ip(self, request: Request) -> str:
        """Extrae la IP real del cliente. Solo confía en X-Forwarded-For si TRUST_PROXY es True."""
        if self.trust_proxy:
            x_forwarded_for = request.headers.get("x-forwarded-for")
            if x_forwarded_for:
                return x_forwarded_for.split(",")[0].strip()
        return request.client.host if request.client else "unknown"

    async def dispatch(self, request: Request, call_next):
        # Omitir el límite para la raíz
        if request.url.path == "/":
            return await call_next(request)

        client_ip = self._extract_client_ip(request)
        now = time.time()

        # ── Rate Limit dedicado para /api/rag ──
        if request.url.path.startswith("/api/rag"):
            rag_timestamps = self.rag_request_history.get(client_ip) or []
            rag_timestamps = [t for t in rag_timestamps if now - t < self.window_seconds]
            if len(rag_timestamps) >= self.rag_max_requests:
                retry_after = int(self.window_seconds - (now - rag_timestamps[0]))
                logger.warning("Rate limit RAG bloqueado: IP=%s", client_ip)
                return JSONResponse(
                    status_code=429,
                    content={
                        "detail": "Demasiadas peticiones de recomendación IA. Por favor, espera un momento.",
                        "retry_after_seconds": max(retry_after, 1),
                    },
                    headers={"Retry-After": str(max(retry_after, 1))},
                )
            rag_timestamps.append(now)
            self.rag_request_history[client_ip] = rag_timestamps

        # ── Rate Limit general ──
        timestamps = self.request_history.get(client_ip) or []
        timestamps = [t for t in timestamps if now - t < self.window_seconds]

        if len(timestamps) >= self.max_requests:
            retry_after = int(self.window_seconds - (now - timestamps[0]))
            logger.warning("Rate limit general bloqueado: IP=%s", client_ip)
            return JSONResponse(
                status_code=429,
                content={
                    "detail": "Demasiadas peticiones. Por favor, intentalo de nuevo mas tarde.",
                    "retry_after_seconds": max(retry_after, 1),
                },
                headers={"Retry-After": str(max(retry_after, 1))},
            )

        timestamps.append(now)
        self.request_history[client_ip] = timestamps
        return await call_next(request)


class RequestLoggingMiddleware(BaseHTTPMiddleware):
    """
    Middleware ligero que registra cada request a nivel DEBUG con el formato:
    method path status duration_ms
    y genera/propaga un header X-Request-ID.
    """

    async def dispatch(self, request: Request, call_next):
        request_id = request.headers.get("X-Request-ID") or uuid.uuid4().hex[:12]
        request.state.request_id = request_id

        start = time.time()
        response = await call_next(request)
        duration_ms = round((time.time() - start) * 1000, 2)

        logger.debug(
            "%s %s %s %sms",
            request.method,
            request.url.path,
            response.status_code,
            duration_ms,
        )

        response.headers["X-Request-ID"] = request_id
        return response
