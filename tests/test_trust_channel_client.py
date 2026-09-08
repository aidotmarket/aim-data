"""Independent server-side Trust Channel v1 interoperability; no backend imports."""
import asyncio
import base64
import hashlib
import json
import logging
from datetime import datetime, timedelta, timezone
from types import SimpleNamespace
from unittest.mock import AsyncMock

import pytest
import websockets
from cryptography.exceptions import InvalidSignature, InvalidTag
from cryptography.hazmat.primitives import hashes, serialization
from cryptography.hazmat.primitives.asymmetric import ed25519, padding, rsa, x25519
from cryptography.hazmat.primitives.ciphers.aead import AESGCM
from cryptography.hazmat.primitives.kdf.hkdf import HKDF

from tests.fixtures.backend_complete_ack_58a04603 import complete_ack

from app.core.crypto import DeviceCrypto
from app.services import trust_channel_client as module
from app.services.trust_channel_protocol import (
    LIMIT, TrafficSession, derive_iv, derive_keys, handshake_transcript,
)

CN = bytes(range(16))
SN = bytes(range(16, 28))
ED = ed25519.Ed25519PrivateKey.from_private_bytes(bytes(range(64, 96)))
X = x25519.X25519PrivateKey.from_private_bytes(bytes(range(32, 64)))
SERVER = x25519.X25519PrivateKey.from_private_bytes(bytes(range(32)))
DEVICE = "ed25519-test-vector"
TRANSCRIPT = (
    "61692e6d61726b6574207472757374206368616e6e656c2076312068616e647368616b65"
    "0013656432353531392d746573742d766563746f7200202543b92ff1095511476adc8369db6ddc"
    "933665a11978dda1404ee1066ca9559d0020358072d6365880d1aeea329adf9121383851ed21a"
    "28e3b75e965d0d2cd1662540010000102030405060708090a0b0c0d0e0f000c101112131415"
    "161718191a1b00208f40c5adb68f25624ae5b214ea767a6ec94d829d3d7b5e1ad1ba6f3e2138285f"
)


def b64(raw):
    return base64.b64encode(raw).decode("ascii")


def public(key):
    return key.public_key().public_bytes(serialization.Encoding.Raw, serialization.PublicFormat.Raw)


def challenge():
    return {"type": "challenge", "server_nonce": SN.hex(),
            "server_ephemeral_x25519": b64(public(SERVER)), "platform_signature": ""}


def established():
    return {"type": "established", "session_id": "00000000-0000-0000-0000-000000000779",
            "expires_at": (datetime.now(timezone.utc) + timedelta(hours=1)).isoformat()}


def server_iv(nonce, sequence, outbound):
    # Independently apply direction bit byte-wise, as specified.
    tail = bytearray(a ^ b for a, b in zip(nonce[4:], sequence.to_bytes(8, "big")))
    tail[0] = (tail[0] & 127) | (128 if outbound else 0)
    return nonce[:4] + tail


def server_keys(client_nonce, nonce=SN, private=SERVER):
    shared = private.exchange(X.public_key())
    return {d: HKDF(algorithm=hashes.SHA256(), length=32, salt=client_nonce + nonce,
                    info=("ai.market trust channel v1 aes-256-gcm " + d).encode()).derive(shared)
            for d in ("c2s", "s2c")}


def server_frame(payload, sequence, key, nonce=SN):
    encrypted = AESGCM(key).encrypt(server_iv(nonce, sequence, True), json.dumps(payload).encode(), None)
    return {"type": "data", "sequence": sequence,
            "ciphertext": b64(encrypted[:-16]), "auth_tag": b64(encrypted[-16:])}


def test_fixed_vector():
    transcript = handshake_transcript(DEVICE, public(ED), public(X), CN, SN, public(SERVER))
    assert transcript.hex() == TRANSCRIPT
    assert b64(ED.sign(transcript)) == "+XYTxBI+AxMkr16tBgM9hrfulyXNhijgL/bZeaRsw7GwKutRW2IgIJrfIXkKSQxiJjOT5OyEf4U8XBiOz6PyAQ=="
    c2s, s2c = derive_keys(X, public(SERVER), CN, SN)
    assert c2s.hex() == "306caa8554a23fd2d14fb7760830b4e9c76bcf4370892acb158bc691f1b9beb3"
    assert s2c.hex() == "1ae6d7fcfbb24712bfd9fb482f4ffe25ed1a1d93e9ab449bc631bc772069f4de"
    assert derive_iv(SN, 0, from_server=False).hex() == "101112131415161718191a1b"
    assert derive_iv(SN, 0, from_server=True).hex() == "101112139415161718191a1b"
    encrypted = AESGCM(c2s).encrypt(derive_iv(SN, 0, from_server=False), b"client payload", None)
    assert b64(encrypted[:-16]) == "vUCNCf3H6F31+Eey5q0="
    assert b64(encrypted[-16:]) == "e5IWGGsE5wgl1Obu5WNMyw=="


