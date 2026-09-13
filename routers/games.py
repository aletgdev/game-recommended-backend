import asyncio
import html
from fastapi import APIRouter, Query, HTTPException, Response
from starlette.concurrency import run_in_threadpool
from services.steam import buscar_juegos_steam, obtener_reseñas_steam, obtener_detalles_juego
from services.sentiment import sentiment_service
from services.cache import cache_service
from services.affiliate_price_service import get_affiliate_prices
from services.curation import curate_diverse_reviews
from services.groq_summary_service import generate_game_summary_groq

router = APIRouter()


def _sanitize_display_name(name: str, max_length: int = 50) -> str:
    """
    Limpia un nombre de usuario para exponerlo de forma segura.
    Elimina caracteres de control, limita la longitud y strip de whitespace.
    """
    cleaned = "".join(c for c in name if c.isprintable() or c.isspace())
    cleaned = " ".join(cleaned.split())
    if len(cleaned) > max_length:
        cleaned = cleaned[:max_length].rstrip()
    return cleaned if cleaned else "Usuario de Steam"


def generar_badge_svg(recommendation_level: str, positives_pct: float) -> str:
    color_map = {
        "Extremadamente Recomendado": "#10b981",
        "Recomendado": "#3b82f6",
        "Mixto": "#f59e0b",
        "No Recomendado": "#f43f5e",
        "Sin reseñas": "#6b7280"
    }
    color = color_map.get(recommendation_level, "#6b7280")
    
    verdict_short = recommendation_level
    if verdict_short == "Extremadamente Recomendado":
        verdict_short = "Ext. Recomendado"
        
    text_content = f"{verdict_short} ({positives_pct:.0f}%)"
    
    # Ancho del badge dinámico
    text_width = len(text_content) * 7 + 12
    label_width = 75
    total_width = label_width + text_width
    
    label_x = (label_width / 2) * 10
    text_x = (label_width + text_width / 2) * 10
    
    # Escapar adecuadamente los caracteres XML
    safe_text_content = html.escape(text_content)
    
    return f"""<svg xmlns="http://www.w3.org/2000/svg" width="{total_width}" height="20" viewBox="0 0 {total_width * 10} 200">
  <linearGradient id="g" x2="0" y2="100%">
    <stop offset="0" stop-color="#bbb" stop-opacity=".1"/>
    <stop offset="1" stop-opacity=".1"/>
  </linearGradient>
  <clipPath id="r">
    <rect width="{total_width * 10}" height="200" rx="30" fill="#fff"/>
  </clipPath>
  <g clip-path="url(#r)">
    <rect width="{label_width * 10}" height="200" fill="#555"/>
    <rect x="{label_width * 10}" width="{text_width * 10}" height="200" fill="{color}"/>
    <rect width="{total_width * 10}" height="200" fill="url(#g)"/>
  </g>
  <g fill="#fff" text-anchor="middle" font-family="Verdana,Geneva,DejaVu Sans,sans-serif" text-rendering="geometricPrecision" font-size="110">
    <text x="{label_x}" y="140" fill="#010101" fill-opacity=".3">Steam IA</text>
    <text x="{label_x}" y="130">Steam IA</text>
    <text x="{text_x}" y="140" fill="#010101" fill-opacity=".3">{safe_text_content}</text>
    <text x="{text_x}" y="130">{safe_text_content}</text>
  </g>
</svg>"""


@router.get("/api/search")
async def buscar_juegos(term: str = Query(..., min_length=1, description="Nombre o término de búsqueda del juego")):
    """
    Busca juegos en la API pública de Steam utilizando un término.
    Respuestas cacheadas 5 minutos por término (case-insensitive).
    """
    if not term.strip():
        raise HTTPException(status_code=400, detail="El término de búsqueda no puede estar vacío.")

    cached = cache_service.get_search(term)
    if cached:
        return cached

    result = await buscar_juegos_steam(term)
    cache_service.set_search(term, result)
    return result


