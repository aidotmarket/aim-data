from unittest.mock import AsyncMock
import httpx
import pytest
from fastapi import FastAPI, HTTPException
from fastapi.testclient import TestClient
from app.services import connected_login as connected, aim_market_oauth as flow
from app.routers import auth


@pytest.fixture
def upstream(monkeypatch):
    calls = []
    responses = []
    real_client = httpx.AsyncClient
    def handle(request):
        calls.append(request)
        response = responses.pop(0)
        if isinstance(response, Exception):
            raise response
        return response
    monkeypatch.setattr(httpx, "AsyncClient", lambda **kwargs: real_client(transport=httpx.MockTransport(handle), **kwargs))
    monkeypatch.setattr(auth, "_handle_ai_market_token", AsyncMock())
    monkeypatch.setattr(flow.settings, "ai_market_url", "https://api.ai.market")
    monkeypatch.setattr(flow.settings, "oauth_enabled", True)
    monkeypatch.setattr(flow.settings, "keystore_passphrase", None)
    return calls, responses


@pytest.mark.asyncio
@pytest.mark.parametrize("identity", [None, {}, {"id": "buyer", "role": "buyer"}, 401])
async def test_identity_failure_and_registration_not_ready(upstream, identity):
    calls, responses = upstream
    responses.append(httpx.Response(401 if identity == 401 else 200, json=identity))
    if isinstance(identity, dict) and identity.get("id"):
        result = await connected.complete_connected_login({"access_token": "a", "refresh_token": "r"}, "oauth")
        assert result["registration_status"] == "not_ready" and result["user"]["role"] == "buyer"
    else:
        with pytest.raises(HTTPException):
            await connected.complete_connected_login({"access_token": "a", "refresh_token": "r"}, "oauth")
        auth._handle_ai_market_token.assert_not_awaited()


@pytest.mark.asyncio
@pytest.mark.parametrize("status,body,expected", [(400, {"error": "invalid_grant"}, 401), (400, {"error_code": "client_disabled"}, 403), (429, {}, 429), (503, {}, 503)])
async def test_oauth_failure_never_uses_password_transport(upstream, status, body, expected):
    calls, responses = upstream
    responses.append(httpx.Response(status, json=body, headers={"Retry-After": "30"}))
    with pytest.raises(HTTPException) as error:
        await connected.refresh_connected_login({"refresh_token": "old", "auth_mode": "oauth"})
    assert error.value.status_code == expected
    assert len(calls) == 1 and calls[0].url.path == "/api/v1/oauth/token"
    assert "cookie" not in calls[0].headers and b"client_id=aim_data_desktop_v1" in calls[0].content
    if status == 429:
        assert error.value.headers["Retry-After"] == "30"


def test_oauth_mode_rotation_and_logout(upstream):
    calls, responses = upstream
    responses.extend([httpx.Response(200, json={"access_token": "new", "refresh_token": "rotated", "token_type": "bearer", "scope": flow.SCOPE}), httpx.Response(200, json={"id": "buyer"})])
    app = FastAPI()
    app.include_router(auth.router, prefix="/api/auth")
    with TestClient(app) as client:
        response = client.post("/api/auth/aim-market-refresh", json={"refresh_token": "old", "auth_mode": "oauth"})
        assert response.status_code == 200 and response.json()["auth_mode"] == "oauth"
        assert response.json()["refresh_token"] == "rotated" and "set-cookie" not in response.headers
        assert client.post("/api/auth/logout").status_code == 200
    assert len(calls) == 2


@pytest.mark.asyncio
@pytest.mark.parametrize("status,body,reason", [
    (200, {"client_id": flow.CLIENT, "protocol_version": 1, "enabled": False}, "client_disabled"),
    (200, {"client_id": flow.CLIENT, "protocol_version": True, "enabled": False}, "backend_unavailable"),
    (200, {"client_id": "other", "protocol_version": 1, "enabled": False}, "backend_unavailable"),
    (200, {"client_id": flow.CLIENT, "protocol_version": 1, "enabled": "false"}, "backend_unavailable"),
    (404, {}, "backend_unsupported"), (503, {}, "backend_unavailable"),
])
async def test_exact_status_contract(upstream, status, body, reason):
    calls, responses = upstream
    responses.append(httpx.Response(status, json=body))
    assert await flow.readiness() == reason
    assert calls[0].url == "https://api.ai.market/api/v1/oauth/clients/aim_data_desktop_v1/status"
    assert "authorization" not in calls[0].headers and "cookie" not in calls[0].headers


@pytest.mark.asyncio
async def test_off_never_contacts_backend(upstream):
    flow.settings.oauth_enabled = False
    assert await flow.readiness() == "local_disabled"
    with pytest.raises(HTTPException) as error:
        await flow.exchange_tokens({"grant_type": "refresh_token", "refresh_token": "old"})
    assert error.value.status_code == 403 and not upstream[0]


@pytest.mark.asyncio
@pytest.mark.parametrize("error,status", [(httpx.ReadTimeout("credential-secret"), 504), (httpx.ConnectError("credential-secret"), 503)])
async def test_network_transport_errors_are_sanitized(upstream, error, status):
    calls, responses = upstream
    responses.append(error)
    with pytest.raises(HTTPException) as exc:
        await connected.refresh_connected_login({"refresh_token": "old", "auth_mode": "oauth"})
    assert exc.value.status_code == status and "credential-secret" not in str(exc.value.detail)
    assert len(calls) == 1


@pytest.mark.asyncio
@pytest.mark.parametrize("cookie", [None, "", "null"])
async def test_password_refresh_missing_cookie_never_completes(upstream, cookie):
    calls, responses = upstream
    responses.append(httpx.Response(200, json={"access_token": "a", "refresh_token": "json-must-not-win"}, headers={} if cookie is None else {"set-cookie": "refresh_token=" + cookie}))
    with pytest.raises(HTTPException) as exc:
        await connected.refresh_connected_login({"refresh_token": "old"})
    assert exc.value.status_code == 502 and len(calls) == 1
    auth._handle_ai_market_token.assert_not_awaited()


@pytest.mark.asyncio
async def test_registration_failure_never_publish_ready(upstream, monkeypatch, tmp_path):
    calls, responses = upstream
    responses.append(httpx.Response(200, json={"id": "buyer", "role": "buyer"}))
    monkeypatch.setattr(auth.settings, "keystore_passphrase", "test-passphrase")
    monkeypatch.setattr(auth.settings, "keystore_path", str(tmp_path / "keys"))
    monkeypatch.setattr("app.services.registration_service.ensure_vz_install_registered", AsyncMock(side_effect=RuntimeError("upstream-secret")))
    result = await connected.complete_connected_login({"access_token": "a", "refresh_token": "r"}, "password")
    assert result["registration_status"] == "not_ready" and "upstream-secret" not in str(result)
