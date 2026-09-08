"""Synthetic runtime inventory for every Gate 1 §3.2 family; no live or paid calls."""
from unittest.mock import AsyncMock, Mock
import base64
import json
from pathlib import Path
from types import SimpleNamespace
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
        if request.url.path == "/api/v1/trust/register":
            assert "x-api-key" not in request.headers
            return httpx.Response(200, json={
                "ai_market_ed25519_public_key": "synthetic-ed25519",
                "ai_market_x25519_public_key": "synthetic-x25519",
                "certificate": "synthetic-certificate",
            })
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
        ("POST", "https://api.ai.market/api/v1/trust/register", "Bearer synthetic-account"),
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

BASE = "https://api.ai.market"
FIXTURES = Path(__file__).parent / "fixtures/data_verification_v1"


@pytest.fixture(params=["owner-a", "owner-b"])
def wire(monkeypatch, request):
    """Exercise each caller with distinct account/install/key bindings in memory."""
    from app.config import settings
    owner = request.param
    state = SimpleNamespace(ai_market_seller_id=owner, ai_market_access_token="account-" + owner,
        vz_install_id="install-" + owner, serial="serial-" + owner, install_token="install-token-" + owner)
    monkeypatch.setattr(settings, "ai_market_url", BASE)
    monkeypatch.setattr(settings, "aimarket_url", BASE)
    monkeypatch.setattr(settings, "internal_api_key", "operator-key-" + owner)
    monkeypatch.setattr(settings, "serial", state.serial)
    pending, seen, expected = [], [], []
    real_async, real_sync = httpx.AsyncClient, httpx.Client
    def handler(req):
        assert pending, "Unexpected outbound call"
        method, path, credentials, response, check = pending.pop(0)
        assert (req.method, str(req.url)) == (method, BASE + path)
        actual = {k: v for k, v in req.headers.items() if k in (
            "authorization", "x-api-key", "x-internal-api-key", "x-vz-feedback-key", "cookie")}
        if callable(credentials):
            assert credentials(actual)
        else:
            assert actual == credentials  # No account/cookie substitution.
        if check:
            check(req)
        seen.append((method, path, credentials))
        return response
    transport = httpx.MockTransport(handler)
    monkeypatch.setattr(httpx, "AsyncClient", lambda **kw: real_async(transport=transport, **kw))
    monkeypatch.setattr(httpx, "Client", lambda **kw: real_sync(transport=transport, **kw))
    def expect(method, path, credentials, body=None, status=200, check=None, content=None):
        response = httpx.Response(status, json=body) if content is None else httpx.Response(status, content=content)
        pending.append((method, path, credentials, response, check))
        expected.append((method, path))
    yield SimpleNamespace(state=state, owner=owner, key=settings.internal_api_key,
        expect=expect, seen=seen, account={"authorization": "Bearer " + state.ai_market_access_token},
        install={"authorization": "Bearer " + state.install_token}, operator={"x-api-key": settings.internal_api_key})
    assert not pending, "Expected runtime caller was not exercised"
    assert [(method, path) for method, path, _ in seen] == expected
    assert state.ai_market_seller_id == owner and state.vz_install_id == "install-" + owner


