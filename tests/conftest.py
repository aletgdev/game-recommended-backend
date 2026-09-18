import os
import pytest

# Desactivar optimizaciones oneDNN para evitar conflictos de hilos C++ en runners headless de Linux
os.environ["TF_ENABLE_ONEDNN_OPTS"] = "0"

from fastapi.testclient import TestClient
from app import app
from services import sentiment_service


class MockVectorizer:
    def transform(self, texts):
        return texts


class MockPredictionResult:
    def __init__(self, predictions):
        self.predictions = predictions

    def tolist(self):
        return self.predictions


class MockModel:
    def predict(self, counts):
        predictions = []
        for text in counts:
            if any(w in text for w in ["buen", "excelente", "recomendado", "gusto", "genial", "divertido", "juegazo"]):
                predictions.append(1)
            else:
                predictions.append(0)
        return MockPredictionResult(predictions)


@pytest.fixture(autouse=True)
def setup_mock_model():
    """
    Inyecta el mock sobre la instancia real sin reemplazarla, para que
    todos los módulos que ya la importaron reciban el mismo objeto parcheado.
    """
    orig_vectorizador = sentiment_service.vectorizador
    orig_modelo = sentiment_service.modelo

    sentiment_service.vectorizador = MockVectorizer()
    sentiment_service.modelo = MockModel()

    yield

    sentiment_service.vectorizador = orig_vectorizador
    sentiment_service.modelo = orig_modelo


@pytest.fixture(autouse=True)
def reset_cache():
    """Limpia el caché entre tests para evitar contaminación."""
    from cachetools import TTLCache
    import services.cache as _cache_mod
    _cache_mod._search_cache = TTLCache(maxsize=200, ttl=300)
    _cache_mod._analyze_cache = TTLCache(maxsize=100, ttl=1800)
    yield


@pytest.fixture
def client():
    return TestClient(app, base_url="https://testserver")