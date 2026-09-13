"""Tests verifying that fastapi-security-headers is active and securing all endpoints."""


def test_root_endpoint_has_security_headers(client):
    """Verify that root endpoint returns all required OWASP security headers."""
    response = client.get("/")
    assert response.status_code == 200

    headers = response.headers
    assert headers.get("x-content-type-options") == "nosniff"
    assert headers.get("x-frame-options") == "DENY"
    assert headers.get("x-xss-protection") == "0"
    assert "max-age=" in headers.get("strict-transport-security", "")
    assert headers.get("referrer-policy") == "strict-origin-when-cross-origin"
    assert "geolocation=()" in headers.get("permissions-policy", "")
    assert "default-src 'self'" in headers.get("content-security-policy", "")
    assert headers.get("cross-origin-opener-policy") == "same-origin"
    assert headers.get("cross-origin-resource-policy") == "same-origin"


def test_api_health_has_security_headers(client):
    """Verify that API routes inherit security headers seamlessly."""
    response = client.get("/api/health")
    assert response.status_code == 200
    assert response.headers.get("x-content-type-options") == "nosniff"
    assert response.headers.get("x-frame-options") == "DENY"


def test_swagger_friendly_csp_allows_cdn(client):
    """Verify that CSP policy permits Swagger UI assets."""
    response = client.get("/")
    csp = response.headers.get("content-security-policy", "")
    assert "https://cdn.jsdelivr.net" in csp
    assert "https://fastapi.tiangolo.com" in csp
