from urllib.parse import parse_qs, urlparse
from unittest.mock import AsyncMock

import httpx
import pytest
from fastapi import FastAPI
from fastapi.testclient import TestClient
from app.routers import aim_market_oauth as routes
from app.services import aim_market_oauth as flow

REAL_READINESS = flow.readiness


@pytest.fixture
def client(monkeypatch):
    flow.records.clear()
    monkeypatch.setattr(flow.settings, "oauth_enabled", True)
    monkeypatch.setattr(flow.settings, "ai_market_url", "https://api.ai.market")
    monkeypatch.setattr(flow, "readiness", AsyncMock(return_value=None))
    app = FastAPI()
    app.include_router(routes.router)
    with TestClient(app, base_url=flow.origin(), follow_redirects=False) as client:
        yield client
    flow.records.clear()


def start(client):
    nonce = client.get(flow.PATH + "/bootstrap").json()["csrf_nonce"]
    response = client.post(flow.PATH + "/start", json={"csrf_nonce": nonce}, headers={"Origin": flow.origin()})
    assert response.status_code == 200
    return parse_qs(urlparse(response.json()["authorization_url"]).query)


def complete(client):
    nonce = client.get(flow.PATH + "/bootstrap").json()["csrf_nonce"]
    return client.post(flow.PATH + "/complete", json={"csrf_nonce": nonce}, headers={"Origin": flow.origin()})


def test_start_callback_complete_atomic(client, monkeypatch):
    exchange = AsyncMock(return_value={"access_token": "access", "refresh_token": "refresh"})
    monkeypatch.setattr(flow, "exchange_tokens", exchange)
    monkeypatch.setattr(routes, "complete_connected_login", AsyncMock(return_value={"auth_mode": "oauth"}))
    query = start(client)
    assert query["redirect_uri"] == [flow.origin() + flow.PATH + "/callback"]
    assert query["code_challenge_method"] == ["S256"]
    callback = flow.PATH + "/callback?code=one-use&state=" + query["state"][0]
    response = client.get(callback)
    assert response.status_code == 303 and response.headers["location"] == "/login/complete"
    assert client.get(callback).status_code == 410
    assert exchange.await_count == 1
    assert complete(client).json() == {"auth_mode": "oauth"}
    assert complete(client).status_code == 403
    assert all("verifier" not in record for record in flow.records.values())


@pytest.mark.parametrize("fault", ["state", "binding", "extra", "duplicate", "nonce", "origin"])
def test_state_cookie_nonce_cross_install_matrix(client, monkeypatch, fault):
    exchange = AsyncMock()
    monkeypatch.setattr(flow, "exchange_tokens", exchange)
    if fault in ("nonce", "origin"):
        nonce = client.get(flow.PATH + "/bootstrap").json()["csrf_nonce"]
        response = client.post(flow.PATH + "/start", json={"csrf_nonce": "wrong" if fault == "nonce" else nonce}, headers={"Origin": "https://attacker.test" if fault == "origin" else flow.origin()})
        assert response.status_code == 403
    else:
        state = start(client)["state"][0]
        if fault == "binding":
            client.cookies.clear()
        response = client.get(flow.PATH + "/callback?code=secret&state=" + ("forged" if fault == "state" else state) + ("&extra=1" if fault == "extra" else "&state=twice" if fault == "duplicate" else ""))
        assert response.status_code == 400
    exchange.assert_not_awaited()


def test_timeout_never_replays_exchange(client, monkeypatch):
    exchange = AsyncMock(side_effect=routes.failure(504, "upstream_timeout"))
    monkeypatch.setattr(flow, "exchange_tokens", exchange)
    query = start(client)
    url = flow.PATH + "/callback?code=secret&state=" + query["state"][0]
    assert client.get(url).status_code == 303
    assert client.get(url).status_code == 410
    assert complete(client).status_code == 504
    assert client.get(url).status_code == 400
    assert exchange.await_count == 1


@pytest.mark.parametrize("reason", ["local_disabled", "client_disabled", "backend_unsupported", "backend_unavailable"])
def test_disabled_and_old_backend_fallback(client, monkeypatch, reason):
    monkeypatch.setattr(flow, "readiness", AsyncMock(return_value=reason))
    bootstrap = client.get(flow.PATH + "/bootstrap").json()
    assert bootstrap["enabled"] is False and bootstrap["reason"] == reason
    result = client.post(flow.PATH + "/start", json={"csrf_nonce": bootstrap["csrf_nonce"]}, headers={"Origin": flow.origin()})
    assert result.status_code in (409, 503) and "authorization_url" not in result.json()