@pytest.mark.asyncio
async def test_response_shape(monkeypatch):
    monkeypatch.setattr(module.os, "urandom", lambda n: CN)
    ws = AsyncMock()
    ws.recv.side_effect = [json.dumps(challenge()), json.dumps(established())]
    client = module.TrustChannelClient()
    session, _ = await client._handshake(ws, DEVICE, ED, public(ED), X, public(X))
    hello, response = [json.loads(call.args[0]) for call in ws.send.call_args_list]
    assert hello == {"type": "hello", "device_id": DEVICE, "client_nonce": CN.hex()}
    assert set(response) == {"type", "device_signature"}
    assert response["type"] == "response"
    ED.public_key().verify(base64.b64decode(response["device_signature"]), bytes.fromhex(TRANSCRIPT))
    assert session.outbound == 0 and session.inbound == -1


@pytest.mark.parametrize("ephemeral", [None, "", "%%%", b64(b"short"), b64(bytes(33)), b64(bytes(32)), " " + b64(public(SERVER))])
@pytest.mark.asyncio
async def test_refuses_bad_ephemeral(ephemeral):
    msg = challenge()
    if ephemeral is None:
        del msg["server_ephemeral_x25519"]
    else:
        msg["server_ephemeral_x25519"] = ephemeral
    ws = AsyncMock()
    ws.recv.return_value = json.dumps(msg)
    with pytest.raises((ValueError, TypeError)):
        await module.TrustChannelClient()._handshake(ws, DEVICE, ED, public(ED), X, public(X))
    assert ws.send.await_count == 1  # HELLO only; no RESPONSE


@pytest.mark.parametrize("nonce", [None, "00", "gg" * 12, " " * 24])
@pytest.mark.asyncio
async def test_refuses_bad_nonce(nonce):
    ws = AsyncMock()
    ws.recv.return_value = json.dumps({**challenge(), "server_nonce": nonce})
    with pytest.raises(ValueError):
        await module.TrustChannelClient()._handshake(ws, DEVICE, ED, public(ED), X, public(X))
    assert ws.send.await_count == 1


@pytest.mark.parametrize("valid", [True, False])
@pytest.mark.asyncio
async def test_optional_trusted_rsa_key(monkeypatch, valid):
    monkeypatch.setattr(module.os, "urandom", lambda n: CN)
    signer = rsa.generate_private_key(public_exponent=65537, key_size=2048)
    msg = challenge()
    msg["server_nonce"] = SN.hex().upper()
    msg["platform_signature"] = b64(signer.sign(
        (CN.hex() + (msg["server_nonce"] if valid else "wrong")).encode(), padding.PKCS1v15(), hashes.SHA256()))
    ws = AsyncMock()
    ws.recv.side_effect = [json.dumps(msg), json.dumps(established())]
    client = module.TrustChannelClient(platform_signing_key=signer.public_key())
    if valid:
        await client._handshake(ws, DEVICE, ED, public(ED), X, public(X))
    else:
        with pytest.raises(InvalidSignature):
            await client._handshake(ws, DEVICE, ED, public(ED), X, public(X))
        assert ws.send.await_count == 1


@pytest.mark.parametrize("sequence", [-1, LIMIT, True, 0.5, "0", None])
def test_invalid_sequences(sequence):
    session = TrafficSession(*derive_keys(X, public(SERVER), CN, SN), SN)
    with pytest.raises(ValueError, match="sequence out of range"):
        session.receive({"type": "data", "sequence": sequence})
    session.outbound = sequence
    with pytest.raises(ValueError):
        session.encrypt({})


