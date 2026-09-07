"""Synthetic HTTP credential inventory; see C2 report for remaining §3.2 families."""
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


@pytest.mark.parametrize("upstream_status", [200, 403])
def test_runtime_version_confirmation_account_ownership(monkeypatch, upstream_status):
    from types import SimpleNamespace
    from app.routers import marketplace_publish as publish

    account_id, install_id = "synthetic-account-owner", "synthetic-install"
    calls = []
    real_client = httpx.AsyncClient
    def upstream(request):
        assert request.headers["authorization"] == "Bearer fixture-token"
        assert "cookie" not in request.headers and "x-api-key" not in request.headers
        calls.append((request.method, str(request.url), "account bearer", upstream_status, account_id))
        return httpx.Response(upstream_status, json={"version_id": "version-a", "listing_id": "listing-a",
            "version_label": "v1", "status": "active"} if upstream_status == 200 else {"detail": "not owner"})
    monkeypatch.setattr(httpx, "AsyncClient", lambda **kwargs: real_client(transport=httpx.MockTransport(upstream), **kwargs))
    monkeypatch.setattr(publish.settings, "ai_market_url", "https://api.ai.market")
    store = SimpleNamespace(state=SimpleNamespace(ai_market_access_token="fixture-token", vz_install_id=install_id))
    monkeypatch.setattr(publish, "get_serial_store", lambda: store)
    app = FastAPI()
    app.include_router(publish.router, prefix="/api")
    app.dependency_overrides[publish.get_current_user] = lambda: SimpleNamespace(user_id=account_id)
    with TestClient(app) as client:
        result = client.post("/api/marketplace/versions/version-a/confirm", headers={"Authorization": "Bearer fixture-token"})
    assert result.status_code == upstream_status
    if upstream_status == 200:
        assert result.json()["listing_id"] == "listing-a" and result.json()["result"] == "confirmed"
    else:
        assert result.json()["detail"] == "not owner"
    assert calls == [("POST", "https://api.ai.market/api/v1/vz/versions/version-a/confirm", "account bearer", upstream_status, account_id)]
    assert store.state.vz_install_id == install_id  # Account call does not replace installation ownership.


@pytest.mark.parametrize("family", ["disclosure", "signed_publish"])
@pytest.mark.parametrize("upstream_status", [201, 403])
def test_runtime_disclosure_and_signed_publish_inventory(monkeypatch, family, upstream_status):
    import json
    import jwt
    from types import SimpleNamespace
    from cryptography.hazmat.primitives.asymmetric.ed25519 import Ed25519PrivateKey
    from app.routers import marketplace_publish as publish
    from app.services.s3_publish_source_resolver import NotS3PublishSource

    account_id, install_id = "synthetic-owner", "synthetic-install"
    private_key = Ed25519PrivateKey.generate()
    crypto = SimpleNamespace(get_or_create_keypairs=lambda: (private_key, None, None, None), has_platform_keys=lambda: False)
    store = SimpleNamespace(state=SimpleNamespace(ai_market_access_token="fixture-token",
        ai_market_seller_id=account_id, vz_install_id=install_id, last_status_cache={}))
    record = SimpleNamespace(id="dataset-a", metadata={}, upload_path=None, listing_id=None)
    processing = SimpleNamespace(get_dataset=lambda dataset: record if dataset == record.id else None, _save_record=lambda *args: None)
    register = AsyncMock(return_value=install_id)
    monkeypatch.setattr(publish, "get_serial_store", lambda: store)
    monkeypatch.setattr(publish, "_get_crypto", lambda: crypto)
    monkeypatch.setattr(publish, "ensure_vz_install_registered", register)
    monkeypatch.setattr(publish, "resolve_s3_publish_source", lambda *args: NotS3PublishSource())
    monkeypatch.setattr(publish.settings, "ai_market_url", "https://api.ai.market")
    monkeypatch.setattr(publish.settings, "keystore_passphrase", "synthetic-passphrase")
    calls = []
    real_client = httpx.AsyncClient
    upstream_path = "/api/v1/listings/listing-a/disclosure-snapshots" if family == "disclosure" else "/api/v1/vz/publish"
    def upstream(request):
        payload = json.loads(request.content)
        bearer = request.headers["authorization"].removeprefix("Bearer ")
        if family == "signed_publish":
            claims = jwt.decode(bearer, private_key.public_key(), algorithms=["EdDSA"])
            assert (claims["sub"], claims["iss"], claims["action"]) == (account_id, install_id, "publish_listing")
            assert claims["metadata_hash"] == publish._jcs_hash(payload)
            assert payload["vz_raw_listing_id"] == record.id
            credential = "installation EdDSA action JWT"
        else:
            assert bearer == "fixture-token" and payload["approved_sample"] is None
            assert "dataset_id" not in payload
            credential = "account bearer"
        assert "cookie" not in request.headers and "x-api-key" not in request.headers
        calls.append((request.method, str(request.url), credential, upstream_status, account_id, install_id))
        return httpx.Response(upstream_status, json={"listing_id": "listing-a", "disclosure_version": "v1"}
            if upstream_status == 201 else {"detail": "not owner"})
    monkeypatch.setattr(httpx, "AsyncClient", lambda **kwargs: real_client(transport=httpx.MockTransport(upstream), **kwargs))
    app = FastAPI()
    app.include_router(publish.router, prefix="/api")
    app.dependency_overrides[publish.get_current_user] = lambda: SimpleNamespace(user_id=account_id, key_id="ai_market_bearer")
    app.dependency_overrides[publish.get_processing_service] = lambda: processing
    if family == "disclosure":
        path = "/api/marketplace/listings/listing-a/disclosure-snapshots"
        body = {"dataset_id": record.id, "approved_fields": {"title": "Synthetic"}, "sample_decision": "none",
            "ai_training_notification_ack": True, "ai_training_notification_text": "Synthetic disclosure",
            "license": "standard_marketplace", "approval_source": "aim_channel", "source_publish_operation_id": "op-a"}
    else:
        path = "/api/marketplace/publish"
        body = {"title": "Synthetic", "description": "Synthetic fixture", "price_cents": 0, "vz_dataset_id": record.id}
    with TestClient(app) as client:
        # Local status must not call this unregistered synthetic installation ready.
        assert client.get("/api/marketplace/publish-status").json()["can_publish"] is False
        result = client.post(path, json=body, headers={"Authorization": "Bearer fixture-token"})
    assert result.status_code == (200 if upstream_status == 201 else 403)
    if upstream_status == 201:
        assert record.listing_id == "listing-a"
    if family == "signed_publish":
        register.assert_awaited_once_with(crypto, access_token="fixture-token", seller_id=account_id)
    else:
        assert record.metadata["disclosure_decision"]["status"] == ("complete" if upstream_status == 201 else "snapshot_pending")
    credential = "account bearer" if family == "disclosure" else "installation EdDSA action JWT"
    assert calls == [("POST", "https://api.ai.market" + upstream_path, credential, upstream_status, account_id, install_id)]
