"""Persistent, fail-closed runtime state for external connectivity.

The environment flag seeds a fresh install only.  Once the operator toggles
connectivity, the small JSON state file is authoritative across restarts.
"""

from __future__ import annotations

import asyncio
import json
import logging
import os
import tempfile
from dataclasses import dataclass
from pathlib import Path
from typing import Awaitable, Callable, Optional, Sequence

from app.config import settings

logger = logging.getLogger(__name__)

CloseAction = Callable[[], None]
CloseHook = Callable[[], Awaitable[Sequence[CloseAction]]]


@dataclass(frozen=True)
class ConnectivityReadiness:
    enabled: bool
    mcp_sse_ready: bool
    reason: Optional[str]


class ConnectivityStateError(RuntimeError):
    """A safe state transition failure with a bounded public category."""

    def __init__(self, reason: str):
        self.reason = reason
        super().__init__(reason)


class ConnectivityState:
    """Own the persisted opt-in bit and MCP request/disable serialization."""

    def __init__(self, state_path: Optional[Path] = None) -> None:
        self._state_path = state_path or Path(settings.data_directory) / "connectivity_state.json"
        self._enabled = False
        self._state_available = True
        self._transport_available = False
        self._enqueue_gate = asyncio.Lock()
        self._close_hook: Optional[CloseHook] = None
        self._load()

    @property
    def enqueue_gate(self) -> asyncio.Lock:
        """Serialize POST enqueue decisions with durable disable transitions."""
        return self._enqueue_gate

    @property
    def enabled(self) -> bool:
        return self._enabled

    @property
    def state_available(self) -> bool:
        return self._state_available

    @property
    def transport_available(self) -> bool:
        return self._transport_available

    @property
    def state_path(self) -> Path:
        return self._state_path

    def readiness(self) -> ConnectivityReadiness:
        if not self._state_available:
            return ConnectivityReadiness(False, False, "state_unavailable")
        if not self._enabled:
            return ConnectivityReadiness(False, False, "disabled")
        if not self._transport_available:
            return ConnectivityReadiness(True, False, "transport_unavailable")
        return ConnectivityReadiness(True, True, None)

    def set_transport_available(self, available: bool) -> None:
        self._transport_available = bool(available)

    def register_close_hook(self, hook: CloseHook) -> None:
        self._close_hook = hook

    async def enable(self) -> bool:
        """Durably enable first, then open the live request gate."""
        async with self._enqueue_gate:
            if not self._state_available or not self._transport_available:
                raise ConnectivityStateError(
                    "state_unavailable" if not self._state_available else "mcp_unavailable"
                )
            changed = not self._enabled
            if changed:
                self._persist(True)
                self._enabled = True
            return changed

    async def disable(self) -> bool:
        """Durably disable and detach sessions before allowing another POST."""
        close_actions: Sequence[CloseAction] = ()
        async with self._enqueue_gate:
            if not self._state_available:
                raise ConnectivityStateError("state_unavailable")
            changed = self._enabled
            if changed:
                self._persist(False)
                self._enabled = False
            if self._close_hook is not None:
                close_actions = await self._close_hook()

        # Registry and enqueue locks are released before best-effort transport close.
        for close in close_actions:
            try:
                close()
            except Exception:
                logger.warning("MCP session cleanup failed: close_error")
        return changed

    async def shutdown(self) -> None:
        """Detach and close all live MCP sessions without changing opt-in state."""
        close_actions: Sequence[CloseAction] = ()
        async with self._enqueue_gate:
            if self._close_hook is not None:
                close_actions = await self._close_hook()
        for close in close_actions:
            try:
                close()
            except Exception:
                logger.warning("MCP shutdown cleanup failed: close_error")

    def _load(self) -> None:
        try:
            with self._state_path.open("r", encoding="utf-8") as handle:
                data = json.load(handle)
            if type(data) is not dict or set(data) != {"enabled"} or type(data["enabled"]) is not bool:
                raise ValueError("invalid connectivity state")
            self._enabled = data["enabled"]
            self._state_available = True
        except FileNotFoundError:
            self._enabled = bool(settings.connectivity_enabled)
            self._state_available = True
        except (OSError, ValueError, TypeError, json.JSONDecodeError):
            self._enabled = False
            self._state_available = False
            logger.error("Connectivity state unavailable; external access disabled")

    def _persist(self, enabled: bool) -> None:
        parent = self._state_path.parent
        temp_path: Optional[str] = None
        try:
            parent.mkdir(parents=True, exist_ok=True)
            fd, temp_path = tempfile.mkstemp(prefix=".connectivity_state.", dir=str(parent))
            with os.fdopen(fd, "w", encoding="utf-8") as handle:
                json.dump({"enabled": enabled}, handle, separators=(",", ":"))
                handle.write("\n")
                handle.flush()
                os.fsync(handle.fileno())
            os.replace(temp_path, self._state_path)
            temp_path = None
            dir_fd = os.open(parent, os.O_RDONLY)
            try:
                os.fsync(dir_fd)
            finally:
                os.close(dir_fd)
            self._state_available = True
        except OSError as exc:
            raise ConnectivityStateError("state_unavailable") from exc
        finally:
            if temp_path is not None:
                try:
                    os.unlink(temp_path)
                except OSError:
                    pass

    def reset_for_tests(self, state_path: Path, *, seed_enabled: bool = False) -> None:
        """Reset the singleton in-place so already-mounted test apps stay valid."""
        self._state_path = state_path
        self._state_available = True
        self._transport_available = False
        self._close_hook = None
        self._enqueue_gate = asyncio.Lock()
        if state_path.exists():
            self._load()
        else:
            self._enabled = seed_enabled


_connectivity_state = ConnectivityState()


def get_connectivity_state() -> ConnectivityState:
    return _connectivity_state


def is_connectivity_enabled() -> bool:
    return _connectivity_state.enabled


def get_connectivity_readiness() -> ConnectivityReadiness:
    return _connectivity_state.readiness()