@pytest.mark.asyncio
async def test_readiness_status_quote_start_lifecycle_signed_report_inventory(wire):
    import jwt
    from cryptography.hazmat.primitives.asymmetric.ed25519 import Ed25519PrivateKey
    from app.services.data_verification_client import DataVerificationClient, DataVerificationClientError
    from app.services.marketplace_action_signer import canonical_payload_hash, sign_receipt_payload, canonical_json_bytes
    from app.schemas.data_verification import QuoteProbeRequest, ScanSpecIssueRequest, LifecycleCommand

    key = Ed25519PrivateKey.generate()
    client = DataVerificationClient(base_url=BASE, seller_id=wire.owner, install_id=wire.state.vz_install_id,
        install_private_key=key, seller_access_token=wire.state.ai_market_access_token)
    spec = json.loads((FIXTURES / "scan_spec.json").read_text())
    report = json.loads((FIXTURES / "report.json").read_text())
    listing, verification = report["listing_id"], report["verification_id"]
    status = dict(verification_id=verification, state="QUOTED", authorization_usd=None, captured_usd=None,
        result_available=False, publication_allowed=False, reconciliation_required=False)
    wire.expect("GET", "/api/v1/data-verification/payment-method/readiness", wire.account,
        dict(version="data_verification_payin_readiness_v1", state="ready", can_start_setup=False,
             can_replace_payment_method=True, message="synthetic"))
    assert await client.payment_method_readiness() == "ready"
    wire.expect("GET", f"/api/v1/data-verification/{verification}/status", wire.account, status)
    assert (await client.status(verification)).verification_id == verification
    # Signatures are verified at the MockTransport boundary, including owner and install.
    def signed_request(action, body):
        def check(req):
            claims = jwt.decode(req.headers["authorization"][7:], key.public_key(), algorithms=["EdDSA"])
            assert (claims["sub"], claims["iss"], claims["action"]) == (wire.owner, wire.state.vz_install_id, action)
            assert json.loads(req.content) == body
            assert claims["payload_hash"] == canonical_payload_hash(body)
        return check
    # Capture the real dynamically minted JWT without replacing the signing operation.
    original_expect = wire.expect
    def signed_expect(path, action, body, response):
        credentials = lambda headers: set(headers) == {"authorization"} and headers["authorization"].startswith("Bearer ey")
        original_expect("POST", path, credentials, response, check=signed_request(action, body))
    probe = QuoteProbeRequest(listing_id=listing, source_handle_id=report["source_handle_id"],
        connector_type="eolymp", connector_version="eolymp-v1", owner_consent=True, source_reachable=True,
        objects_discovered=1, size_class="small", supported_capabilities=("complete_traversal",
        "deterministic_object_order", "fixed_bucket_aggregates", "exact_or_declared_estimated_row_counts"),
        estimated_max_input_tokens=512, preview_requested=False)
    quote = dict(quote_id="quote_fixture", depth_class="complete_standard_v1", traversal_scope="all_reachable_supported_objects",
        row_count_policy="exact_or_declared_estimate", low_occupancy_behavior="suppressed_low_occupancy",
        minimum_aggregate_occupancy=10, hard_maximum=dict(authorization_usd="25.00",
        inference=dict(max_input_tokens=8192, max_output_tokens=1024, model_request_count=1)), partial_traversal_allowed=False)
    signed_expect("/api/v1/data-verification/quote", "data_verification_quote", probe.model_dump(mode="json"), quote)
    assert (await client.quote(probe)).quote_id == quote["quote_id"]
    start = ScanSpecIssueRequest(**{k: report[k] for k in ScanSpecIssueRequest.model_fields if k in report})
    signed_expect("/api/v1/data-verification/scan-spec", "data_verification_start", start.model_dump(mode="json"), spec)
    assert (await client.start(start)).spec_hash == spec["spec_hash"]
    for action in ("cancel", "publish", "decline", "withdraw"):
        command = LifecycleCommand(verification_id=verification, listing_id=listing,
            source_handle_id=report["source_handle_id"], requested_action=action)
        signed_expect(f"/api/v1/data-verification/{verification}/{action}", "data_verification_" + action,
            command.model_dump(mode="json"), status)
        assert (await client.command(command)).status.verification_id == verification
    report["install_key_id"] = wire.state.vz_install_id
    unsigned = {k: v for k, v in report.items() if k != "receipt_signature"}
    report["receipt_signature"] = sign_receipt_payload(unsigned, key)
    def signed_body(req):
        body = json.loads(req.content)
        signature = body.pop("receipt_signature")
        key.public_key().verify(base64.b64decode(signature), canonical_json_bytes(body))
        assert body["install_key_id"] == wire.state.vz_install_id and body["listing_id"] == listing
    wire.expect("PUT", "/api/v1/data-verification/scan-spec", {}, {"verification_id": verification, "accepted": True}, check=signed_body)
    assert str((await client.ingest_report(report)).verification_id) == verification
    wire.expect("GET", f"/api/v1/data-verification/{verification}/status", wire.account, {"detail": "other owner"}, status=403)
    with pytest.raises(DataVerificationClientError, match="403"):
        await client.status(verification)


