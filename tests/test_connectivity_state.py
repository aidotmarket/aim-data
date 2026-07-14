"""Persistent connectivity opt-in and transition-ordering regressions."""

import asyncio
import json

import pytest

from app.services.connectivity_state import ConnectivityState, ConnectivityStateError


def test_missing_state_seeds_from_explicit_environment(monkeypatch, tmp_path):
    monkeypatch.setattr("app.services.connectivity_state.settings.connectivity_enabled", True)
    state = ConnectivityState(tmp_path / "connectivity_state.json")
    assert state.enabled is True
    assert state.readiness().reason == "transport_unavailable"


def test_valid_file_wins_and_corrupt_file_fails_closed(monkeypatch, tmp_path):
    path = tmp_path / "connectivity_state.json"
    path.write_text('{"enabled":false}\n', encoding="utf-8")
    monkeypatch.setattr("app.services.connectivity_state.settings.connectivity_enabled", True)
    assert ConnectivityState(path).enabled is False

    path.write_text('{"enabled":"yes"}', encoding="utf-8")
    corrupt = ConnectivityState(path)
    assert corrupt.enabled is False
    assert corrupt.state_available is False
    assert corrupt.readiness().reason == "state_unavailable"


@pytest.mark.asyncio
async def test_enable_disable_are_durable_and_preserve_close_order(tmp_path):
    path = tmp_path / "connectivity_state.json"
    state = ConnectivityState(path)
    state.set_transport_available(True)
    observed = []

    async def detach():
        observed.append(("detach", path.read_text(encoding="utf-8"), state.enabled))
        return (lambda: observed.append(("close", state.enqueue_gate.locked())),)

    state.register_close_hook(detach)
    assert await state.enable() is True
    assert json.loads(path.read_text(encoding="utf-8")) == {"enabled": True}
    assert await state.disable() is True
    assert observed == [
        ("detach", '{"enabled":false}\n', False),
        ("close", False),
    ]
    assert ConnectivityState(path).enabled is False


@pytest.mark.asyncio
async def test_failed_write_never_changes_live_state(monkeypatch, tmp_path):
    path = tmp_path / "connectivity_state.json"
    path.write_text('{"enabled":true}\n', encoding="utf-8")
    state = ConnectivityState(path)
    state.set_transport_available(True)

    def fail(_enabled):
        raise ConnectivityStateError("state_unavailable")

    monkeypatch.setattr(state, "_persist", fail)
    with pytest.raises(ConnectivityStateError):
        await state.disable()
    assert state.enabled is True
    assert json.loads(path.read_text(encoding="utf-8")) == {"enabled": True}


@pytest.mark.asyncio
async def test_disable_serializes_after_an_inflight_enqueue(tmp_path):
    state = ConnectivityState(tmp_path / "connectivity_state.json")
    state.set_transport_available(True)
    await state.enable()
    entered = asyncio.Event()
    release = asyncio.Event()
    order = []

    async def enqueue():
        async with state.enqueue_gate:
            entered.set()
            await release.wait()
            order.append("enqueue")

    task = asyncio.create_task(enqueue())
    await entered.wait()
    disable = asyncio.create_task(state.disable())
    await asyncio.sleep(0)
    assert not disable.done()
    release.set()
    await task
    await disable
    order.append("disabled")
    assert order == ["enqueue", "disabled"]
    assert state.enabled is False


@pytest.mark.asyncio
async def test_enable_fails_when_transport_is_unavailable(tmp_path):
    state = ConnectivityState(tmp_path / "connectivity_state.json")
    with pytest.raises(ConnectivityStateError) as exc:
        await state.enable()
    assert exc.value.reason == "mcp_unavailable"
    assert state.enabled is False
    assert not state.state_path.exists()
