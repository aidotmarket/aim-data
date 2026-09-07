import pytest
import httpx
from unittest.mock import AsyncMock
from fastapi import HTTPException
from fastapi.testclient import TestClient

# NOTE: The client fixture will enable auth and import the app,
# so we import other modules inside the tests or fixtures where needed.

VALID_KEY = "aim_valid_123"
INVALID_KEY = "aim_invalid_456"
VALID_RESPONSE_PAYLOAD = {
    "valid": True,
    "user_id": "usr_abc123",
    "key_id": "key_xyz789",
    "scopes": ["read", "write"]
}
VALID_AI_MARKET_USER = {
    "id": "usr_bearer_123",
    "email": "buyer@example.com",
    "role": "buyer",
    "status": "active",
}


class DummyWebSocket:
    def __init__(self, token: str):
        self.query_params = {"token": token}

@pytest.fixture(scope="function")
def auth_client(monkeypatch):
    """
    Provides a TestClient with auth enabled, overriding conftest.py.
    Clears the auth cache for each test for isolation.
    """
    # This must be set BEFORE the application and its modules are imported.
    monkeypatch.setenv("VECTORAIZ_AUTH_ENABLED", "true")

    # Delay import until env var is set
    from fastapi import Depends, APIRouter
    from app.auth.api_key_auth import api_key_cache, AuthenticatedUser, get_current_user
    from app.main import app

    # Patch Alembic upgrade to no-op: conftest.py already created tables via
    # SQLModel.metadata.create_all(). The lifespan's init_db() re-runs Alembic
    # migrations which fail with "table already exists" on the shared test DB.
    # We keep the rest of init_db (legacy migrations) but skip Alembic.
    monkeypatch.setattr("app.core.database._run_alembic_upgrade", lambda: None)

    # Ensure Alembic-only tables exist (not backed by SQLModel metadata)
    from app.services.deduction_queue import deductions_metadata
    from app.core.database import get_engine
    deductions_metadata.create_all(get_engine(), checkfirst=True)

    api_key_cache.clear()

    # Reset shared httpx client so mock is used
    import app.auth.api_key_auth as _auth_mod
    _auth_mod._http_client = None

    test_auth_router = APIRouter()
    @test_auth_router.get("/protected-auth-test")
    async def protected_endpoint(user: AuthenticatedUser = Depends(get_current_user)):
        return {"status": "ok", "user": user.model_dump()}

    app.include_router(test_auth_router, prefix="/api/v1")

    with TestClient(app) as test_client:
        yield test_client


@pytest.fixture
def mock_httpx_client(mocker):
    """
    Mocks the httpx.AsyncClient used for API key validation, handling the
    async context manager (`async with`).
    """
    mock = mocker.patch("app.auth.api_key_auth.httpx.AsyncClient", autospec=True)
    instance = mock.return_value
    instance.__aenter__.return_value = instance
    instance.__aexit__.return_value = None
    return instance


def test_valid_api_key(auth_client: TestClient, mock_httpx_client):
    """
    Scenario: A valid API key is provided.
    Expected: Request succeeds (200 OK) after validating with ai.market.
    """
    mock_response = httpx.Response(200, json=VALID_RESPONSE_PAYLOAD)
    mock_httpx_client.post.return_value = mock_response

    headers = {"X-API-Key": VALID_KEY}
    response = auth_client.get("/api/v1/protected-auth-test", headers=headers)

    assert response.status_code == 200
    assert response.json()["user"]["user_id"] == "usr_abc123"
    mock_httpx_client.post.assert_awaited_once()


def test_invalid_api_key(auth_client: TestClient, mock_httpx_client):
    """
    Scenario: An invalid API key is provided.
    Expected: Request is rejected (401 Unauthorized).
    """
    mock_response = httpx.Response(401, json={"valid": False})
    mock_httpx_client.post.return_value = mock_response

    headers = {"X-API-Key": INVALID_KEY}
    response = auth_client.get("/api/v1/protected-auth-test", headers=headers)

    assert response.status_code == 401
    assert response.json()["detail"] == "Invalid API Key."


def test_missing_api_key_header(auth_client: TestClient, mock_httpx_client):
    """
    Scenario: The X-API-Key header is not included.
    Expected: Request is rejected (401 Unauthorized) without calling ai.market.
    """
    response = auth_client.get("/api/v1/protected-auth-test")

    assert response.status_code == 401
    assert response.json()["detail"] == "Not authenticated. Provide X-API-Key header or vz_session cookie."
    mock_httpx_client.post.assert_not_called()


def test_auth_service_unavailable(auth_client: TestClient, mock_httpx_client):
    """
    Scenario: The ai.market auth service is down or unreachable.
    Expected: Request fails (503 Service Unavailable).
    """
    mock_httpx_client.post.side_effect = httpx.RequestError("Connection failed")

    headers = {"X-API-Key": VALID_KEY}
    response = auth_client.get("/api/v1/protected-auth-test", headers=headers)

    assert response.status_code == 503
    assert "Authentication service is currently unavailable" in response.json()["detail"]