@pytest.mark.asyncio
async def test_connect_raw_listings_request_sync_and_legacy_listing_keys(wire, monkeypatch):
    from app.services import stripe_connect_proxy as connect, raw_listing_service as raw, request_sync_service as sync
    from app.services.marketplace_push_service import MarketplacePushService
    store = SimpleNamespace(state=wire.state)
    monkeypatch.setattr(raw, "get_serial_store", lambda: store)
    async with httpx.AsyncClient() as client:
        monkeypatch.setattr(connect, "_client", client)
        proxy = connect.StripeConnectProxy()
        for method, suffix, caller in (("POST", "onboarding", proxy.initiate_onboarding),
                ("GET", "status", proxy.get_status), ("POST", "login-link", proxy.get_login_link)):
            result = {"account_id": wire.owner, "status": "synthetic"}
            wire.expect(method, "/api/v1/connect/" + suffix, wire.operator, result)
            assert await caller(wire.key) == result
        wire.expect("GET", "/api/v1/connect/status", wire.operator, {"detail": "not owner"}, status=403)
        with pytest.raises(connect.StripeConnectProxyError) as exc:
            await proxy.get_status(wire.key)
        assert exc.value.status_code == 403 and exc.value.detail == "not owner"
    wire.expect("POST", "/api/v1/listings/", wire.account, {"id": "listing-" + wire.owner}, status=201)
    assert await raw.RawListingService()._create_marketplace_listing({"title": "synthetic"}) == "listing-" + wire.owner
    wire.expect("GET", "/api/v1/data-requests?limit=50", wire.account, {"items": [{"buyer_id": wire.owner}], "next_cursor": None})
    account = wire.state.ai_market_access_token
    assert await sync.poll_requests(BASE, auth_token=account) == ([{"buyer_id": wire.owner}], None)
    # Legacy key-based create, conflict/mine resolution, and update all run unchanged.
    legacy = MarketplacePushService()
    wire.expect("POST", "/api/v1/listings/", wire.operator, {"id": "listing-" + wire.owner}, status=201)
    assert (await legacy._push_with_retry({"title": "synthetic"}))["listing_id"] == "listing-" + wire.owner
    wire.expect("POST", "/api/v1/listings/", wire.operator, {}, status=409)
    wire.expect("GET", "/api/v1/listings/mine", wire.operator, [{"title": "synthetic", "id": "listing-" + wire.owner}])
    wire.expect("PATCH", "/api/v1/listings/listing-" + wire.owner, wire.operator, {"owner_id": wire.owner})
    result = await legacy._push_with_retry({"title": "synthetic"})
    assert result["status"] == "updated" and result["response"] == {"owner_id": wire.owner}


@pytest.mark.asyncio
async def test_serial_bootstrap_activation_status_refresh_checkout_account_metering(wire, monkeypatch):
    from decimal import Decimal
    from app.services.activation_manager import ActivationManager
    from app.services.serial_client import SerialClient
    from app.services.serial_store import PROVISIONED
    client = SerialClient()
    store = SimpleNamespace(state=SimpleNamespace(), save=Mock())
    manager = ActivationManager(store=store, client=client)
    monkeypatch.setattr(manager, "_attempt_activation", AsyncMock())
    wire.expect("POST", "/api/v1/serials/generate", {}, {"serial": wire.state.serial, "bootstrap_token": "bootstrap-" + wire.owner}, status=201)
    await manager._auto_provision()
    assert store.state.serial == wire.state.serial and store.state.state == PROVISIONED
    assert store.state.bootstrap_token == "bootstrap-" + wire.owner
    manager._attempt_activation.assert_awaited_once()
    prefix = "/api/v1/serials/" + wire.state.serial
    wire.expect("POST", prefix + "/activate", {"authorization": "Bearer " + store.state.bootstrap_token},
        {"install_token": wire.state.install_token, "serial_id": wire.state.serial},
        check=lambda req: assert_json(req, "instance_id", wire.state.vz_install_id))
    activated = await client.activate(wire.state.serial, store.state.bootstrap_token, wire.state.vz_install_id, "synthetic", "test")
    assert activated.success and activated.install_token == wire.state.install_token and activated.serial_id == wire.state.serial
    wire.expect("GET", prefix + "/status", wire.install, {"serial_id": wire.state.serial, "owner": wire.owner})
    assert (await client.status(wire.state.serial, wire.state.install_token)).data["owner"] == wire.owner
    wire.expect("POST", prefix + "/refresh", wire.install, {"install_token": "rotated-" + wire.owner},
        check=lambda req: assert_json(req, "instance_id", wire.state.vz_install_id))
    assert (await client.refresh(wire.state.serial, wire.state.install_token, wire.state.vz_install_id)).install_token == "rotated-" + wire.owner
    for suffix, caller in (("credits/checkout", client.credits_checkout), ("account", client.get_account), ("credits/usage", client.credits_usage)):
        wire.expect("POST" if suffix.endswith("checkout") else "GET", prefix + "/" + suffix, wire.install, {"owner": wire.owner})
        assert (await caller(wire.state.serial, wire.state.install_token))["owner"] == wire.owner
    wire.expect("POST", prefix + "/meter", wire.install, {"allowed": False, "remaining_usd": "0.00", "reason": "exhausted"}, status=402)
    result = await client.meter(wire.state.serial, wire.state.install_token, "data", Decimal("0"), "synthetic-request")
    assert not result.allowed and result.status_code == 402 and result.reason == "exhausted"