@pytest.mark.parametrize("top_bit", [0, 128])
def test_sequence_order_gaps_and_nonce_direction(top_bit):
    nonce = SN[:4] + bytes([SN[4] | top_bit]) + SN[5:]
    keys = server_keys(CN, nonce)
    session = TrafficSession(keys["c2s"], keys["s2c"], nonce)
    for seq in (0, 1, 2):
        frame = session.encrypt({"n": seq})
        assert frame["sequence"] == seq
        plain = AESGCM(keys["c2s"]).decrypt(server_iv(nonce, seq, False),
            base64.b64decode(frame["ciphertext"]) + base64.b64decode(frame["auth_tag"]), None)
        assert json.loads(plain) == {"n": seq}
        assert derive_iv(nonce, seq, from_server=True) != derive_iv(nonce, seq, from_server=False)
    for seq in (0, 3, 8):
        frame = server_frame({"n": seq}, seq, keys["s2c"], nonce)
        assert session.receive(frame) == {"n": seq}
    for seq in (8, 7):
        with pytest.raises(ValueError, match="Replayed"):
            session.receive(server_frame({}, seq, keys["s2c"], nonce))
    assert session.receive({"type": "event", "sequence": 9, "payload": {"action": "event"}}) == {"action": "event"}
    with pytest.raises(ValueError, match="Replayed"):
        session.receive(server_frame({}, 9, keys["s2c"], nonce))
    bad = server_frame({}, 10, keys["s2c"], nonce)
    bad["auth_tag"] = b64(bytes(16))
    with pytest.raises(InvalidTag):
        session.receive(bad)
    assert session.inbound == 9


@pytest.mark.parametrize("wrapped", [True, False])
def test_existing_registration_identity(tmp_path, monkeypatch, wrapped):
    crypto = DeviceCrypto(str(tmp_path / "keys.json"), "test passphrase")
    crypto._save_keys(ED, ED.public_key(), X, X.public_key())
    payload = {"device_id": "registered-string-not-hostname", "key_type": "ed25519",
               "key_fingerprint": hashlib.sha256(public(ED)).hexdigest()}
    cert = {"payload": b64(json.dumps(payload).encode()), "signature": "test", "algorithm": "Ed25519"} if wrapped else {
        "device_id": payload["device_id"], "ed25519_public_key": b64(public(ED)), "x25519_public_key": b64(public(X))}
    crypto.store_platform_keys("ed", "x", b64(json.dumps(cert).encode()))
    monkeypatch.setattr(module.settings, "keystore_path", str(crypto.keystore_path))
    monkeypatch.setattr(module.settings, "keystore_passphrase", "test passphrase")
    before = crypto.keystore_path.read_bytes()
    identity = module.TrustChannelClient()._load_identity()
    assert identity[0] == payload["device_id"]
    assert identity[2] == public(ED) and identity[4] == public(X)
    assert crypto.keystore_path.read_bytes() == before


@pytest.mark.asyncio
async def test_real_websocket_loopback(monkeypatch):
    monkeypatch.setattr(module.settings, "internal_api_key", "test-key")
    client = module.TrustChannelClient()
    monkeypatch.setattr(client, "_load_identity", lambda: (DEVICE, ED, public(ED), X, public(X)))
    finished = asyncio.get_running_loop().create_future()
    handled = asyncio.Event()
    action = {"action": "vai.fulfillment.request", "transfer_id": "test-transfer"}

    async def handler(message):
        assert message == action
        await client.send_action({"action": "vai.fulfillment.metadata", "transfer_id": "test-transfer"})
        handled.set()

    client.register_handler(action["action"], handler)

    async def server(ws):
        try:
            assert ws.request.headers["X-API-Key"] == "test-key"
            hello = json.loads(await ws.recv())
            assert hello["type"] == "hello" and hello["device_id"] == DEVICE
            nonce = bytes.fromhex(hello["client_nonce"])
            assert len(nonce) == 16
            await ws.send(json.dumps(challenge()))
            response = json.loads(await ws.recv())
            fields = (DEVICE.encode(), public(ED), public(X), nonce, SN, public(SERVER))
            signed = b"ai.market trust channel v1 handshake" + b"".join(len(v).to_bytes(2, "big") + v for v in fields)
            ED.public_key().verify(base64.b64decode(response["device_signature"]), signed)
            assert set(response) == {"type", "device_signature"}
            keys = server_keys(nonce)
            await ws.send(json.dumps(established()))
            await ws.send(json.dumps({"type": "ping"}))
            assert json.loads(await ws.recv()) == {"type": "pong"}
            await ws.send(json.dumps(server_frame(action, 0, keys["s2c"])))
            incoming = json.loads(await ws.recv())
            assert incoming["type"] == "data" and incoming["sequence"] == 0
            assert "action" not in incoming
            plain = AESGCM(keys["c2s"]).decrypt(server_iv(SN, 0, False),
                base64.b64decode(incoming["ciphertext"]) + base64.b64decode(incoming["auth_tag"]), None)
            decoded = json.loads(plain)
            assert decoded["action"] == "vai.fulfillment.metadata"
            assert decoded["request_id"]
            assert decoded["parameters"] == {"transfer_id": "test-transfer"}
            await handled.wait()
            waiter = asyncio.create_task(client.wait_for_action("vai.fulfillment.ack", "test-transfer"))
            await asyncio.sleep(0)
            ack = {"action": "vai.fulfillment.ack", "transfer_id": "test-transfer"}
            await ws.send(json.dumps({"type": "event", "sequence": 1, "payload": {"action": "unhandled"}}))
            await ws.send(json.dumps(server_frame({"success": True, "data": ack}, 3, keys["s2c"])))
            assert await asyncio.wait_for(waiter, 2) == ack
            finished.set_result(True)
        except BaseException as exc:
            if not finished.done():
                finished.set_exception(exc)
            raise

    async with websockets.serve(server, "127.0.0.1", 0) as listener:
        client._ws_url = f"ws://127.0.0.1:{listener.sockets[0].getsockname()[1]}/api/v1/trust/stream"
        with pytest.raises(websockets.exceptions.ConnectionClosedOK):
            await asyncio.wait_for(client._connect_and_listen(), 5)
        assert await finished
    assert client._ws is None and client._session is None


