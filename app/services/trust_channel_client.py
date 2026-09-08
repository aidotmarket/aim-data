"""
Trust Channel WebSocket Client
==============================

Maintains a persistent WebSocket connection to ai.market's Trust Channel.
Dispatches incoming actions to registered handler functions.

BQ-D1: Fulfillment Listener (AIM Data side)

The Trust Channel is the encrypted bidirectional communication channel
between AIM Data instances and the ai.market platform. Messages are
encrypted data envelopes carrying JSON actions; event frames remain plaintext.

Connection lifecycle:
  1. Connect to ws://{ai_market_url}/api/v1/trust/stream
  2. Authenticate with internal API key
  3. Perform Ed25519/X25519 handshake and dispatch AES-GCM decrypted actions
  4. Reconnect with exponential backoff on disconnect
"""

import asyncio
import base64
import hashlib
import os
from contextlib import suppress
from datetime import datetime, timezone
from uuid import uuid4
import json
import logging
import math
from typing import Any, Awaitable, Callable, Dict, Optional

import websockets
from websockets.exceptions import ConnectionClosed, ConnectionClosedError

try:
    from websockets.exceptions import InvalidStatusCode
except ImportError:
    # websockets >= 14 moved this; fall back to generic
    InvalidStatusCode = ConnectionClosedError  # type: ignore[misc,assignment]

from cryptography.hazmat.primitives import hashes, serialization
from cryptography.hazmat.primitives.asymmetric import padding, rsa

from app.config import settings
from app.core.crypto import DeviceCrypto
from app.services.trust_channel_protocol import (
    LIMIT, TrafficSession, decode_b64, derive_keys, handshake_transcript,
)

logger = logging.getLogger(__name__)

# Type alias for action handlers: async fn(params: dict) -> None
ActionHandler = Callable[[Dict[str, Any]], Awaitable[None]]

# Reconnect backoff
_INITIAL_BACKOFF_S = 2.0
_MAX_BACKOFF_S = 60.0
_BACKOFF_MULTIPLIER = 2.0


class _RateLimited(ConnectionError):
    """The server rejected traffic before decrypting its request ID."""