def test_denial_and_nonce_replay(client, monkeypatch):
    exchange = AsyncMock()
    monkeypatch.setattr(flow, "exchange_tokens", exchange)
    query = start(client)
    nonce_record = next(record for record in flow.records.values() if "hits" in record and "nonce" not in record)
    assert "verifier" not in nonce_record
    response = client.get(flow.PATH + "/callback", params={"error": "access_denied", "state": query["state"][0]})
    assert response.status_code == 303
    assert complete(client).json() == {"error_code": "access_denied"}
    exchange.assert_not_awaited()


def test_new_start_invalidates_old_browser_attempt(client, monkeypatch):
    exchange = AsyncMock()
    monkeypatch.setattr(flow, "exchange_tokens", exchange)
    first = start(client)
    second = start(client)
    assert first["state"] != second["state"]
    response = client.get(flow.PATH + "/callback", params={"code": "unused", "state": first["state"][0]})
    assert response.status_code == 400
    exchange.assert_not_awaited()


def test_bootstrap_cookie_and_origin_no_host_trust(client):
    response = client.get(flow.PATH + "/bootstrap", headers={"Host": "attacker.test", "X-Forwarded-Host": "attacker.test"})
    assert response.json()["loopback_origin"] == flow.origin()
    cookie = response.headers["set-cookie"]
    assert all(part in cookie for part in ("HttpOnly", "Max-Age=600", "SameSite=lax", "Path=/api/auth/aim-market"))
    assert "Domain=" not in cookie
    assert response.headers["cache-control"] == "no-store"


def test_body_duplicate_nonce_and_rate_bound(client):
    nonce = client.get(flow.PATH + "/bootstrap").json()["csrf_nonce"]
    response = client.post(flow.PATH + "/start", content='{"csrf_nonce":"' + nonce + '","csrf_nonce":"' + nonce + '"}', headers={"Origin": flow.origin(), "Content-Type": "application/json"})
    assert response.status_code == 403
    for _ in range(9):
        assert client.get(flow.PATH + "/bootstrap").status_code == 200
    assert client.get(flow.PATH + "/bootstrap").status_code == 429


def test_concurrent_callback_claims_once(client, monkeypatch):
    import threading
    from concurrent.futures import ThreadPoolExecutor
    entered, release = threading.Event(), threading.Event()
    async def exchange(_data):
        import asyncio
        entered.set()
        await asyncio.to_thread(release.wait, 2)
        return {"access_token": "a", "refresh_token": "r"}
    monkeypatch.setattr(flow, "exchange_tokens", exchange)
    monkeypatch.setattr(routes, "complete_connected_login", AsyncMock(return_value={"auth_mode": "oauth"}))
    state = start(client)["state"][0]
    url = flow.PATH + "/callback?code=one&state=" + state
    with ThreadPoolExecutor(max_workers=2) as pool:
        first = pool.submit(client.get, url)
        assert entered.wait(2)
        try:
            assert client.get(url).status_code == 410
        finally:
            release.set()
        assert first.result().status_code == 303
    assert complete(client).status_code == 200


def test_expired_attempt_and_completion(client, monkeypatch):
    state = start(client)["state"][0]
    key = flow.digest(client.cookies[routes.cookie_name("binding")])
    flow.records[key]["expires"] = 0
    exchange = AsyncMock()
    monkeypatch.setattr(flow, "exchange_tokens", exchange)
    assert client.get(flow.PATH + "/callback", params={"code": "unused", "state": state}).status_code == 410
    exchange.assert_not_awaited()
    assert flow.records[key]["status"] == "expired"
    assert "verifier" not in flow.records[key]
    flow.allocate(key, {"status": "complete", "body": {"access_token": "secret"}, "http_status": 200}, ttl=-1)
    assert complete(client).status_code == 410 and key not in flow.records


def test_disabled_completed_transaction_cannot_sign_in(client, monkeypatch):
    start(client)
    key = flow.digest(client.cookies[routes.cookie_name("binding")])
    flow.allocate(key, {"status": "complete", "body": {"access_token": "secret"}, "http_status": 200}, ttl=60)
    monkeypatch.setattr(flow.settings, "oauth_enabled", False)
    result = complete(client)
    assert result.status_code == 409 and result.json() == {"error_code": "client_disabled"}
    assert key not in flow.records and "secret" not in result.text


def test_https_cookie_is_secure(client):
    response = client.get("https://127.0.0.1:8080" + flow.PATH + "/bootstrap")
    assert "Secure" in response.headers["set-cookie"]


@pytest.mark.parametrize("field", ["issuer", "uri", "client", "scope", "success"])
def test_stored_transaction_contract_cannot_change(client, monkeypatch, field):
    state = start(client)["state"][0]
    key = flow.digest(client.cookies[routes.cookie_name("binding")])
    flow.records[key][field] = "attacker-value"
    exchange = AsyncMock()
    monkeypatch.setattr(flow, "exchange_tokens", exchange)
    response = client.get(flow.PATH + "/callback", params={"code": "unused", "state": state})
    assert response.status_code == 400
    exchange.assert_not_awaited()