def test_api_key_caching(auth_client: TestClient, mock_httpx_client):
    """
    Scenario: A valid API key is used twice.
    Expected: The ai.market validation endpoint is called only once.
    """
    mock_response = httpx.Response(200, json=VALID_RESPONSE_PAYLOAD)
    mock_httpx_client.post.return_value = mock_response

    headers = {"X-API-Key": VALID_KEY}
    
    # First request - should hit ai.market
    response1 = auth_client.get("/api/v1/protected-auth-test", headers=headers)
    assert response1.status_code == 200

    # Second request - should be served from cache
    response2 = auth_client.get("/api/v1/protected-auth-test", headers=headers)
    assert response2.status_code == 200

    # Assert that the external call was only made once
    mock_httpx_client.post.assert_awaited_once()


@pytest.mark.asyncio
async def test_get_current_user_ws_connected_accepts_ai_market_access_token(monkeypatch):
    from app.auth import api_key_auth

    api_key_auth.api_key_cache.clear()
    monkeypatch.setenv("VECTORAIZ_AUTH_ENABLED", "true")
    mock_fetch = AsyncMock(return_value=VALID_AI_MARKET_USER)
    monkeypatch.setattr(api_key_auth, "_fetch_ai_market_me", mock_fetch)
    monkeypatch.setattr(api_key_auth, "_upsert_ai_market_user", lambda *args, **kwargs: None)

    user = await api_key_auth.get_current_user_ws(DummyWebSocket("jwt.account.access.token"))

    assert user is not None
    assert user.user_id == "usr_bearer_123"
    assert user.key_id == "ai_market_bearer"
    assert user.scopes == ["read", "write"]
    mock_fetch.assert_awaited_once_with("jwt.account.access.token")


@pytest.mark.asyncio
async def test_get_current_user_ws_connected_still_accepts_local_key(monkeypatch):
    from app.auth import api_key_auth

    api_key_auth.api_key_cache.clear()
    monkeypatch.setenv("VECTORAIZ_AUTH_ENABLED", "true")
    expected_user = api_key_auth.AuthenticatedUser(
        user_id="local_user",
        key_id="local_key",
        scopes=["read", "write"],
        valid=True,
    )
    mock_validate = AsyncMock(return_value=expected_user)
    monkeypatch.setattr(api_key_auth, "_validate_local_key", mock_validate)

    user = await api_key_auth.get_current_user_ws(DummyWebSocket("vz_local_secret"))

    assert user == expected_user
    mock_validate.assert_awaited_once_with("vz_local_secret")


@pytest.mark.asyncio
async def test_get_current_user_ws_connected_rejects_invalid_access_token(monkeypatch):
    from app.auth import api_key_auth

    api_key_auth.api_key_cache.clear()
    monkeypatch.setenv("VECTORAIZ_AUTH_ENABLED", "true")
    mock_fetch = AsyncMock(
        side_effect=HTTPException(status_code=401, detail="Invalid or expired ai.market token")
    )
    monkeypatch.setattr(api_key_auth, "_fetch_ai_market_me", mock_fetch)

    user = await api_key_auth.get_current_user_ws(DummyWebSocket("garbage.invalid.token"))

    assert user is None
    mock_fetch.assert_awaited_once_with("garbage.invalid.token")


def test_local_operator_api_key_and_jwt_cookie_unchanged(monkeypatch):
    from uuid import uuid4
    from fastapi import FastAPI, Depends, Request
    from app.routers import auth
    from app.auth.api_key_auth import get_current_user
    from app.middleware.auth import create_jwt_token
    from app.core.database import get_session_context
    from app.models.user import User
    from app.models.local_auth import LocalUser

    monkeypatch.setenv("VECTORAIZ_AUTH_ENABLED", "true")
    monkeypatch.setattr(auth.settings, "oauth_enabled", False)
    identity = str(uuid4())
    with get_session_context() as db:
        db.add(User(id=identity, username=identity, role="admin"))
        db.add(LocalUser(id=identity, username=identity, password_hash="unused", role="admin"))
        db.commit()
    key = auth._create_api_key_for_user(identity, "regression", ["read", "write", "admin"])["full_key"]
    app = FastAPI()
    @app.get("/protected")
    async def protected(request: Request, user=Depends(get_current_user)):
        return {"id": user.user_id, "scopes": user.scopes, "role": request.state.user_role}
    with TestClient(app) as client:
        by_key = client.get("/protected", headers={"X-API-Key": key})
        assert key.startswith("vz_") and by_key.status_code == 200
        client.cookies.set("vz_session", create_jwt_token(identity, "admin"))
        by_cookie = client.get("/protected")
        assert by_cookie.status_code == 200 and by_cookie.json() == by_key.json()
        assert by_cookie.json() == {"id": identity, "scopes": ["read", "write", "admin"], "role": "admin"}
        client.cookies.set("vz_session", "invalid")
        assert client.get("/protected").status_code == 401
        client.cookies.clear()
        assert client.get("/protected", headers={"X-API-Key": key + "wrong"}).status_code == 401
        assert client.get("/protected").status_code == 401