def assert_json(request, key, expected):
    assert json.loads(request.content)[key] == expected


@pytest.mark.asyncio
async def test_credits_deduction_reconciliation_internal_key_inventory(wire):
    from app.services import reconciliation
    from app.services.deduction_queue import DeductionQueue
    from app.services.metering_service import MeteringService
    credentials = {"x-internal-api-key": wire.key}
    payload = {"user_id": wire.owner, "amount_cents": 0}
    for cls, method in ((DeductionQueue, "_attempt_send"), (MeteringService, "_attempt_deduct")):
        for status in (200, 402):
            wire.expect("POST", "/api/v1/credits/deduct", credentials, {"user_id": wire.owner}, status=status,
                check=lambda req: assert_json(req, "user_id", wire.owner))
            result = await getattr(cls.__new__(cls), method)(payload, "synthetic-idempotency")
            assert result == (status == 200, {"user_id": wire.owner}, False, status)
    for path, field, caller in (("balance", "balance_cents", reconciliation._fetch_remote_balance),
            ("deductions", "total_deducted_cents", reconciliation._fetch_remote_deductions_total)):
        wire.expect("GET", f"/api/v1/credits/{path}/{wire.owner}" + ("?period=24h" if path == "deductions" else ""), credentials, {field: 17})
        assert await caller(wire.owner) == 17


def test_s3_broker_inventory(wire, monkeypatch):
    from app.services import s3_broker_client as broker
    monkeypatch.setattr(broker, "get_serial_store", lambda: SimpleNamespace(state=wire.state))
    client = broker.S3BrokerClient()
    prefix = "/api/v1/serials/" + wire.state.serial + "/s3-connections/"
    kwargs = dict(role_arn="synthetic-role-" + wire.owner, region="eu-west-1", bucket="synthetic-" + wire.owner)
    for method, op, caller, args, body in (
        ("GET", "external-id", client.get_external_id, {}, {"external_id": wire.owner}),
        ("POST", "verify", client.verify, kwargs, {"owner": wire.owner}),
        ("POST", "list-objects", client.list_objects, kwargs, {"owner": wire.owner}),
        ("POST", "presign-object", client.presign_object, {**kwargs, "object_key": "synthetic"}, {"url": "https://artifact.test/synthetic"}),
    ):
        wire.expect(method, prefix + op + "?serial=" + wire.state.serial, wire.install, body)
        assert caller(**args) == (wire.owner if op == "external-id" else body)
    wire.expect("GET", prefix + "external-id?serial=" + wire.state.serial, wire.install, {}, status=403)
    with pytest.raises(broker.S3BrokerError, match="authentication failed"):
        client.get_external_id()