@pytest.mark.parametrize("binding", [None, "unknown-binding"])
def test_complete_unknown_binding_is_403(client, binding):
    if binding:
        client.cookies.set(routes.cookie_name("binding"), binding)
    response = complete(client)
    assert response.status_code == 403 and response.json() == {"error_code": "binding_failed"}
    assert "Max-Age=0" in response.headers["set-cookie"]


def test_localhost_start_requires_numeric_loopback(client):
    nonce = client.get(flow.PATH + "/bootstrap").json()["csrf_nonce"]
    response = client.post(flow.PATH + "/start", json={"csrf_nonce": nonce},
                           headers={"Origin": "http://localhost:8080"})
    assert response.status_code == 400 and response.json() == {"error_code": "loopback_origin_required"}
    assert not any("status" in record for record in flow.records.values())


@pytest.mark.parametrize("status,detail,expected,code", [
    (400, {"error_code": "arbitrary-secret"}, 400, "access_denied"),
    (401, {"error_code": "identity_failed"}, 400, "access_denied"),
    (403, "policy-secret", 400, "access_denied"),
    (403, {"error_code": "client_disabled"}, 409, "client_disabled"),
    (429, {"error_code": "secret"}, 503, "upstream_unavailable"),
    (502, "secret", 502, "upstream_invalid_response"),
    (503, "secret", 503, "upstream_unavailable"),
    (504, "secret", 504, "upstream_timeout"),
])
def test_callback_bound_error_parity(client, monkeypatch, status, detail, expected, code):
    from fastapi import HTTPException
    exchange = AsyncMock(return_value={"access_token": "a", "refresh_token": "r"})
    monkeypatch.setattr(flow, "exchange_tokens", exchange)
    monkeypatch.setattr(routes, "complete_connected_login", AsyncMock(side_effect=HTTPException(status, detail)))
    state = start(client)["state"][0]
    url = flow.PATH + "/callback?code=secret&state=" + state
    response = client.get(url)
    assert response.status_code == 303 and response.headers["location"] == "/login/complete"
    assert client.get(url).status_code == 410
    response = complete(client)
    assert response.status_code == expected and response.json() == {"error_code": code}
    assert "Max-Age=0" in response.headers["set-cookie"]
    assert exchange.await_count == 1


def test_forged_binding_and_expired_terminal_clear(client, monkeypatch):
    state = start(client)["state"][0]
    binding = client.cookies[routes.cookie_name("binding")]
    client.cookies.clear()
    client.cookies.set(routes.cookie_name("binding"), "forged")
    exchange = AsyncMock()
    monkeypatch.setattr(flow, "exchange_tokens", exchange)
    assert client.get(flow.PATH + "/callback", params={"code": "secret", "state": state}).json() == {"error_code": "invalid_callback"}
    exchange.assert_not_awaited()
    client.cookies.clear()
    client.cookies.set(routes.cookie_name("binding"), binding)
    flow.records[flow.digest(binding)]["expires"] = 0
    response = complete(client)
    assert response.status_code == 410 and response.json() == {"error_code": "completion_expired"}
    assert "Max-Age=0" in response.headers["set-cookie"]


def test_disabled_bootstrap_erases_credentials_but_preserves_bound_failure(client, monkeypatch):
    start(client)
    key = flow.digest(client.cookies[routes.cookie_name("binding")])
    flow.allocate(key, {"status": "complete", "body": {"access_token": "secret"}}, ttl=60)
    monkeypatch.setattr(flow.settings, "oauth_enabled", False)
    monkeypatch.setattr(flow, "readiness", REAL_READINESS)
    response = complete(client)
    assert response.status_code == 409 and response.json() == {"error_code": "client_disabled"}
    assert key not in flow.records and "secret" not in response.text


@pytest.mark.parametrize("fault", ["origin", "nonce", "content_type"])
def test_complete_csrf_error_parity_preserves_binding(client, fault):
    start(client)
    binding = client.cookies[routes.cookie_name("binding")]
    nonce = client.get(flow.PATH + "/bootstrap").json()["csrf_nonce"]
    response = client.post(flow.PATH + "/complete", json={"csrf_nonce": "wrong" if fault == "nonce" else nonce},
        headers={"Origin": "https://attacker.test" if fault == "origin" else flow.origin(),
                 "Content-Type": "text/plain" if fault == "content_type" else "application/json"})
    assert response.status_code == 403 and response.json() == {"error_code": "csrf_failed"}
    assert flow.records[flow.digest(binding)]["status"] == "pending"
    assert "set-cookie" not in response.headers
