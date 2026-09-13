import asyncio
from unittest.mock import AsyncMock, patch, MagicMock
import httpx
from services.affiliate_price_service import (
    build_instant_gaming_url,
    build_g2a_url,
    fetch_cheapshark_deal,
    get_affiliate_prices,
    _affiliate_price_cache,
)


def test_build_affiliate_urls():
    ig_url = build_instant_gaming_url("Hollow Knight: Silksong")
    assert "https://www.instant-gaming.com/es/busqueda/?q=" in ig_url
    assert "igr=game-recommended" in ig_url
    assert "Silksong" in ig_url

    g2a_url = build_g2a_url("Portal 2")
    assert "https://www.g2a.com/search?query=" in g2a_url
    assert "gname=gamerecommended" in g2a_url
    assert "Portal+2" in g2a_url


def test_fetch_cheapshark_deal_success():
    mock_client = MagicMock()
    mock_response = MagicMock()
    mock_response.status_code = 200
    mock_response.json.return_value = {"cheapestPrice": {"price": "4.99"}}
    mock_client.get = AsyncMock(return_value=mock_response)

    with patch("services.affiliate_price_service.get_http_client", AsyncMock(return_value=mock_client)):
        deal = asyncio.run(fetch_cheapshark_deal(400, "Portal"))
        assert deal["cheapshark_best"] == "4.99 $"


def test_fetch_cheapshark_deal_error_graceful():
    mock_client = MagicMock()
    mock_client.get = AsyncMock(side_effect=httpx.RequestError("Timeout"))

    with patch("services.affiliate_price_service.get_http_client", AsyncMock(return_value=mock_client)):
        deal = asyncio.run(fetch_cheapshark_deal(99999, "Nonexistent"))
        assert deal["cheapshark_best"] is None
        assert deal["discount_pct"] == 0


def test_get_affiliate_prices_caching():
    _affiliate_price_cache.clear()

    with patch("services.affiliate_price_service.fetch_cheapshark_deal", AsyncMock(return_value={"cheapshark_best": "7.50 $", "discount_pct": 0})) as mock_fetch:
        result1 = asyncio.run(get_affiliate_prices("Hades", 1145360))
        assert result1["game_name"] == "Hades"
        assert result1["stores"]["instant_gaming"]["best_deal"] == "7.50 $"
        assert mock_fetch.call_count == 1

        # Second call should hit TTLCache and not invoke fetch_cheapshark_deal again
        result2 = asyncio.run(get_affiliate_prices("Hades", 1145360))
        assert result2 == result1
        assert mock_fetch.call_count == 1


def test_affiliate_prices_endpoint(client):
    response = client.get("/api/games/400/affiliate-prices?name=Portal")
    assert response.status_code == 200
    data = response.json()
    assert data["app_id"] == 400
    assert data["game_name"] == "Portal"
    assert "instant_gaming" in data["stores"]
    assert "g2a" in data["stores"]
    assert data["stores"]["instant_gaming"]["available"] is True