@pytest.mark.parametrize("code,close", [("AUTH_FAILED", 1008), ("INTERNAL_ERROR", 1011)])
@pytest.mark.parametrize("error_frame", [True, False])
@pytest.mark.asyncio
async def test_failure_backoff_and_cleanup(monkeypatch, caplog, code, close, error_frame):
    # Other suites reconfigure/disable existing loggers; isolate this log proof.
    logger = logging.Logger("trust-channel-test")
    logger.addHandler(caplog.handler)
    monkeypatch.setattr(module, "logger", logger)
    monkeypatch.setattr(module.settings, "internal_api_key", "test-key")
    client = module.TrustChannelClient()
    monkeypatch.setattr(client, "_load_identity", lambda: (DEVICE, ED, public(ED), X, public(X)))
    delays = []
    hellos = []
    original_sleep = asyncio.sleep

    async def backoff(delay):
        delays.append(delay)
        if len(delays) == 2:
            client._running = False
        await original_sleep(0)

    async def server(ws):
        hellos.append(json.loads(await ws.recv()))
        if error_frame:
            await ws.send(json.dumps({"type": "error", "code": code, "message": "test rejection"}))
        await ws.close(code=close, reason="test rejection")

    monkeypatch.setattr(module, "asyncio", SimpleNamespace(**{**vars(asyncio), "sleep": backoff}))
    async with websockets.serve(server, "127.0.0.1", 0) as listener:
        client._ws_url = f"ws://127.0.0.1:{listener.sockets[0].getsockname()[1]}"
        await asyncio.wait_for(client.run(), 5)
    assert delays == [2.0, 4.0]
    assert len(hellos) == 2 and hellos[0]["client_nonce"] != hellos[1]["client_nonce"]
    assert (code if error_frame else str(close)) in caplog.text
    assert "test rejection" in caplog.text
    assert client._ws is None and client._session is None


@pytest.mark.asyncio
async def test_send_requires_established_and_serializes(monkeypatch):
    client = module.TrustChannelClient()
    client._ws = AsyncMock()
    with pytest.raises(ConnectionError, match="not established"):
        await client.send_action({})
    client._session = TrafficSession(*derive_keys(X, public(SERVER), CN, SN), SN)
    await asyncio.gather(*(client.send_action({"n": n}) for n in range(10)))
    assert [json.loads(c.args[0])["sequence"] for c in client._ws.send.call_args_list] == list(range(10))
    client._session.outbound = LIMIT
    with pytest.raises(ConnectionError, match="exhausted"):
        await client.send_action({})
    client._ws.close.assert_awaited_once()


