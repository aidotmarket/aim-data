"""OAuth-only installations start the real Trust client and fulfillment handler."""
import asyncio
import base64
import json
from contextlib import ExitStack
from unittest.mock import AsyncMock, MagicMock, patch

import pytest

from app.core.crypto import DeviceCrypto


@pytest.mark.asyncio
async def test_lifespan_starts_trust_and_fulfillment_without_api_key(monkeypatch, tmp_path):
    from app import main
    from app.services import registration_service as registration
    from app.services import fulfillment_service as fulfillment
    from app.services import trust_channel_client as channel

    path = tmp_path / "keystore.json"
    monkeypatch.setattr(main.settings, "internal_api_key", None)
    monkeypatch.setattr(main.settings, "keystore_passphrase", "test-passphrase")
    monkeypatch.setattr(main.settings, "keystore_path", str(path))
    monkeypatch.setenv("AIM_DATA_LOCK_PATH", str(tmp_path / "startup.lock"))
    crypto = DeviceCrypto(str(path), "test-passphrase")
    crypto.get_or_create_keypairs()
    ed, x = crypto.get_public_keys_b64()
    certificate = base64.b64encode(json.dumps({
        "device_id": "startup-device", "ed25519_public_key": ed, "x25519_public_key": x,
    }).encode()).decode()
    crypto.store_platform_keys(ed, x, certificate)

    client = channel.TrustChannelClient()
    monkeypatch.setattr(channel, "_client", client)
    monkeypatch.setattr(fulfillment, "_service", None)
    connected = asyncio.Event()
    socket = AsyncMock()
    socket.__aenter__.return_value = socket

    def connect(*args, **kwargs):
        connected.set()
        return socket

    # Keep the real run loop and identity loading; pause at the mocked handshake.
    async def handshake(*args):
        await asyncio.Event().wait()

    original_safe_task = main._safe_background_task

    async def only_trust_background(name, coro):
        if name == "trust_channel":
            await original_safe_task(name, coro)
        else:
            coro.close()

    activation = AsyncMock()
    processing = MagicMock()
    processing.shutdown = AsyncMock()
    processing._concurrency = 1
    created = []
    create_task = asyncio.create_task

    def tracked_task(coro, **kwargs):
        task = create_task(coro, **kwargs)
        created.append(task)
        return task

    with ExitStack() as stack:
        for target in (
            "app.main.init_db", "app.main.close_db", "app.main.ensure_log_fallback",
            "app.main.error_registry.load", "app.main.issue_tracker.reload",
            "app.main.issue_tracker.persist", "app.scripts.migrate_json.migrate_datasets_json",
        ):
            stack.enter_context(patch(target))
        stack.enter_context(patch("app.services.activation_manager.get_activation_manager", return_value=activation))
        stack.enter_context(patch("app.services.processing_queue.get_processing_queue", return_value=processing))
        stack.enter_context(patch("app.services.connectivity_state.get_connectivity_state", return_value=AsyncMock()))
        stack.enter_context(patch("app.services.stripe_connect_proxy.close_proxy_client", new_callable=AsyncMock))
        stack.enter_context(patch("app.services.aim_market_oauth.cleanup_loop", new_callable=AsyncMock))
        stack.enter_context(patch.object(main, "_safe_background_task", only_trust_background))
        stack.enter_context(patch.object(channel, "get_trust_channel_client", return_value=client))
        stack.enter_context(patch.object(fulfillment, "get_trust_channel_client", return_value=client))
        factory = stack.enter_context(patch.object(fulfillment, "get_fulfillment_service", wraps=fulfillment.get_fulfillment_service))
        register = stack.enter_context(patch.object(registration, "ensure_trust_device_registered", wraps=registration.ensure_trust_device_registered))
        connect_mock = stack.enter_context(patch.object(channel.websockets, "connect", side_effect=connect))
        handshake_mock = stack.enter_context(patch.object(client, "_handshake", side_effect=handshake))
        stack.enter_context(patch("asyncio.create_task", side_effect=tracked_task))
        try:
            async with main.lifespan(main.app):
                await asyncio.wait_for(connected.wait(), timeout=2)
                register.assert_awaited_once()
                assert register.call_args.kwargs == {}  # Startup retains default retry policy.
                factory.assert_called_once_with()
                assert "vai.fulfillment.deliver" in client._handlers
                service = fulfillment.get_fulfillment_service()
                assert service._worker_task is not None
                assert not service._worker_task.done()
                assert client._running
                connect_mock.assert_called_once()
                assert connect_mock.call_args.kwargs["additional_headers"] == {}
                handshake_mock.assert_awaited_once()
                assert handshake_mock.call_args.args[1] == "startup-device"
            assert not client._running
        finally:
            # Lifespan currently leaves artifact and fulfillment tasks untracked.
            # Clean up only the tasks created by this test, preserving other work.
            for task in created:
                if not task.done():
                    task.cancel()
            await asyncio.gather(*created, return_exceptions=True)