@pytest.mark.asyncio
async def test_allai_chat_and_agentic_install_key_inventory(wire, monkeypatch):
    from app.services.allie_provider import AiMarketAllieProvider
    from app.services import allai_agentic_provider as agentic
    def serial_header(req):
        assert req.headers["x-serial"] == wire.state.serial
    wire.expect("POST", "/api/v1/allie/chat", wire.operator, check=serial_header,
        content='event: delta\ndata: {"text":"synthetic"}\n\nevent: done\ndata: {"cost_cents":0}\n\n')
    chunks = [chunk async for chunk in AiMarketAllieProvider(serial=wire.state.serial, api_key=wire.key).stream("synthetic")]
    assert chunks[0].text == "synthetic" and chunks[-1].done and chunks[-1].usage.cost_cents == 0
    monkeypatch.setattr(agentic, "read_allie_config", lambda: dict(install_token=wire.state.install_token, serial_number=wire.state.serial, ai_market_url=BASE))
    wire.expect("POST", "/api/v1/allie/chat/agentic", {"x-api-key": wire.state.install_token}, {"content": [{"text": wire.owner}]}, check=serial_header)
    assert await agentic.AgenticAllieProvider()._call_proxy([], "synthetic", []) == {"content": [{"text": wire.owner}]}


@pytest.mark.asyncio
async def test_trust_registration_and_websocket_stream_inventory(wire, monkeypatch):
    import asyncio
    from contextlib import asynccontextmanager
    from app.services import registration_service as registration, trust_channel_client as trust
    crypto = SimpleNamespace(has_platform_keys=lambda: False, get_public_keys_b64=lambda: ("ed-" + wire.owner, "x-" + wire.owner), store_platform_keys=Mock())
    monkeypatch.setattr(registration, "_get_device_id", lambda: wire.state.vz_install_id)
    wire.expect("POST", "/api/v1/trust/register", wire.operator,
        dict(ai_market_ed25519_public_key="platform-ed", ai_market_x25519_public_key="platform-x", certificate="synthetic"),
        check=lambda req: assert_json(req, "device_id", wire.state.vz_install_id))
    assert await registration.register_with_marketplace(crypto, wire.key)
    crypto.store_platform_keys.assert_called_once_with(platform_ed25519_pub="platform-ed", platform_x25519_pub="platform-x", certificate="synthetic")
    # httpx has no WebSocket transport: exercise the real receive loop with a synthetic upgrade.
    client = trust.TrustChannelClient()
    message = {"action": "synthetic", "transfer_id": wire.state.vz_install_id, "owner": wire.owner}
    future = asyncio.get_running_loop().create_future()
    client._waiters["synthetic:" + wire.state.vz_install_id] = future
    from datetime import datetime, timedelta, timezone
    from app.services.trust_channel_protocol import TrafficSession
    # This inventory checks URL/credential routing. Full crypto interoperability
    # is covered by test_trust_channel_client's independent loopback server.
    monkeypatch.setattr(client, "_load_identity", lambda: ("registered-device", None, None, None, None))
    monkeypatch.setattr(client, "_handshake", AsyncMock(return_value=(
        TrafficSession(bytes(32), bytes(32), bytes(12)),
        {"session_id": "inventory", "expires_at": (datetime.now(timezone.utc) + timedelta(hours=1)).isoformat()},
    )))
    socket = SimpleNamespace(recv=AsyncMock(side_effect=[
        json.dumps({"type": "event", "sequence": 0, "payload": message}),
        ConnectionError("inventory complete"),
    ]))
    @asynccontextmanager
    async def connect(url, **kwargs):
        assert url == "wss://api.ai.market/api/v1/trust/stream"
        assert kwargs["additional_headers"] == {"X-API-Key": wire.key}
        yield socket
    monkeypatch.setattr(trust.websockets, "connect", connect)
    with pytest.raises(ConnectionError, match="inventory complete"):
        await client._connect_and_listen()
    assert future.result() == message and client._ws is None