@pytest.mark.asyncio
async def test_send_envelopes_fulfillment_without_mutating_caller():
    client = module.TrustChannelClient()
    client._ws = AsyncMock()
    keys = server_keys(CN)
    client._session = TrafficSession(keys["c2s"], keys["s2c"], SN)
    original = {"action": "vai.fulfillment.chunk", "transfer_id": "transfer", "payload": "YQ==",
                "chunk_index": 2, "parameters": {"chunk_index": 3}, "request_id": "known"}
    await client.send_action(original)
    frame = json.loads(client._ws.send.call_args.args[0])
    plaintext = AESGCM(keys["c2s"]).decrypt(server_iv(SN, 0, False),
        base64.b64decode(frame["ciphertext"]) + base64.b64decode(frame["auth_tag"]), None)
    payload = json.loads(plaintext)
    assert payload["request_id"] == "known"
    assert payload["parameters"] == {"transfer_id": "transfer", "payload": "YQ==", "chunk_index": 2, "parameters": {"chunk_index": 3}}
    assert original["parameters"] == {"chunk_index": 3}


@pytest.mark.asyncio
async def test_failed_send_invalidates_session():
    client = module.TrustChannelClient()
    client._ws = AsyncMock()
    client._ws.send.side_effect = OSError("send failed")
    session = TrafficSession(*derive_keys(X, public(SERVER), CN, SN), SN)
    client._session = session
    with pytest.raises(OSError):
        await client.send_action({})
    assert session.outbound == 1 and client._session is None
    with pytest.raises(ConnectionError):
        await client.send_action({})


@pytest.mark.asyncio
async def test_handshake_timeout(monkeypatch):
    client = module.TrustChannelClient()
    ws = AsyncMock()
    never = asyncio.Event()
    ws.recv.side_effect = never.wait
    original_wait = asyncio.wait_for

    async def short_wait(coro, timeout):
        assert timeout == 30.0
        return await original_wait(coro, 0.01)

    monkeypatch.setattr(module, "asyncio", SimpleNamespace(**{**vars(asyncio), "wait_for": short_wait}))
    with pytest.raises(asyncio.TimeoutError):
        await client._handshake(ws, DEVICE, ED, public(ED), X, public(X))
    assert ws.send.await_count == 1


def test_missing_keystore_does_not_create_identity(tmp_path, monkeypatch):
    path = tmp_path / "absent.json"
    monkeypatch.setattr(module.settings, "keystore_path", str(path))
    monkeypatch.setattr(module.settings, "keystore_passphrase", "test")
    with pytest.raises(ConnectionError, match="registration is missing"):
        module.TrustChannelClient()._load_identity()
    assert not path.exists()


