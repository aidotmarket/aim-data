"""Authenticated MCP 1.8.1 legacy SSE transport.

The SDK owns JSON-RPC framing.  This guard owns the AIM Data request-time
enable gate, Bearer validation, and exact GET-session/POST-token binding.
"""

from __future__ import annotations

import asyncio
import json
import logging
import re
import time
import uuid
from dataclasses import dataclass
from datetime import datetime, timezone
from typing import Any, Awaitable, Callable, Dict, List, Optional, Sequence, Tuple
from urllib.parse import parse_qsl

from fastapi import FastAPI

from app.services.connectivity_state import get_connectivity_state
from app.services.query_orchestrator import ConnectivityError, get_query_orchestrator

logger = logging.getLogger(__name__)

MAX_MCP_CONNECTIONS = 32
FIRST_FRAME_TIMEOUT_SECONDS = 10.0
MAX_FIRST_FRAME_BYTES = 4096
MAX_BINDING_SECONDS = 3600.0
WATCHDOG_SECONDS = 60.0
_ENDPOINT_FRAME_RE = re.compile(
    rb"\Aevent: endpoint\r?\ndata: (/mcp/messages/\?session_id=([0-9a-f]{32}))\r?\n\r?\n\Z"
)


@dataclass
class SessionReservation:
    nonce: str
    owner_task: asyncio.Task[Any]
    created_at: float
    first_frame_deadline: float


@dataclass
class SessionBinding:
    token_id: str
    created_at: float
    last_validated_activity: float
    deadline: float
    owner_task: asyncio.Task[Any]
    closing: bool = False
    enqueueable: bool = True


class _FirstFrameRejected(RuntimeError):
    pass


def _json_message(status: int, error: str, *, bearer: bool = False) -> List[dict]:
    body = json.dumps({"error": error}, separators=(",", ":")).encode("utf-8")
    headers = [(b"content-type", b"application/json"), (b"content-length", str(len(body)).encode())]
    if bearer:
        headers.append((b"www-authenticate", b"Bearer"))
    return [
        {"type": "http.response.start", "status": status, "headers": headers},
        {"type": "http.response.body", "body": body},
    ]


async def _send_error(send: Callable[[dict], Awaitable[None]], status: int, error: str, *, bearer: bool = False) -> None:
    for message in _json_message(status, error, bearer=bearer):
        await send(message)


def _authorization_header(scope: dict) -> Optional[str]:
    values = [value for name, value in scope.get("headers", []) if name.lower() == b"authorization"]
    if len(values) != 1:
        return None
    try:
        value = values[0].decode("ascii")
    except UnicodeDecodeError:
        return None
    parts = value.split(" ")
    if len(parts) != 2 or parts[0].lower() != "bearer" or not parts[1] or "," in parts[1]:
        return None
    return parts[1]


def _query_items(scope: dict) -> List[Tuple[str, str]]:
    try:
        return parse_qsl(scope.get("query_string", b"").decode("ascii"), keep_blank_values=True)
    except (UnicodeDecodeError, ValueError):
        return [("", "")]


def _token_deadline(expires_at: Optional[datetime], now_wall: float, now_mono: float) -> float:
    deadline = now_mono + MAX_BINDING_SECONDS
    if expires_at is not None:
        if expires_at.tzinfo is None:
            expires_at = expires_at.replace(tzinfo=timezone.utc)
        seconds = max(0.0, expires_at.timestamp() - now_wall)
        deadline = min(deadline, now_mono + seconds)
    return deadline