@pytest.mark.asyncio
async def test_feedback_and_diagnostics_inventory(wire, monkeypatch):
    from contextlib import contextmanager
    from io import BytesIO
    from fastapi import HTTPException
    from app.core import database
    from app.services.allai_tool_executor import AllAIToolExecutor
    from app.routers import diagnostics
    saved = []
    session = SimpleNamespace(add=lambda obj: saved.append(obj), commit=lambda: None, refresh=lambda obj: None, get=lambda *args: saved[0])
    @contextmanager
    def memory_session():
        yield session
    monkeypatch.setattr(database, "get_session_context", memory_session)
    monkeypatch.setenv("VECTORAIZ_AI_MARKET_URL", BASE)
    user = SimpleNamespace(user_id=wire.owner)
    executor = AllAIToolExecutor(user, AsyncMock(), "synthetic-session")
    wire.expect("POST", "/api/v1/feedback", {}, {}, check=lambda req: assert_json(req, "user_id", wire.owner))
    result = await executor._handle_submit_feedback({"summary": "synthetic"})
    assert result.frontend_data["forwarded"] and saved[0].user_id == wire.owner
    wire.expect("POST", "/api/v1/feedback/ingest", {"x-vz-feedback-key": "beta-feedback-key"}, {},
        check=lambda req: assert_json(req, "user_id", wire.owner))
    assert await executor._forward_feedback_to_aimarket(BASE, {"user_id": wire.owner}) is None
    monkeypatch.setattr(diagnostics, "_last_transmit_time", 0)
    monkeypatch.setattr(diagnostics, "DiagnosticService", lambda: SimpleNamespace(generate_bundle=AsyncMock(return_value=BytesIO(b"synthetic bundle"))))
    wire.expect("POST", "/api/v1/support/upload-diagnostic", {}, {}, status=403)
    with pytest.raises(HTTPException) as exc:
        await diagnostics.transmit_diagnostic_bundle(user)
    assert exc.value.status_code == 502 and "403" in exc.value.detail


@pytest.mark.asyncio
async def test_serial_s3_and_magic_link_inventory(wire):
    from app.services.serial_client import SerialClient
    client = SerialClient()
    prefix = "/api/v1/serials/" + wire.state.serial + "/s3-connections/"
    common = dict(serial=wire.state.serial, install_token=wire.state.install_token)
    s3 = dict(role_arn="role-" + wire.owner, region="eu-west-1", bucket="bucket-" + wire.owner)
    for method, op, caller, args in (
        ("GET", "external-id", client.get_s3_external_id, {}),
        ("POST", "verify", client.verify_s3_connection, {**s3, "external_id": wire.owner}),
        ("POST", "list-objects", client.list_s3_objects, s3),
        ("POST", "presign-object", client.presign_object, {**s3, "object_key": "synthetic"}),
    ):
        wire.expect(method, prefix + op, wire.install, {"owner": wire.owner})
        assert (await caller(**common, **args))["owner"] == wire.owner
    wire.expect("POST", "/api/v1/auth/magic-link", wire.install, {"owner": wire.owner})
    assert (await client.send_magic_link(**common, email=wire.owner + "@example.test"))["owner"] == wire.owner
    wire.expect("POST", "/api/v1/auth/verify-magic-link", wire.install, {"owner": wire.owner})
    assert (await client.verify_magic_link(**common, token="synthetic"))["owner"] == wire.owner


@pytest.mark.asyncio
async def test_account_me_link_cache_ownership_inventory(wire, monkeypatch):
    from app.auth import api_key_auth as keys
    from app.core.database import get_session_context
    from app.models.user import User
    from sqlmodel import select
    identity = str(uuid4())
    body = {"id": identity, "email": identity + "@example.test", "role": "buyer", "status": "active"}
    memory_store = SimpleNamespace(persist_ai_market_session=Mock())
    from app.services import serial_store
    monkeypatch.setattr(serial_store, "get_serial_store", lambda: memory_store)
    monkeypatch.setenv("VECTORAIZ_AUTH_ENABLED", "true")
    keys.api_key_cache.clear()
    wire.expect("GET", "/api/v1/auth/me", wire.account, body)
    app = FastAPI()
    app.include_router(auth.router, prefix="/api/auth")
    with TestClient(app, base_url="http://127.0.0.1:8080") as client:
        for _ in range(2):
            response = client.get("/api/auth/me", headers=wire.account)
            assert response.status_code == 200 and response.json()["user_id"] == identity
            assert response.json()["role"] == "user"
    # Exactly one upstream fetch; the second HTTP request uses the bound cache.
    assert len(wire.seen) == 1
    memory_store.persist_ai_market_session.assert_called_once_with(wire.state.ai_market_access_token, identity)
    with get_session_context() as db:
        linked = db.exec(select(User).where(User.ai_market_user_id == identity)).one()
        assert linked.role == "user" and linked.pw_hash is None
    keys.api_key_cache.clear()
