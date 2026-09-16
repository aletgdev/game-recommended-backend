import time
from datetime import datetime, timezone
from fastapi import APIRouter
from services.sentiment import sentiment_service
from services.cache import cache_service

router = APIRouter()

# Momento en que arrancó el servidor (se evalúa al importar el módulo)
_START_TIME = time.time()


def _format_uptime(seconds: float) -> str:
    """Convierte segundos en un string legible: '2d 3h 15m 42s'."""
    seconds = int(seconds)
    days, seconds = divmod(seconds, 86400)
    hours, seconds = divmod(seconds, 3600)
    minutes, seconds = divmod(seconds, 60)

    parts = []
    if days:
        parts.append(f"{days}d")
    if hours:
        parts.append(f"{hours}h")
    if minutes:
        parts.append(f"{minutes}m")
    parts.append(f"{seconds}s")
    return " ".join(parts)


@router.get("/api/health", tags=["Health"])
def health_check():
    """
    Devuelve el estado operacional del servidor:
    - Estado general (healthy / degraded)
    - Disponibilidad del modelo de ML
    - Estadísticas de caché
    - Uptime del proceso
    """
    uptime_seconds = time.time() - _START_TIME
    model_ok = sentiment_service.model_loaded

    return {
        "status": "healthy" if model_ok else "degraded",
        "uptime": _format_uptime(uptime_seconds),
        "uptime_seconds": round(uptime_seconds, 2),
        "model": {
            "loaded": model_ok,
            "path": sentiment_service.model_path,
        },
        "cache": cache_service.stats,
        "timestamp": datetime.now(timezone.utc).isoformat(),
        "version": "1.0.0",
    }