class AuthenticatedMcpSse:
    """ASGI guard around the pinned FastMCP ``sse_app``."""

    def __init__(self, sdk_app: Optional[Callable[..., Awaitable[None]]]) -> None:
        self._sdk_app = sdk_app
        self._registry_lock = asyncio.Lock()
        self._reservations: Dict[str, SessionReservation] = {}
        self._bindings: Dict[str, SessionBinding] = {}
        self._state = get_connectivity_state()
        self._state.set_transport_available(sdk_app is not None)
        self._state.register_close_hook(self.detach_all)

    @property
    def transport_available(self) -> bool:
        return self._sdk_app is not None

    def registry_snapshot(self) -> dict:
        """Return non-secret lifecycle metadata for diagnostics/tests."""
        return {
            "reservations": {
                nonce: {
                    "created_at": value.created_at,
                    "first_frame_deadline": value.first_frame_deadline,
                    "owner_done": value.owner_task.done(),
                }
                for nonce, value in self._reservations.items()
            },
            "bindings": {
                session_id: {
                    "token_id": value.token_id,
                    "created_at": value.created_at,
                    "last_validated_activity": value.last_validated_activity,
                    "deadline": value.deadline,
                    "owner_done": value.owner_task.done(),
                    "closing": value.closing,
                    "enqueueable": value.enqueueable,
                }
                for session_id, value in self._bindings.items()
            },
        }

    async def __call__(self, scope: dict, receive: Callable, send: Callable) -> None:
        if scope.get("type") != "http":
            await _send_error(send, 503, "mcp_unavailable")
            return
        if self._sdk_app is None:
            await _send_error(send, 503, "mcp_unavailable")
            return
        readiness = self._state.readiness()
        if not readiness.enabled:
            error = "mcp_disabled" if readiness.reason == "disabled" else "mcp_unavailable"
            await _send_error(send, 503, error)
            return

        path = scope.get("path", "")
        root_path = scope.get("root_path", "")
        if root_path and path.startswith(root_path):
            path = path[len(root_path):] or "/"
        method = scope.get("method", "").upper()
        if method == "GET" and path == "/sse":
            await self._handle_get(scope, receive, send)
            return
        if method == "POST" and path == "/messages/":
            await self._handle_post(scope, receive, send)
            return
        await _send_error(send, 404, "not_found")

    def _validate(self, raw_token: str):
        return get_query_orchestrator().validate_token(raw_token)

    async def _handle_get(self, scope: dict, receive: Callable, send: Callable) -> None:
        if _query_items(scope):
            await _send_error(send, 401, "unauthorized", bearer=True)
            return
        raw_token = _authorization_header(scope)
        if raw_token is None:
            await _send_error(send, 401, "unauthorized", bearer=True)
            return
        try:
            token = self._validate(raw_token)
        except ConnectivityError:
            await _send_error(send, 401, "unauthorized", bearer=True)
            return
        except Exception:
            await _send_error(send, 503, "mcp_unavailable")
            return

        owner = asyncio.current_task()
        if owner is None:
            await _send_error(send, 503, "mcp_unavailable")
            return
        now = time.monotonic()
        nonce = uuid.uuid4().hex
        close_actions: Sequence[Callable[[], None]]
        async with self._registry_lock:
            close_actions = self._sweep_locked(now)
            if not self._state.enabled:
                admission_error = "mcp_disabled"
                admitted = False
            elif len(self._reservations) + len(self._bindings) >= MAX_MCP_CONNECTIONS:
                admission_error = "mcp_unavailable"
                admitted = False
            else:
                self._reservations[nonce] = SessionReservation(
                    nonce=nonce,
                    owner_task=owner,
                    created_at=now,
                    first_frame_deadline=now + FIRST_FRAME_TIMEOUT_SECONDS,
                )
                admission_error = None
                admitted = True
        self._run_close_actions(close_actions)
        if not admitted:
            await _send_error(send, 503, admission_error or "mcp_unavailable")
            return

        from app.mcp_server import (
            http_auth_failure_callback,
            http_bearer_context,
            http_execution_context,
        )

        context_token = http_bearer_context.set(raw_token)
        execution_token = http_execution_context.set(True)
        promoted = asyncio.Event()
        promoted_session: Optional[str] = None
        buffered: List[dict] = []
        first_frame = bytearray()
        response_started = False

        def close_on_tool_auth_failure() -> None:
            asyncio.create_task(
                self._close_owned_connection(nonce, lambda: promoted_session)
            )

        failure_callback_token = http_auth_failure_callback.set(close_on_tool_auth_failure)

        async def guarded_send(message: dict) -> None:
            nonlocal promoted_session, response_started
            if promoted.is_set():
                response_started = True
                await send(message)
                return
            buffered.append(message)
            if message.get("type") == "http.response.body":
                first_frame.extend(message.get("body", b""))
                if len(first_frame) > MAX_FIRST_FRAME_BYTES:
                    raise _FirstFrameRejected()
                marker = b"\r\n\r\n" if b"\r\n\r\n" in first_frame else b"\n\n"
                if marker not in first_frame:
                    return
                frame, remainder = bytes(first_frame).split(marker, 1)
                complete_frame = frame + marker
                match = _ENDPOINT_FRAME_RE.fullmatch(complete_frame)
                if match is None or remainder:
                    raise _FirstFrameRejected()
                session_id = match.group(2).decode("ascii")
                promoted_session = session_id
                now_mono = time.monotonic()
                async with self._registry_lock:
                    reservation = self._reservations.get(nonce)
                    if (
                        reservation is None
                        or reservation.owner_task is not owner
                        or now_mono > reservation.first_frame_deadline
                        or not self._state.enabled
                    ):
                        raise _FirstFrameRejected()
                    del self._reservations[nonce]
                    self._bindings[session_id] = SessionBinding(
                        token_id=token.id,
                        created_at=now_mono,
                        last_validated_activity=now_mono,
                        deadline=_token_deadline(token.expires_at, time.time(), now_mono),
                        owner_task=owner,
                    )
                promoted.set()
                for pending in buffered:
                    response_started = True
                    await send(pending)
                buffered.clear()

        watchdog = asyncio.create_task(
            self._watchdog(nonce, lambda: promoted_session, promoted, raw_token, owner)
        )
        try:
            await self._sdk_app(scope, receive, guarded_send)
        except _FirstFrameRejected:
            if not response_started:
                await _send_error(send, 503, "mcp_unavailable")
        finally:
            watchdog.cancel()
            try:
                await watchdog
            except asyncio.CancelledError:
                pass
            async with self._registry_lock:
                self._reservations.pop(nonce, None)
                if promoted_session is not None:
                    binding = self._bindings.get(promoted_session)
                    if binding is not None and binding.owner_task is owner:
                        self._bindings.pop(promoted_session, None)
            http_auth_failure_callback.reset(failure_callback_token)
            http_execution_context.reset(execution_token)
            http_bearer_context.reset(context_token)

    async def _handle_post(self, scope: dict, receive: Callable, send: Callable) -> None:
        items = _query_items(scope)
        if len(items) != 1 or items[0][0] != "session_id" or not re.fullmatch(r"[0-9a-f]{32}", items[0][1]):
            await _send_error(send, 401, "unauthorized", bearer=True)
            return
        session_id = items[0][1]
        raw_token = _authorization_header(scope)
        if raw_token is None:
            await _send_error(send, 401, "unauthorized", bearer=True)
            return

        async with self._state.enqueue_gate:
            if not self._state.enabled:
                await _send_error(send, 503, "mcp_disabled")
                return
            try:
                token = self._validate(raw_token)
            except ConnectivityError as exc:
                if exc.code in {"auth_revoked", "auth_expired"}:
                    await self._detach_session_if_token(session_id, raw_token)
                await _send_error(send, 401, "unauthorized", bearer=True)
                return
            except Exception:
                close_actions = await self._detach_session(session_id)
                self._run_close_actions(close_actions)
                await _send_error(send, 503, "mcp_unavailable")
                return

            now = time.monotonic()
            async with self._registry_lock:
                close_actions = self._sweep_locked(now)
                binding = self._bindings.get(session_id)
                allowed = bool(
                    binding is not None
                    and binding.token_id == token.id
                    and binding.enqueueable
                    and not binding.closing
                    and binding.deadline > now
                    and self._state.enabled
                )
                if allowed:
                    binding.last_validated_activity = now
                    # The SDK's zero-buffer send completes only after the GET-owned
                    # server task receives this message, so the locked call is the
                    # final enqueue decision and enqueue itself.
                    await self._sdk_app(scope, receive, send)
            self._run_close_actions(close_actions)
            if not allowed:
                await _send_error(send, 401, "unauthorized", bearer=True)

    async def _watchdog(
        self,
        nonce: str,
        session_id_getter: Callable[[], Optional[str]],
        promoted: asyncio.Event,
        raw_token: str,
        owner: asyncio.Task[Any],
    ) -> None:
        try:
            await asyncio.wait_for(promoted.wait(), timeout=FIRST_FRAME_TIMEOUT_SECONDS)
            while True:
                session_id = session_id_getter()
                if session_id is None:
                    return
                expired = False
                async with self._registry_lock:
                    binding = self._bindings.get(session_id)
                    if binding is None or binding.owner_task is not owner:
                        return
                    remaining = binding.deadline - time.monotonic()
                    if remaining <= 0:
                        expired = True
                        delay = 0.0
                    else:
                        delay = min(WATCHDOG_SECONDS, remaining)
                if expired:
                    close_actions = await self._detach_session(session_id)
                    self._run_close_actions(close_actions)
                    return
                await asyncio.sleep(delay)
                try:
                    token = self._validate(raw_token)
                except Exception:
                    close_actions = await self._detach_session(session_id)
                    self._run_close_actions(close_actions)
                    return
                now = time.monotonic()
                async with self._registry_lock:
                    binding = self._bindings.get(session_id)
                    if binding is None or binding.owner_task is not owner:
                        return
                    binding.last_validated_activity = now
                    binding.deadline = min(binding.deadline, _token_deadline(token.expires_at, time.time(), now))
        except asyncio.TimeoutError:
            close_actions = await self._detach_reservation(nonce)
            self._run_close_actions(close_actions)

    async def _close_owned_connection(
        self,
        nonce: str,
        session_id_getter: Callable[[], Optional[str]],
    ) -> None:
        session_id = session_id_getter()
        if session_id is None:
            actions = await self._detach_reservation(nonce)
        else:
            actions = await self._detach_session(session_id)
        self._run_close_actions(actions)

    async def _detach_reservation(self, nonce: str) -> Sequence[Callable[[], None]]:
        async with self._registry_lock:
            reservation = self._reservations.pop(nonce, None)
            if reservation is None:
                return ()
            return (self._cancel_action(reservation.owner_task),)

    async def _detach_session(self, session_id: str) -> Sequence[Callable[[], None]]:
        async with self._registry_lock:
            binding = self._bindings.pop(session_id, None)
            if binding is None:
                return ()
            binding.closing = True
            binding.enqueueable = False
            return (self._cancel_action(binding.owner_task),)

    async def _detach_session_if_token(self, session_id: str, raw_token: str) -> None:
        from app.services.connectivity_token_service import ConnectivityTokenError, parse_token

        try:
            token_id, _ = parse_token(raw_token)
        except ConnectivityTokenError:
            return
        async with self._registry_lock:
            binding = self._bindings.get(session_id)
            if binding is None or binding.token_id != token_id:
                return
            self._bindings.pop(session_id, None)
            binding.closing = True
            binding.enqueueable = False
            close = self._cancel_action(binding.owner_task)
        close()

    async def detach_all(self) -> Sequence[Callable[[], None]]:
        """Detach everything under the registry lock; caller closes afterward."""
        async with self._registry_lock:
            actions = [self._cancel_action(value.owner_task) for value in self._reservations.values()]
            for binding in self._bindings.values():
                binding.closing = True
                binding.enqueueable = False
                actions.append(self._cancel_action(binding.owner_task))
            self._reservations.clear()
            self._bindings.clear()
            return actions

    def _sweep_locked(self, now: float) -> Sequence[Callable[[], None]]:
        actions: List[Callable[[], None]] = []
        for nonce, reservation in list(self._reservations.items()):
            if reservation.owner_task.done() or reservation.first_frame_deadline <= now:
                self._reservations.pop(nonce, None)
                if not reservation.owner_task.done():
                    actions.append(self._cancel_action(reservation.owner_task))
        for session_id, binding in list(self._bindings.items()):
            if binding.owner_task.done() or binding.deadline <= now:
                self._bindings.pop(session_id, None)
                binding.closing = True
                binding.enqueueable = False
                if not binding.owner_task.done():
                    actions.append(self._cancel_action(binding.owner_task))
        return actions

    @staticmethod
    def _cancel_action(task: asyncio.Task[Any]) -> Callable[[], None]:
        return task.cancel

    @staticmethod
    def _run_close_actions(actions: Sequence[Callable[[], None]]) -> None:
        for action in actions:
            try:
                action()
            except Exception:
                logger.warning("MCP connection close failed: close_error")


_mounted_guard: Optional[AuthenticatedMcpSse] = None


def get_mcp_guard() -> Optional[AuthenticatedMcpSse]:
    return _mounted_guard


def mount_mcp_sse(app: FastAPI) -> AuthenticatedMcpSse:
    """Construct the pinned legacy SSE app once and mount a fail-closed guard."""
    global _mounted_guard
    sdk_app = None
    try:
        from app.mcp_server import MCP_AVAILABLE, mcp_server

        if MCP_AVAILABLE and mcp_server is not None:
            sdk_app = mcp_server.sse_app(mount_path="/mcp")
    except Exception:
        logger.error("MCP SSE transport unavailable: construction_failed")

    guard = AuthenticatedMcpSse(sdk_app)
    _mounted_guard = guard
    app.mount("/mcp", guard)
    if guard.transport_available:
        logger.info("MCP SSE routes constructed at /mcp/sse and /mcp/messages/")
    else:
        logger.warning("MCP SSE routes installed with unavailable fallback")
    return guard
