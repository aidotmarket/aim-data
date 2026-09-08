"""T-2026-000780: OAuth installs register a Trust device and recover on refresh."""
import base64
import json
from types import SimpleNamespace
from unittest.mock import AsyncMock, MagicMock, patch

import httpx
import pytest

from app.core.crypto import DeviceCrypto
from app.services import registration_service as registration
from app.services import connected_login
from app.services.trust_channel_client import TrustChannelClient


@pytest.mark.asyncio
@pytest.mark.parametrize("explicit,stored,key,expected", [
    ("explicit-token", "stored-token", "aim_key", {"Authorization": "Bearer explicit-token"}),
    (None, "stored-token", "aim_key", {"Authorization": "Bearer stored-token"}),
    (None, None, "aim_key", {"X-API-Key": "aim_key"}),
    (None, None, None, None),
])
async def test_helper_precedence(monkeypatch, explicit, stored, key, expected):
    crypto = MagicMock()
    crypto.has_platform_keys.return_value = False
    crypto.get_public_keys_b64.return_value = ("ed", "x")
    monkeypatch.setattr(registration.settings, "internal_api_key", key)
    store = SimpleNamespace(state=SimpleNamespace(ai_market_access_token=stored))
    with patch("app.services.serial_store.get_serial_store", return_value=store), \
         patch("app.services.registration_service.httpx.AsyncClient") as factory, \
         patch.object(registration.logger, "warning") as warning:
        client = AsyncMock()
        client.__aenter__.return_value = client
        client.post.return_value = httpx.Response(200, json={"certificate": "cert"})
        factory.return_value = client
        assert await registration.ensure_trust_device_registered(crypto, explicit) is bool(expected)
    if expected:
        assert client.post.call_args.kwargs["headers"] == {**expected, "Content-Type": "application/json"}
    else:
        factory.assert_not_called()
        warning.assert_called_once_with("No credential available for device registration — skipping")


@pytest.mark.asyncio
async def test_helper_existing_keys_skips_credentials():
    crypto = MagicMock()
    crypto.has_platform_keys.return_value = True
    with patch("app.services.serial_store.get_serial_store") as store, \
         patch.object(registration, "register_with_marketplace", new_callable=AsyncMock) as register:
        assert await registration.ensure_trust_device_registered(crypto)
    store.assert_not_called()
    register.assert_not_awaited()


@pytest.fixture
def login_setup(monkeypatch, tmp_path):
    from app.routers import auth

    monkeypatch.setattr(connected_login.settings, "keystore_passphrase", "test-passphrase")
    monkeypatch.setattr(connected_login.settings, "keystore_path", str(tmp_path / "keystore.json"))
    monkeypatch.setattr(connected_login.settings, "internal_api_key", None)
    monkeypatch.setattr(auth, "_handle_ai_market_token", AsyncMock())
    monkeypatch.setattr(registration, "ensure_vz_install_registered", AsyncMock(return_value="vz-install"))
    return {"access_token": "new-login-token", "refresh_token": "new-refresh-token"}


@pytest.mark.asyncio
@pytest.mark.parametrize("refresh", [False, True])
@pytest.mark.parametrize("trust_status", [200, 401, "exception"])
async def test_login_and_refresh_register_trust(login_setup, refresh, trust_status):
    data = login_setup
    client = AsyncMock()
    client.__aenter__.return_value = client
    client.get.return_value = httpx.Response(200, json={"id": "seller"})
    client.post.return_value = httpx.Response(trust_status if isinstance(trust_status, int) else 200, json={
        "ai_market_ed25519_public_key": "ed", "ai_market_x25519_public_key": "x", "certificate": "cert",
    })
    with patch("app.services.connected_login.httpx.AsyncClient", return_value=client), \
         patch("app.services.aim_market_oauth.exchange_tokens", new_callable=AsyncMock, return_value=data), \
         patch("asyncio.sleep", new_callable=AsyncMock):
        if trust_status == "exception":
            client.post.side_effect = RuntimeError("upstream failure")
        if refresh:
            result = await connected_login.refresh_connected_login({"auth_mode": "oauth", "refresh_token": "old-token"})
        else:
            result = await connected_login.complete_connected_login(data, "oauth")
    assert result == {
        **data, "token_type": "bearer", "user": {"id": "seller"}, "auth_mode": "oauth",
        "onboarding_required": False, "onboarding_step": None, "registration_status": "registered",
    }
    assert client.post.call_args.args[0].endswith("/api/v1/trust/register")
    assert client.post.call_args.kwargs["headers"] == {
        "Authorization": "Bearer new-login-token", "Content-Type": "application/json",
    }


@pytest.mark.asyncio
async def test_trust_failure_independent_of_vz(login_setup):
    client = AsyncMock()
    client.__aenter__.return_value = client
    client.get.return_value = httpx.Response(200, json={"id": "seller"})
    with patch("app.services.connected_login.httpx.AsyncClient", return_value=client), \
         patch.object(registration, "ensure_vz_install_registered", new_callable=AsyncMock, side_effect=RuntimeError), \
         patch.object(registration, "ensure_trust_device_registered", new_callable=AsyncMock, side_effect=RuntimeError) as trust:
        result = await connected_login.complete_connected_login(login_setup, "oauth")
    trust.assert_awaited_once()
    assert trust.call_args.kwargs == {"access_token": "new-login-token"}
    assert result["access_token"] == "new-login-token"
    assert result["registration_status"] == "not_ready"


@pytest.mark.asyncio
@pytest.mark.parametrize("api_key", [None, "aim_legacy"])
@pytest.mark.parametrize("certificate_exists", [False, True])
async def test_socket_certificate_without_api_key(monkeypatch, tmp_path, api_key, certificate_exists):
    from app.services import trust_channel_client as channel

    path = str(tmp_path / "keystore.json")
    monkeypatch.setattr(channel.settings, "keystore_path", path)
    monkeypatch.setattr(channel.settings, "keystore_passphrase", "test-passphrase")
    monkeypatch.setattr(channel.settings, "internal_api_key", api_key)
    crypto = DeviceCrypto(path, "test-passphrase")
    crypto.get_or_create_keypairs()
    if certificate_exists:
        ed, x = crypto.get_public_keys_b64()
        certificate = base64.b64encode(json.dumps({
            "device_id": "registered-device", "ed25519_public_key": ed, "x25519_public_key": x,
        }).encode()).decode()
        crypto.store_platform_keys(ed, x, certificate)
    socket = AsyncMock()
    socket.__aenter__.return_value = socket
    client = TrustChannelClient()
    with patch.object(channel.websockets, "connect", return_value=socket) as connect, \
         patch.object(client, "_handshake", new_callable=AsyncMock, side_effect=ConnectionError("handshake reached")) as handshake:
        with pytest.raises(ConnectionError, match="handshake reached" if certificate_exists else "Trust Channel device registration is missing"):
            await client._connect_and_listen()
    if certificate_exists:
        assert connect.call_args.kwargs["additional_headers"] == ({"X-API-Key": api_key} if api_key else {})
        handshake.assert_awaited_once()
        assert handshake.call_args.args[1] == "registered-device"
    else:
        connect.assert_not_called()
        handshake.assert_not_awaited()
