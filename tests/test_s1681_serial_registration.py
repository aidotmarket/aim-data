"""S1681 M2/G7: bind activated serial credentials during VZ registration."""

import json
from types import SimpleNamespace
from unittest.mock import AsyncMock, MagicMock

import httpx
import pytest

from app.services import registration_service as registration
from app.services import serial_store
from app.services.serial_store import ACTIVE, DEGRADED, MIGRATED, PROVISIONED, UNPROVISIONED, SerialStore

SERIAL = "VZ-0123abcd-4567efab"
SERIAL_TOKEN = "vzit_synthetic_serial_install"
VZ_TOKEN = "vzi_synthetic_vz_install"
BOOTSTRAP_TOKEN = "synthetic_bootstrap"


@pytest.fixture
def setup(monkeypatch, tmp_path):
    path = tmp_path / "serial.json"
    # Existing empty state also isolates tests from activation environment aliases.
    path.write_text("{}")
    store = SerialStore(str(path))
    monkeypatch.setattr(serial_store, "get_serial_store", lambda: store)
    monkeypatch.setattr(registration.settings, "internal_api_key", "aim_synthetic_key")
    monkeypatch.setattr(registration.settings, "ai_market_url", "https://market.example.test")
    crypto = MagicMock()
    crypto.get_public_keys_b64.return_value = ("synthetic-public-key", "unused-key")
    client = AsyncMock()
    client.__aenter__.return_value = client
    client.post.return_value = httpx.Response(200, json={
        "install_id": "synthetic-install-id", "install_token": VZ_TOKEN,
    })
    factory = MagicMock(return_value=client)
    monkeypatch.setattr(registration.httpx, "AsyncClient", factory)
    return SimpleNamespace(store=store, path=path, crypto=crypto, client=client, factory=factory)


def activate(store):
    store.state.serial = SERIAL
    store.transition_to_active(SERIAL_TOKEN)


@pytest.mark.asyncio
@pytest.mark.parametrize("status", [200, 201, 409])
async def test_activated_credentials_sent_and_binding_survives_reload(setup, status):
    activate(setup.store)
    setup.store.state.vz_install_token = VZ_TOKEN
    setup.store.state.bootstrap_token = BOOTSTRAP_TOKEN
    setup.client.post.return_value = httpx.Response(status, json={
        "install_id": "synthetic-install-id", "install_token": VZ_TOKEN,
    })
    assert await registration.ensure_vz_install_registered(
        setup.crypto, access_token="seller-token", seller_id="synthetic-seller",
    ) == "synthetic-install-id"
    setup.client.post.assert_awaited_once_with(
        "https://market.example.test/api/v1/vz/register",
        json={"public_key_b64": "synthetic-public-key", "serial": SERIAL,
              "serial_install_token": SERIAL_TOKEN},
        headers={"Authorization": "Bearer seller-token", "Content-Type": "application/json"},
    )
    assert setup.store.state.vz_install_serial_bound is True
    reloaded = SerialStore(str(setup.path))
    assert reloaded.state.vz_install_serial_bound is True
    assert reloaded.state.vz_install_id == "synthetic-install-id"
    assert reloaded.state.install_token == SERIAL_TOKEN
    assert reloaded.state.vz_install_token == VZ_TOKEN


@pytest.mark.asyncio
@pytest.mark.parametrize("status", [200, 201, 409])
async def test_no_serial_sends_only_public_key_and_stays_unbound(setup, status):
    setup.store.state.ai_market_access_token = "stored-token"
    setup.client.post.return_value = httpx.Response(status, json={"install_id": "synthetic-install-id"})
    assert await registration.ensure_vz_install_registered(setup.crypto) == "synthetic-install-id"
    setup.client.post.assert_awaited_once()
    assert setup.client.post.call_args.kwargs == {
        "json": {"public_key_b64": "synthetic-public-key"},
        "headers": {"Authorization": "Bearer stored-token", "Content-Type": "application/json"},
    }
    assert setup.store.state.vz_install_serial_bound is False
    assert SerialStore(str(setup.path)).state.vz_install_serial_bound is False