@router.get("/api/analyze/{app_id}")
async def analizar_reseñas(
    app_id: int,
    limit: int = Query(30, ge=5, le=50, description="Cantidad máxima de reseñas a analizar (máximo 50)")
):
    """
    Obtiene las reseñas más recientes en español de un juego en Steam,
    las preprocesa y predice el sentimiento utilizando el modelo.
    Respuestas cacheadas 30 minutos por app_id.
    """
    if not sentiment_service.model_loaded:
        raise HTTPException(
            status_code=503,
            detail="El modelo de análisis de sentimiento no está disponible en el servidor."
        )

    cached = cache_service.get_analyze(app_id)
    if cached and cached.get("groq_summary") is not None:
        # Si la respuesta cacheada tiene suficientes reseñas o se pide menos/igual, retornarla recortada
        cached_copy = dict(cached)
        if "reviews_classified" in cached_copy:
            cached_copy["reviews_classified"] = cached_copy["reviews_classified"][:limit]
            cached_copy["total_reviews_analyzed"] = len(cached_copy["reviews_classified"])
        return cached_copy

    # 1. Obtener piscina amplia de reseñas y detalles del juego en paralelo desde Steam
    fetch_limit = max(limit * 3, 60)
    reviews_raw, game_details = await asyncio.gather(
        obtener_reseñas_steam(app_id, fetch_limit),
        obtener_detalles_juego(app_id),
    )

    if not reviews_raw:
        empty_res = {
            "app_id": app_id,
            "total_reviews_analyzed": 0,
            "recommendation_level": "Sin reseñas",
            "sentiment_stats": {
                "positives_pct": 0,
                "negatives_pct": 0
            },
            "steam_voted_up_pct": 0,
            "reviews_classified": [],
            "game_details": game_details
        }
        cache_service.set_analyze(app_id, empty_res)
        return empty_res

    # 2. Curación Inteligente de Reseñas por IA (Filtro anti-spam + Diversidad Semántica MMR)
    reviews_curated = curate_diverse_reviews(reviews_raw, sentiment_service.vectorizador, limit)

    # 3. Limpieza y preparación de reseñas curadas
    textos_crudos = [r.get("review", "") for r in reviews_curated]

    # 4. Predicción del sentimiento en lote sin bloquear el Event Loop de FastAPI
    try:
        predicciones = await run_in_threadpool(sentiment_service.predecir_sentimientos, textos_crudos)
    except Exception as e:
        raise HTTPException(
            status_code=500,
            detail=f"Error al clasificar el sentimiento de las reseñas: {str(e)}"
        )

    # 4. Agrupación y cálculo de estadísticas
    reseñas_clasificadas = []
    positivas_ia = 0
    positivas_steam = 0

    for idx, r in enumerate(reviews_curated):
        sentimiento_ia = predicciones[idx]
        voted_up_steam = 1 if r.get("voted_up") else 0

        if sentimiento_ia == 1:
            positivas_ia += 1
        if voted_up_steam == 1:
            positivas_steam += 1

        reseñas_clasificadas.append({
            "recommendation_id": r.get("recommendationid"),
            "author": _sanitize_display_name(r.get("author", {}).get("personaname", "")),
            "playtime_forever": r.get("author", {}).get("playtime_forever", 0),
            "review_text": r.get("review", "").strip(),
            "sentiment_predicted": "Positivo" if sentimiento_ia == 1 else "Negativo",
            "voted_up_steam": bool(voted_up_steam),
            "timestamp_created": r.get("timestamp_created"),
            "timestamp_updated": r.get("timestamp_updated"),
        })

    total_reviews = len(reviews_curated)
    pos_ia_pct = round((positivas_ia / total_reviews) * 100, 2)
    neg_ia_pct = round(100.0 - pos_ia_pct, 2)
    pos_steam_pct = round((positivas_steam / total_reviews) * 100, 2)

    if pos_ia_pct >= 80:
        nivel_recomendacion = "Extremadamente Recomendado"
    elif pos_ia_pct >= 60:
        nivel_recomendacion = "Recomendado"
    elif pos_ia_pct >= 40:
        nivel_recomendacion = "Mixto"
    else:
        nivel_recomendacion = "No Recomendado"

    groq_summary = await generate_game_summary_groq(
        game_name=game_details.get("name") if game_details else f"AppID {app_id}",
        app_id=app_id,
        recommendation_level=nivel_recomendacion,
        reviews_texts=textos_crudos,
        game_details=game_details,
    )

    result = {
        "app_id": app_id,
        "total_reviews_analyzed": total_reviews,
        "recommendation_level": nivel_recomendacion,
        "sentiment_stats": {
            "positives_pct": pos_ia_pct,
            "negatives_pct": neg_ia_pct
        },
        "steam_voted_up_pct": pos_steam_pct,
        "reviews_classified": reseñas_clasificadas,
        "game_details": game_details,
        "groq_summary": groq_summary,
    }

    cache_service.set_analyze(app_id, result)
    return result


@router.get("/api/games/{app_id}/badge")
async def obtener_badge(app_id: int):
    """
    Devuelve un SVG embebible con el veredicto y el porcentaje positivo de reseñas.
    Utiliza el caché si está disponible, o realiza el análisis al vuelo de 30 reseñas.
    """
    cached = cache_service.get_analyze(app_id)
    if not cached:
        try:
            cached = await analizar_reseñas(app_id, limit=30)
        except Exception:
            cached = {
                "recommendation_level": "Sin reseñas",
                "sentiment_stats": {"positives_pct": 0.0}
            }
            
    verdict = cached.get("recommendation_level", "Sin reseñas")
    pos_pct = cached.get("sentiment_stats", {}).get("positives_pct", 0.0)
    
    svg_content = generar_badge_svg(verdict, pos_pct)
    
    return Response(
        content=svg_content,
        media_type="image/svg+xml",
        headers={
            "Cache-Control": "max-age=1800, public"
        }
    )


@router.get("/api/games/{app_id}/affiliate-prices")
async def obtener_precios_afiliados(app_id: int, name: str = Query(..., min_length=1)):
    """
    Devuelve los enlaces y precios aproximados de afiliado para Instant Gaming y G2A.
    """
    return await get_affiliate_prices(game_name=name, app_id=app_id)