class TrustChannelClient:
    """
    WebSocket client that connects to ai.market's Trust Channel
    and dispatches incoming actions to registered handlers.
    """

    def __init__(self, *, platform_signing_key: rsa.RSAPublicKey | None = None) -> None:
        self._handlers: Dict[str, ActionHandler] = {}
        self._ws: Optional[Any] = None  # websockets connection
        self._session: TrafficSession | None = None
        self._platform_signing_key = platform_signing_key
        self._handler_tasks: set[asyncio.Task] = set()
        self._running = False
        self._send_lock = asyncio.Lock()
        self._send_resume_at = 0.0
        self._waiters: Dict[str, asyncio.Future] = {}
        self._response_waiters: set[asyncio.Future] = set()
        # Build WS URL from ai_market_url (http → ws, https → wss)
        base = settings.ai_market_url.rstrip("/")
        if base.startswith("https://"):
            self._ws_url = base.replace("https://", "wss://") + "/api/v1/trust/stream"
        elif base.startswith("http://"):
            self._ws_url = base.replace("http://", "ws://") + "/api/v1/trust/stream"
        else:
            self._ws_url = "wss://" + base + "/api/v1/trust/stream"

    def register_handler(self, action: str, handler: ActionHandler) -> None:
        """Register a handler for a specific action type."""
        if action in self._handlers:
            logger.warning("Overwriting existing handler for action: %s", action)
        self._handlers[action] = handler
        logger.info("Registered Trust Channel handler: %s", action)

    async def send_action(self, message: Dict[str, Any]) -> None:
        """Envelope and encrypt an application action after ESTABLISHED."""
        async with self._send_lock:
            ws, session = self._ws, self._session
            if ws is None or session is None:
                raise ConnectionError("Trust Channel not established")
            loop = asyncio.get_running_loop()
            while self._send_resume_at > loop.time():
                await asyncio.sleep(self._send_resume_at - loop.time())
            if self._ws is not ws or self._session is not session:
                raise ConnectionError("Trust Channel disconnected while rate limited")
            if session.outbound >= LIMIT:
                await ws.close(code=1000, reason="Sequence exhausted; fresh handshake required")
                raise ConnectionError("Trust Channel sequence exhausted")
            try:
                payload = dict(message)
                payload.setdefault("request_id", str(uuid4()))
                if payload.get("action") in {
                    "vai.fulfillment.metadata", "vai.fulfillment.chunk",
                    "vai.fulfillment.complete", "vai.fulfillment.error",
                    "vai.fulfillment.response",
                }:
                    # Backend 58a04603 trust_websocket.py:1241,1249-1253
                    # spreads request.parameters into the fulfillment model.
                    # Preserve the caller's nested parameters, not a flat merge.
                    payload["parameters"] = {k: v for k, v in message.items() if k not in (
                        "action", "request_id", "action_version", "hitl_token"
                    )}
                if payload.get("action") == "vai.fulfillment.response":
                    payload["parameters"]["request_id"] = payload["request_id"]
                await ws.send(json.dumps(session.encrypt(payload)))
            except asyncio.CancelledError:
                self._session = None
                with suppress(Exception):
                    await ws.close()
                raise
            except Exception:
                # Do not allow a possibly partial send to continue on this session.
                self._session = None
                with suppress(Exception):
                    await ws.close()
                raise

    async def wait_for_action(
        self, action: str, transfer_id: str, timeout: float = 30.0,
        *, message: Optional[Dict[str, Any]] = None
    ) -> Dict[str, Any]:
        """
        Wait for a specific action+transfer_id message from the server.
        For action-less responses, use an empty action and request_id as the key.
        Optional message is sent after registering the waiter to avoid fast-reply races.

        Returns the parsed message dict, or raises TimeoutError.
        """
        waiter_key = f"{action}:{transfer_id}"
        is_response = message is not None and message.get("action") == "vai.fulfillment.response"
        for attempt in range(2 if is_response else 1):
            future = asyncio.get_running_loop().create_future()
            self._waiters[waiter_key] = future
            try:
                if is_response:
                    self._response_waiters.add(future)
                if message is not None:
                    await self.send_action(message)
                return await asyncio.wait_for(future, timeout=timeout)
            except _RateLimited:
                if attempt:
                    raise
                # send_action observes the server's pause before this one resend.
            except asyncio.TimeoutError:
                raise TimeoutError(f"Timed out waiting for {action} (transfer_id={transfer_id})")
            finally:
                self._response_waiters.discard(future)
                self._waiters.pop(waiter_key, None)

    def _handle_rate_limit(self, frame):
        delay = frame.get("retry_after_ms")
        if type(delay) not in (int, float) or not math.isfinite(delay) or delay < 0:
            return
        self._send_resume_at = max(
            self._send_resume_at, asyncio.get_running_loop().time() + delay / 1000,
        )
        logger.info("Trust Channel rate limited; retry_after_ms=%s", delay)
        # Pre-decryption controls have no request ID. Fulfillment is sequential;
        # only pending S3 response waiters opt into a single bounded replay.
        for future in self._response_waiters:
            if not future.done():
                future.set_exception(_RateLimited("Trust Channel rate limited delivery"))

    async def run(self) -> None:
        """
        Main connection loop with automatic reconnection.
        Call this as an asyncio task during app lifespan.
        """
        self._running = True
        backoff = _INITIAL_BACKOFF_S

        while self._running:
            try:
                await self._connect_and_listen()
                # If _connect_and_listen returns normally, reset backoff
                backoff = _INITIAL_BACKOFF_S
            except (ConnectionClosed, ConnectionClosedError, OSError) as e:
                logger.warning("Trust Channel disconnected code=%s reason=%s: %s",
                               getattr(e, "code", None), getattr(e, "reason", None), e)
            except InvalidStatusCode as e:
                logger.error("Trust Channel connection rejected (HTTP %s)", e.status_code)
                if e.status_code in (401, 403):
                    logger.error("Auth failure — check VECTORAIZ_INTERNAL_API_KEY")
                    # Don't retry rapidly on auth failures
                    backoff = min(backoff * _BACKOFF_MULTIPLIER, _MAX_BACKOFF_S)
            except Exception as e:
                logger.error("Trust Channel unexpected error: %s", e, exc_info=True)

            if not self._running:
                break

            logger.info("Reconnecting to Trust Channel in %.0fs...", backoff)
            await asyncio.sleep(backoff)
            backoff = min(backoff * _BACKOFF_MULTIPLIER, _MAX_BACKOFF_S)

    async def _connect_and_listen(self) -> None:
        """Connect, authenticate, and enter the message dispatch loop."""
        api_key = settings.internal_api_key
        if not api_key:
            logger.error("Cannot connect to Trust Channel — no VECTORAIZ_INTERNAL_API_KEY")
            raise ConnectionError("No API key for Trust Channel")

        # Read the existing registration snapshot; never create or rotate keys here.
        device_id, ed_private, ed_public, x_private, x_public = await asyncio.to_thread(
            self._load_identity
        )
        headers = {"X-API-Key": api_key}
        logger.info("Connecting to Trust Channel: %s", self._ws_url)
        try:
            async with websockets.connect(
                self._ws_url,
                additional_headers=headers,
                ping_interval=30,
                ping_timeout=10,
                max_size=2 * 1024 * 1024,
            ) as ws:
                self._ws = ws
                session, established = await self._handshake(
                    ws, device_id, ed_private, ed_public, x_private, x_public
                )
                self._session = session
                expires = datetime.fromisoformat(established["expires_at"].replace("Z", "+00:00"))
                lifetime = (expires - datetime.now(timezone.utc)).total_seconds()
                deadline = asyncio.get_running_loop().time() + min(lifetime, 3600)
                logger.info("Trust Channel established: %s", established["session_id"])
                while True:
                    remaining = deadline - asyncio.get_running_loop().time()
                    if remaining <= 0:
                        raise ConnectionError("Trust Channel session expired")
                    raw_message = await asyncio.wait_for(ws.recv(), timeout=remaining)
                    frame = self._parse_frame(raw_message)
                    self._check_error(frame)
                    frame_type = frame.get("type")
                    if frame_type == "ping":
                        await ws.send(json.dumps({"type": "pong"}))
                        continue
                    if frame_type == "rate_limit":
                        self._handle_rate_limit(frame)
                        continue
                    if frame_type not in ("data", "event"):
                        continue
                    message = session.receive(frame)
                    self._dispatch(message)
                    if frame_type == "event" and "event_id" in frame:
                        await ws.send(json.dumps({"type": "ack", "event_id": frame["event_id"]}))
        finally:
            self._send_resume_at = 0.0
            self._session = None
            self._ws = None
            tasks = list(self._handler_tasks)
            for task in tasks:
                task.cancel()
            if tasks:
                await asyncio.gather(*tasks, return_exceptions=True)
            for future in self._waiters.values():
                if not future.done():
                    future.set_exception(ConnectionError("Trust Channel disconnected"))

    def _load_identity(self):
        if not settings.keystore_passphrase:
            raise ConnectionError("Trust Channel keystore passphrase is not configured")
        crypto = DeviceCrypto(settings.keystore_path, settings.keystore_passphrase)
        keystore = crypto._read_keystore()
        if not keystore or not keystore.get("certificate"):
            raise ConnectionError("Trust Channel device registration is missing")
        certificate = json.loads(decode_b64(keystore["certificate"]))
        if "payload" in certificate:
            certificate = json.loads(decode_b64(certificate["payload"]))
        device_id = certificate.get("device_id")
        if not isinstance(device_id, str) or not device_id or len(device_id.encode("utf-8")) > 65535:
            raise ValueError("Invalid registered device identifier")
        ed_private, ed_public, x_private, x_public = crypto._load_keys(keystore)
        ed_raw = ed_public.public_bytes(serialization.Encoding.Raw, serialization.PublicFormat.Raw)
        x_raw = x_public.public_bytes(serialization.Encoding.Raw, serialization.PublicFormat.Raw)
        if "key_fingerprint" in certificate:
            if certificate.get("key_type") != "ed25519" or certificate["key_fingerprint"] != hashlib.sha256(ed_raw).hexdigest():
                raise ValueError("Registration certificate and device keys disagree")
        else:
            for name, raw in (("ed25519_public_key", ed_raw), ("x25519_public_key", x_raw)):
                if decode_b64(certificate.get(name), 32) != raw:
                    raise ValueError("Registration certificate and device keys disagree")
        return device_id, ed_private, ed_raw, x_private, x_raw

    @staticmethod
    def _parse_frame(raw):
        frame = json.loads(raw)
        if not isinstance(frame, dict):
            raise ValueError("Trust Channel frame must be an object")
        return frame

    @staticmethod
    def _check_error(frame):
        if frame.get("type") == "error":
            code, reason = frame.get("code"), frame.get("message")
            logger.warning("Trust Channel error code=%s reason=%s", code, reason)
            raise ConnectionError(f"Trust Channel {code}: {reason}")

    async def _handshake(self, ws, device_id, ed_private, ed_public, x_private, x_public):
        client_nonce = os.urandom(16)
        async def receive(expected):
            frame = self._parse_frame(await ws.recv())
            self._check_error(frame)
            if frame.get("type") != expected:
                raise ValueError(f"Expected Trust Channel {expected}")
            return frame

        # One bounded handshake including challenge validation and RESPONSE send.
        async def exchange():
            await ws.send(json.dumps({"type": "hello", "device_id": device_id,
                                      "client_nonce": client_nonce.hex()}))
            challenge = await receive("challenge")
            nonce_hex = challenge.get("server_nonce")
            if not isinstance(nonce_hex, str) or len(nonce_hex) != 24:
                raise ValueError("Server nonce must be 12 bytes of hex")
            nonce = bytes.fromhex(nonce_hex)
            if len(nonce) != 12 or nonce.hex() != nonce_hex.lower():
                raise ValueError("Invalid server nonce")
            ephemeral = decode_b64(challenge.get("server_ephemeral_x25519"), 32)
            if self._platform_signing_key is not None:
                self._platform_signing_key.verify(
                    decode_b64(challenge.get("platform_signature")),
                    (client_nonce.hex() + nonce_hex).encode("ascii"),
                    padding.PKCS1v15(), hashes.SHA256(),
                )
            # Current keystore has no trusted RSA signing key. WSS authenticates
            # the server; never trust a key supplied by the challenge itself.
            transcript = handshake_transcript(
                device_id, ed_public, x_public, client_nonce, nonce, ephemeral
            )
            c2s, s2c = derive_keys(x_private, ephemeral, client_nonce, nonce)
            await ws.send(json.dumps({"type": "response", "device_signature":
                                      base64.b64encode(ed_private.sign(transcript)).decode("ascii")}))
            established = await receive("established")
            if not established.get("session_id") or not isinstance(established.get("expires_at"), str):
                raise ValueError("Malformed ESTABLISHED frame")
            return TrafficSession(c2s, s2c, nonce), established

        return await asyncio.wait_for(exchange(), timeout=30.0)

    def _dispatch(self, message):
        # Preserve the envelope: response delivery is correlated by request_id,
        # and outer execution success alone does not mean delivery succeeded.
        if not message.get("action"):
            request_id = message.get("request_id")
            failed = message.get("error") or message.get("type") == "error" or message.get("success") is False
            if failed:
                for key, future in self._waiters.items():
                    if not future.done() and (not request_id or key == f":{request_id}"):
                        future.set_exception(ConnectionError("Trust Channel rejected delivery"))
                return
            future = self._waiters.get(f":{request_id}") if request_id else None
            if future is not None and not future.done():
                future.set_result(message)
                return
            if isinstance(message.get("data"), dict):
                message = message["data"]
        action = message.get("action", "")
        transfer_id = message.get("transfer_id", "")
        waiter_key = f"{action}:{transfer_id}"
        if waiter_key in self._waiters and not self._waiters[waiter_key].done():
            self._waiters[waiter_key].set_result(message)
            return
        handler = self._handlers.get(action)
        if handler:
            task = asyncio.create_task(self._safe_handle(action, handler, message))
            self._handler_tasks.add(task)
            task.add_done_callback(self._handler_tasks.discard)
        else:
            logger.debug("No handler for Trust Channel action: %s", action)

    async def _safe_handle(
        self, action: str, handler: ActionHandler, message: Dict[str, Any]
    ) -> None:
        """Run a handler with error isolation."""
        try:
            await handler(message)
        except Exception as e:
            logger.error(
                "Handler for %s raised: %s", action, e, exc_info=True
            )

    async def stop(self) -> None:
        """Gracefully stop the client."""
        self._running = False
        if self._ws is not None:
            await self._ws.close()
            self._ws = None
        # Cancel any pending waiters
        for future in self._waiters.values():
            if not future.done():
                future.cancel()
        logger.info("Trust Channel client stopped")


# Module-level singleton
_client: Optional[TrustChannelClient] = None


def get_trust_channel_client() -> TrustChannelClient:
    """Get or create the Trust Channel client singleton."""
    global _client
    if _client is None:
        _client = TrustChannelClient()
    return _client
