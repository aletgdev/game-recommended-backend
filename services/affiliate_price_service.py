import logging
import httpx
import urllib.parse
from cachetools import TTLCache
from services.steam import get_http_client

logger = logging.getLogger(__name__)

# Cache en memoria de precios de afiliados por AppID / Nombre (12 horas TTL)
_affiliate_price_cache: TTLCache[str, dict] = TTLCache(maxsize=2000, ttl=43200)

AFFILIATE_CODES = {
    "instant_gaming": "igr=game-recommended",
    "g2a": "gname=gamerecommended",
}


def build_instant_gaming_url(game_name: str) -> str:
    """Genera URL directa de búsqueda en Instant Gaming con código de afiliado."""
    encoded_name = urllib.parse.quote_plus(game_name)
    return f"https://www.instant-gaming.com/es/busqueda/?q={encoded_name}&{AFFILIATE_CODES['instant_gaming']}"


def build_g2a_url(game_name: str) -> str:
    """Genera URL directa de búsqueda en G2A con código de afiliado."""
    encoded_name = urllib.parse.quote_plus(game_name)
    return f"https://www.g2a.com/search?query={encoded_name}&{AFFILIATE_CODES['g2a']}"


async def fetch_cheapshark_deal(app_id: int, game_name: str) -> dict:
    """
    Consulta la API pública de CheapShark usando steamAppID o nombre.
    Retorna precios de ofertas activas y porcentajes de descuento.
    """
    headers = {"User-Agent": "GameRecommendedApp/2.0"}
    deals_data = {"cheapshark_best": None, "discount_pct": 0}

    try:
        client = await get_http_client()
        url = f"https://www.cheapshark.com/api/1.0/games?steamAppID={app_id}"
        response = await client.get(url, headers=headers, timeout=4.0)

        if response.status_code == 200:
            data = response.json()
            if isinstance(data, dict) and data.get("cheapestPrice"):
                cheapest = data["cheapestPrice"]
                cheapest_val = float(cheapest.get("price", 0))
                if cheapest_val > 0:
                    deals_data["cheapshark_best"] = f"{cheapest_val:.2f} $"

        if not deals_data["cheapshark_best"] and game_name:
            search_url = f"https://www.cheapshark.com/api/1.0/games?title={urllib.parse.quote(game_name)}"
            res = await client.get(search_url, headers=headers, timeout=4.0)
            if res.status_code == 200:
                arr = res.json()
                if isinstance(arr, list) and len(arr) > 0:
                    cheapest_val = float(arr[0].get("cheapest", 0))
                    if cheapest_val > 0:
                        deals_data["cheapshark_best"] = f"{cheapest_val:.2f} $"
    except Exception as e:
        logger.debug(f"CheapShark lookup skipped for app_id={app_id}: {e}")

    return deals_data


async def get_affiliate_prices(game_name: str, app_id: int = 0) -> dict:
    """
    Obtiene los precios aproximados y enlaces de afiliados para Instant Gaming y G2A.
    Utiliza caché TTLCache de 12 horas para garantizar respuestas ultrarrápidas.
    """
    cache_key = f"{app_id}_{game_name.strip().lower()}"
    if cache_key in _affiliate_price_cache:
        return _affiliate_price_cache[cache_key]

    instant_url = build_instant_gaming_url(game_name)
    g2a_url = build_g2a_url(game_name)

    deals = await fetch_cheapshark_deal(app_id, game_name)

    res_data = {
        "game_name": game_name,
        "app_id": app_id,
        "stores": {
            "instant_gaming": {
                "name": "Instant Gaming",
                "url": instant_url,
                "badge": "Ver Ofertas IgR",
                "available": True,
                "best_deal": deals.get("cheapshark_best"),
            },
            "g2a": {
                "name": "G2A",
                "url": g2a_url,
                "badge": "Ver Claves G2A",
                "available": True,
            },
        },
    }

    _affiliate_price_cache[cache_key] = res_data
    return res_data