@pytest.mark.parametrize("rate_limited", [False, True])
@pytest.mark.asyncio
async def test_event_capacity_released_before_handlers_finish(monkeypatch, caplog, rate_limited):
    logger = logging.Logger("trust-channel-flow-test")
    logger.addHandler(caplog.handler)
    monkeypatch.setattr(module, "logger", logger)
    monkeypatch.setattr(module.settings, "internal_api_key", "test-key")
    client = module.TrustChannelClient()
    monkeypatch.setattr(client, "_load_identity", lambda: (DEVICE, ED, public(ED), X, public(X)))
    finished = asyncio.get_running_loop().create_future()
    all_acked = asyncio.Event()
    delivered = []
    completed = []
    connections = []
    ack_ids = []
    unacked = set()
    refused = []
    send_started = asyncio.Event()

    async def handler(message):
        delivered.append(message["n"])
        # A transport ACK must not wait for fulfillment to complete.
        await all_acked.wait()
        completed.append(message["n"])

    client.register_handler("vai.fulfillment.deliver", handler)

    async def send_action():
        send_started.set()
        await client.send_action({"action": "probe"})

    async def server(ws):
        connections.append(ws)
        sender = None
        try:
            hello = json.loads(await ws.recv())
            await ws.send(json.dumps(challenge()))
            await ws.recv()  # RESPONSE; handshake cryptography is covered above.
            keys = server_keys(bytes.fromhex(hello["client_nonce"]))
            await ws.send(json.dumps(established()))
            if rate_limited:
                rate_limit_sent_at = asyncio.get_running_loop().time()
                await ws.send(json.dumps({"type": "rate_limit", "retry_after_ms": 300}))
                # Pong proves the receive loop has processed the rate limit.
                await ws.send(json.dumps({"type": "ping"}))
                assert json.loads(await ws.recv()) == {"type": "pong"}
                sender = asyncio.create_task(send_action())
                await send_started.wait()
                with pytest.raises(asyncio.TimeoutError):
                    await asyncio.wait_for(ws.recv(), 0.05)
                assert not sender.done()

            async def send_event(n):
                if len(unacked) >= 3:
                    refused.append(n)
                    return False
                event_id = f"event-{n}"
                unacked.add(event_id)
                await ws.send(json.dumps({"type": "event", "event_id": event_id,
                    "sequence": n, "payload": {"action": "vai.fulfillment.deliver", "n": n}}))
                return True

            next_event = 0
            while next_event < 6 and await send_event(next_event):
                next_event += 1
            assert next_event == 3 and refused == [3]
            while unacked:
                ack = json.loads(await asyncio.wait_for(ws.recv(), 2))
                assert set(ack) == {"type", "event_id"} and ack["type"] == "ack"
                assert ack["event_id"] in unacked
                unacked.remove(ack["event_id"])
                ack_ids.append(ack["event_id"])
                while next_event < 6 and await send_event(next_event):
                    next_event += 1
            assert not completed
            all_acked.set()
            if sender:
                incoming = json.loads(await asyncio.wait_for(ws.recv(), 2))
                assert asyncio.get_running_loop().time() - rate_limit_sent_at >= 0.3
                assert incoming["type"] == "data" and incoming["sequence"] == 0
                plain = AESGCM(keys["c2s"]).decrypt(server_iv(SN, 0, False),
                    base64.b64decode(incoming["ciphertext"]) + base64.b64decode(incoming["auth_tag"]), None)
                assert json.loads(plain)["action"] == "probe"
                await sender
            await asyncio.gather(*list(client._handler_tasks))
            finished.set_result(True)
        except BaseException as exc:
            if not finished.done():
                finished.set_exception(exc)
            raise
        finally:
            if sender and not sender.done():
                sender.cancel()
                await asyncio.gather(sender, return_exceptions=True)

    async with websockets.serve(server, "127.0.0.1", 0) as listener:
        client._ws_url = f"ws://127.0.0.1:{listener.sockets[0].getsockname()[1]}"
        with pytest.raises(websockets.exceptions.ConnectionClosedOK):
            await asyncio.wait_for(client._connect_and_listen(), 5)
        assert await finished
    assert len(connections) == 1
    assert ack_ids == [f"event-{n}" for n in range(6)]
    assert sorted(delivered) == sorted(completed) == list(range(6))
    assert len(unacked) == 0
    assert client._send_resume_at == 0
    if rate_limited:
        assert any(r.levelno == logging.INFO and "retry_after_ms=300" in r.message
                   for r in caplog.records)


@pytest.mark.parametrize("error", [asyncio.CancelledError, OSError])
@pytest.mark.asyncio
async def test_send_preserves_error_when_close_fails(error):
    client = module.TrustChannelClient()
    ws = client._ws = AsyncMock()
    client._session = TrafficSession(*derive_keys(X, public(SERVER), CN, SN), SN)
    ws.send.side_effect = error("original send error")
    ws.close.side_effect = RuntimeError("close failed")
    with pytest.raises(error, match="original send error"):
        await client.send_action({})
    assert client._session is None
    ws.close.assert_awaited_once()


@pytest.mark.asyncio
@pytest.mark.parametrize("reply", [
    complete_ack("s3-request"),
    {"request_id": "s3-request", "success": False, "error": "not permitted"},
    {"request_id": "s3-request", "type": "error", "error": "rejected"},
    {"type": "error", "error": "uncorrelated failure"},
])
async def test_encrypted_response_waiter_preserves_envelope_and_surfaces_errors(reply):
    client = module.TrustChannelClient()
    client._session = TrafficSession(*derive_keys(X, public(SERVER), CN, SN), SN)
    async def immediate_reply(message):
        # Reply arrives during send, before send_action returns.
        unrelated = dict(reply, request_id="unrelated")
        client._dispatch(client._session.receive(server_frame(unrelated, 0, server_keys(CN)["s2c"])))
        assert not client._waiters[":s3-request"].done()
        client._dispatch(client._session.receive(server_frame(reply, 1, server_keys(CN)["s2c"])))
    client.send_action = immediate_reply
    if reply.get("error"):
        with pytest.raises(ConnectionError, match="rejected"):
            await client.wait_for_action("", "s3-request", timeout=0.1, message={})
    else:
        assert await client.wait_for_action("", "s3-request", timeout=0.1, message={}) == reply
    assert not client._waiters
