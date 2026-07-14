from fastapi.testclient import TestClient
from app.main import app

client = TestClient(app)


def test_root():
    response = client.get("/")
    assert response.status_code == 200
    assert response.json()["name"] == "AIM Data API"
    assert response.json()["status"] == "running"


def test_health_check():
    response = client.get("/api/health")
    assert response.status_code == 200
    assert response.json()["status"] == "ok"


def test_liveness_stays_healthy_when_mcp_is_disabled():
    from app.services.connectivity_state import get_connectivity_state

    state = get_connectivity_state()
    previous = state._enabled
    try:
        state._enabled = False
        response = client.get("/api/health")
        assert response.status_code == 200
        assert response.json()["status"] == "ok"
    finally:
        state._enabled = previous