@pytest.mark.asyncio
@pytest.mark.parametrize("state,serial,install_token,bootstrap", [
    (ACTIVE, SERIAL, None, None),
    (ACTIVE, SERIAL, "", None),
    (ACTIVE, "", SERIAL_TOKEN, None),
    (ACTIVE, None, SERIAL_TOKEN, None),
    (ACTIVE, "", None, None),
    (ACTIVE, SERIAL, None, BOOTSTRAP_TOKEN),
    (PROVISIONED, SERIAL, None, BOOTSTRAP_TOKEN),
    (PROVISIONED, SERIAL, SERIAL_TOKEN, None),
    (UNPROVISIONED, SERIAL, SERIAL_TOKEN, None),
    (DEGRADED, SERIAL, SERIAL_TOKEN, None),
    (MIGRATED, SERIAL, SERIAL_TOKEN, None),
])
async def test_partial_or_stale_credentials_never_use_other_tokens(
    setup, state, serial, install_token, bootstrap,
):
    setup.store.state.state = state
    setup.store.state.serial = serial
    setup.store.state.install_token = install_token
    setup.store.state.bootstrap_token = bootstrap
    setup.store.state.vz_install_token = VZ_TOKEN
    assert await registration.ensure_vz_install_registered(
        setup.crypto, access_token="seller-token",
    ) == "synthetic-install-id"
    setup.client.post.assert_awaited_once()
    assert setup.client.post.call_args.kwargs["json"] == {"public_key_b64": "synthetic-public-key"}
    assert setup.store.state.vz_install_serial_bound is False


@pytest.mark.asyncio
@pytest.mark.parametrize("bound", [False, True])
async def test_activated_cached_install_requires_binding(setup, monkeypatch, bound):
    activate(setup.store)
    setup.store.persist_vz_install("cached-id", VZ_TOKEN, serial_bound=bound)
    warning = MagicMock()
    monkeypatch.setattr(registration.logger, "warning", warning)
    result = await registration.ensure_vz_install_registered(
        setup.crypto, access_token="seller-token",
    )
    assert result == ("cached-id" if bound else None)
    setup.factory.assert_not_called()
    setup.client.post.assert_not_awaited()
    if bound:
        warning.assert_not_called()
    else:
        warning.assert_called_once()
        warning_text = str(warning.call_args)
        assert "unbound" in warning_text
        assert "activated serial credentials" in warning_text
        for secret in (SERIAL, SERIAL_TOKEN, VZ_TOKEN, "seller-token"):
            assert secret not in warning_text
    assert setup.store.state.vz_install_id == "cached-id"
    assert SerialStore(str(setup.path)).state.vz_install_serial_bound is bound


@pytest.mark.asyncio
async def test_cached_install_without_activated_credentials_is_unchanged(setup):
    setup.store.persist_vz_install("cached-id", VZ_TOKEN)
    assert await registration.ensure_vz_install_registered(setup.crypto) == "cached-id"
    setup.factory.assert_not_called()


def test_legacy_serial_file_without_binding_key_loads_unbound(setup):
    setup.path.write_text(json.dumps({
        "state": ACTIVE, "serial": SERIAL, "install_token": SERIAL_TOKEN,
        "vz_install_id": "legacy-id", "vz_install_token": VZ_TOKEN,
    }))
    reloaded = SerialStore(str(setup.path))
    assert reloaded.state.vz_install_serial_bound is False
    assert reloaded.state.vz_install_id == "legacy-id"


@pytest.mark.asyncio
@pytest.mark.parametrize("outcome", [401, 403, 500, "timeout", "connection", "missing-id", "409-missing-id"])
async def test_failures_do_not_persist_binding(setup, outcome):
    activate(setup.store)
    if outcome == "timeout":
        setup.client.post.side_effect = httpx.ReadTimeout("synthetic failure")
    elif outcome == "connection":
        setup.client.post.side_effect = httpx.ConnectError("synthetic failure")
    else:
        status = {"missing-id": 200, "409-missing-id": 409}.get(outcome, outcome)
        setup.client.post.return_value = httpx.Response(status, json={})
    assert await registration.ensure_vz_install_registered(
        setup.crypto, access_token="seller-token",
    ) is None
    setup.client.post.assert_awaited_once()
    assert setup.store.state.vz_install_id is None
    assert SerialStore(str(setup.path)).state.vz_install_serial_bound is False


@pytest.mark.asyncio
async def test_missing_bearer_does_not_fall_back_to_api_key(setup):
    activate(setup.store)
    assert await registration.ensure_vz_install_registered(setup.crypto) is None
    setup.factory.assert_not_called()
