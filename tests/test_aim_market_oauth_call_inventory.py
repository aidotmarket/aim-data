"""Runtime account/link/registration subset. Other §3.2 families remain unverified."""
from unittest.mock import AsyncMock
from uuid import uuid4

import httpx
import pytest
from fastapi import FastAPI
from fastapi.testclient import TestClient
from app.routers import auth
from app.services import connected_login
from app.services.serial_store import get_serial_store


@pytest.mark.asyncio
async def test_runtime_account_and_install_credential_inventory(monkeypatch, tmp_path):
    from app.auth import api_key_auth
    from app.core.database import get_session_context
    from app.models.user import User
    from sqlmodel import select

    identity = str(uuid4())
    account = {"id": identity, "email": identity + "@example.test", "role": "buyer", "status": "active"}
    calls = []
    real_client = httpx.AsyncClient
    def upstream(request):
        calls.append((request.method, str(request.url), request.headers.get("authorization")))
        if request.url.path == "/api/v1/auth/me":
            return httpx.Response(200, json=account)
        assert request.url.path == "/api/v1/vz/register"
        return httpx.Response(201, json={"install_id": "synthetic-install", "install_token": "synthetic-install-token"})
    monkeypatch.setattr(httpx, "AsyncClient", lambda **kwargs: real_client(transport=httpx.MockTransport(upstream), **kwargs))
    monkeypatch.setattr(auth.settings, "ai_market_url", "https://api.ai.market")
    monkeypatch.setattr(auth.settings, "keystore_passphrase", "synthetic-keystore-passphrase")
    monkeypatch.setattr(auth.settings, "keystore_path", str(tmp_path / "keys"))
    store = get_serial_store()
    monkeypatch.setattr(store.state, "vz_install_id", None)
    monkeypatch.setattr(store, "persist_ai_market_session", lambda token, seller: None)
    monkeypatch.setattr(store, "persist_vz_install", lambda install_id, token: None)
    monkeypatch.setattr(store, "save", lambda: None)
    def forbidden(*args, **kwargs):
        pytest.fail("Connected completion attempted to issue a local operator credential")
    monkeypatch.setattr(auth, "_set_jwt_cookie", forbidden)
    monkeypatch.setattr(auth, "_create_api_key_for_user", forbidden)
    result = await connected_login.complete_connected_login({"access_token": "synthetic-account", "refresh_token": "synthetic-refresh"}, "oauth")
    assert result["user"] == account and result["registration_status"] == "registered"
    assert calls == [
        ("GET", "https://api.ai.market/api/v1/auth/me", "Bearer synthetic-account"),
        ("POST", "https://api.ai.market/api/v1/vz/register", "Bearer synthetic-account"),
    ]
    with get_session_context() as db:
        linked = db.exec(select(User).where(User.ai_market_user_id == identity)).one()
        assert linked.pw_hash is None and linked.role == "user" and linked.is_active
    monkeypatch.setenv("VECTORAIZ_AUTH_ENABLED", "true")
    app = FastAPI()
    app.include_router(auth.router, prefix="/api/auth")
    with TestClient(app) as client:
        response = client.get("/api/auth/me", headers={"Authorization": "Bearer synthetic-account"})
        assert response.status_code == 200 and response.json()["user_id"] == identity
        assert response.json()["role"] == "user"
    api_key_auth.api_key_cache.clear()


def test_connected_exception_and_query_logging_suppressed(monkeypatch, caplog):
    import logging
    from app.routers import aim_market_oauth as routes
    from app.services import aim_market_oauth as flow

    async def broken():
        logging.getLogger("app.services.registration_service").warning("secret-account-token")
        raise RuntimeError("secret-provider-code")
    monkeypatch.setattr(flow, "readiness", broken)
    flow.records.clear()
    app = FastAPI()
    app.include_router(routes.router)
    with TestClient(app) as client:
        response = client.get(flow.PATH + "/bootstrap")
    assert response.status_code == 502
    assert "secret" not in response.text and "secret" not in caplog.text
    record = logging.LogRecord("uvicorn.access", 20, "", 0, "GET /api/auth/aim-market/callback?code=secret", (), None)
    assert not flow.SensitiveAuthFilter().filter(record)
    flow.records.clear()
