"""MCP 1.8.1 legacy SSE auth, binding, and lifecycle tests."""

import asyncio
import importlib.metadata
from types import SimpleNamespace

import pytest

from app.routers.mcp import AuthenticatedMcpSse, MAX_MCP_CONNECTIONS
from app.services.connectivity_state import get_connectivity_state
from app.services.query_orchestrator import ConnectivityError

TOKEN_A = "vzmcp_AAAAAAAA_aaaaaaaaaaaaaaaaaaaaaaaaaaaaaaaa"
TOKEN_B = "vzmcp_BBBBBBBB_bbbbbbbbbbbbbbbbbbbbbbbbbbbbbbbb"
SESSION = "1" * 32


def _scope(method, path, *, token=None, query="", extra_headers=()):
    headers = list(extra_headers)
    if token is not None:
        headers.append((b"authorization", f"Bearer {token}".encode()))
    return {
        "type": "http",
        "asgi": {"version": "3.0"},
        "http_version": "1.1",
        "method": method,
        "scheme": "http",
        "path": path,
        "raw_path": path.encode(),
        "query_string": query.encode(),
        "headers": headers,
        "client": ("127.0.0.1", 1234),
        "server": ("test", 80),
    }


async def _receive():
    return {"type": "http.request", "body": b"", "more_body": False}


def _send_to(messages):
    async def send(message):
        messages.append(message)
    return send


async def _discard(_message):
    return None


class FakeSseApp:
    def __init__(self, frame=None):
        self.frame = frame or f"event: endpoint\r\ndata: /mcp/messages/?session_id={SESSION}\r\n\r\n".encode()
        self.release = asyncio.Event()
        self.posts = 0
        self.post_contexts = []

    async def __call__(self, scope, receive, send):
        if scope["method"] == "GET":
            await send({"type": "http.response.start", "status": 200, "headers": []})
            await send({"type": "http.response.body", "body": self.frame, "more_body": True})
            await self.release.wait()
            return
        from app.mcp_server import http_bearer_context, http_execution_context

        self.posts += 1
        self.post_contexts.append((http_execution_context.get(), http_bearer_context.get()))
        await send({"type": "http.response.start", "status": 202, "headers": []})
        await send({"type": "http.response.body", "body": b"Accepted"})


@pytest.fixture
def state(tmp_path):
    state = get_connectivity_state()
    state.reset_for_tests(tmp_path / "connectivity_state.json")
    return state


def _token(raw):
    return SimpleNamespace(id=raw.split("_")[1], expires_at=None)


async def _start_bound_get(guard, fake, token=TOKEN_A):
    sent = []
    task = asyncio.create_task(guard(_scope("GET", "/sse", token=token), _receive, _send_to(sent)))
    for _ in range(100):
        if guard.registry_snapshot()["bindings"]:
            return task, sent
        await asyncio.sleep(0.001)
    raise AssertionError("SSE session did not bind")


def test_sdk_version_and_exact_legacy_routes():
    from app.mcp_server import mcp_server

    assert importlib.metadata.version("mcp") == "1.8.1"
    sdk_app = mcp_server.sse_app(mount_path="/mcp")
    assert [(route.path, type(route).__name__) for route in sdk_app.routes] == [
        ("/sse", "Route"),
        ("/messages", "Mount"),
    ]
    assert mcp_server.settings.mount_path == "/mcp"


@pytest.mark.asyncio
async def test_disabled_missing_duplicate_and_query_auth_fail_closed(state):
    fake = FakeSseApp()
    guard = AuthenticatedMcpSse(fake)
    guard._validate = lambda raw: _token(raw)

    sent = []
    await guard(_scope("GET", "/sse", token=TOKEN_A), _receive, _send_to(sent))
    assert sent[0]["status"] == 503
    assert b"mcp_disabled" in sent[1]["body"]

    await state.enable()
    for scope in (
        _scope("GET", "/sse"),
        _scope("GET", "/sse", token=TOKEN_A, query="token=leak"),
        _scope(
            "GET",
            "/sse",
            extra_headers=((b"authorization", f"Bearer {TOKEN_A}".encode()),
                           (b"authorization", f"Bearer {TOKEN_B}".encode())),
        ),
    ):
        sent = []
        await guard(scope, _receive, _send_to(sent))
        assert sent[0]["status"] == 401
        assert (b"www-authenticate", b"Bearer") in sent[0]["headers"]
        assert TOKEN_A.encode() not in sent[1]["body"]


@pytest.mark.asyncio
async def test_get_binding_and_post_require_same_revalidated_token(state, caplog):
    fake = FakeSseApp()
    guard = AuthenticatedMcpSse(fake)
    guard._validate = lambda raw: _token(raw)
    await state.enable()
    get_task, sent = await _start_bound_get(guard, fake)
    assert b"/mcp/messages/?session_id=" in sent[1]["body"]
    snapshot = guard.registry_snapshot()
    assert TOKEN_A not in repr(snapshot)
    assert TOKEN_A.encode() not in b"".join(message.get("body", b"") for message in sent)
    assert TOKEN_A not in caplog.text
    assert TOKEN_A not in state.state_path.read_text(encoding="utf-8")
    assert snapshot["bindings"][SESSION]["token_id"] == "AAAAAAAA"

    mismatch = []
    await guard(
        _scope("POST", "/messages/", token=TOKEN_B, query=f"session_id={SESSION}"),
        _receive,
        _send_to(mismatch),
    )
    assert mismatch[0]["status"] == 401
    assert fake.posts == 0

    accepted = []
    await guard(
        _scope("POST", "/messages/", token=TOKEN_A, query=f"session_id={SESSION}"),
        _receive,
        _send_to(accepted),
    )
    assert accepted[0]["status"] == 202
    assert fake.posts == 1
    assert fake.post_contexts == [(False, None)]
    fake.release.set()
    await get_task
    assert guard.registry_snapshot() == {"reservations": {}, "bindings": {}}


@pytest.mark.asyncio
async def test_validator_indeterminacy_detaches_session_and_closes_get(state):
    fake = FakeSseApp()
    guard = AuthenticatedMcpSse(fake)
    guard._validate = lambda raw: _token(raw)
    await state.enable()
    get_task, _ = await _start_bound_get(guard, fake)

    def unavailable(_raw):
        raise RuntimeError("database unavailable")

    guard._validate = unavailable
    sent = []
    await guard(
        _scope("POST", "/messages/", token=TOKEN_A, query=f"session_id={SESSION}"),
        _receive,
        _send_to(sent),
    )
    assert sent[0]["status"] == 503
    assert b"mcp_unavailable" in sent[1]["body"]
    with pytest.raises(asyncio.CancelledError):
        await get_task
    assert not guard.registry_snapshot()["bindings"]


@pytest.mark.asyncio
@pytest.mark.parametrize("response_starts", [False, True])
async def test_post_sdk_failure_runs_sweep_and_only_sends_503_before_response_start(
    state,
    response_starts,
):
    class FailingPostApp(FakeSseApp):
        async def __call__(self, scope, receive, send):
            if scope["method"] == "GET":
                return await super().__call__(scope, receive, send)
            if response_starts:
                await send({"type": "http.response.start", "status": 202, "headers": []})
            raise RuntimeError("sensitive-sdk-detail")

    fake = FailingPostApp()
    guard = AuthenticatedMcpSse(fake)
    guard._validate = lambda raw: _token(raw)
    await state.enable()
    get_task, _ = await _start_bound_get(guard, fake)
    close_actions_run = []
    guard._sweep_locked = lambda _now: (lambda: close_actions_run.append(True),)

    sent = []
    await guard(
        _scope("POST", "/messages/", token=TOKEN_A, query=f"session_id={SESSION}"),
        _receive,
        _send_to(sent),
    )

    assert close_actions_run == [True]
    statuses = [message["status"] for message in sent if message["type"] == "http.response.start"]
    assert statuses == ([202] if response_starts else [503])
    if not response_starts:
        assert b"mcp_unavailable" in sent[1]["body"]
    fake.release.set()
    await get_task


@pytest.mark.asyncio
async def test_watchdog_revalidates_immediately_after_session_promotion(state):
    fake = FakeSseApp()
    guard = AuthenticatedMcpSse(fake)
    validation_calls = 0

    def validate(raw):
        nonlocal validation_calls
        validation_calls += 1
        if validation_calls > 1:
            raise ConnectivityError("auth_revoked", "revoked")
        return _token(raw)

    guard._validate = validate
    await state.enable()
    sent = []
    get_task = asyncio.create_task(
        guard(_scope("GET", "/sse", token=TOKEN_A), _receive, _send_to(sent))
    )

    with pytest.raises(asyncio.CancelledError):
        await asyncio.wait_for(get_task, timeout=0.5)
    assert validation_calls == 2
    assert guard.registry_snapshot() == {"reservations": {}, "bindings": {}}


@pytest.mark.asyncio
async def test_idle_watchdog_revokes_only_its_own_connection(state, monkeypatch):
    import app.routers.mcp as mcp_router

    monkeypatch.setattr(mcp_router, "WATCHDOG_SECONDS", 0.01)

    class MultiSseApp(FakeSseApp):
        def __init__(self):
            super().__init__()
            self.sessions = iter(("a" * 32, "b" * 32))
            self.releases = {}

        async def __call__(self, scope, receive, send):
            if scope["method"] == "POST":
                return await super().__call__(scope, receive, send)
            session_id = next(self.sessions)
            release = self.releases[session_id] = asyncio.Event()
            frame = f"event: endpoint\r\ndata: /mcp/messages/?session_id={session_id}\r\n\r\n".encode()
            await send({"type": "http.response.start", "status": 200, "headers": []})
            await send({"type": "http.response.body", "body": frame, "more_body": True})
            await release.wait()

    fake = MultiSseApp()
    guard = AuthenticatedMcpSse(fake)
    revoked = set()

    def validate(raw):
        if raw in revoked:
            raise ConnectivityError("auth_revoked", "revoked")
        return _token(raw)

    guard._validate = validate
    await state.enable()
    sent_a, sent_b = [], []
    task_a = asyncio.create_task(guard(_scope("GET", "/sse", token=TOKEN_A), _receive, _send_to(sent_a)))
    for _ in range(100):
        if "a" * 32 in guard.registry_snapshot()["bindings"]:
            break
        await asyncio.sleep(0.001)
    task_b = asyncio.create_task(guard(_scope("GET", "/sse", token=TOKEN_B), _receive, _send_to(sent_b)))
    for _ in range(100):
        if "b" * 32 in guard.registry_snapshot()["bindings"]:
            break
        await asyncio.sleep(0.001)

    revoked.add(TOKEN_A)
    with pytest.raises(asyncio.CancelledError):
        await asyncio.wait_for(task_a, timeout=0.5)
    assert set(guard.registry_snapshot()["bindings"]) == {"b" * 32}

    accepted = []
    await guard(
        _scope("POST", "/messages/", token=TOKEN_B, query=f"session_id={'b' * 32}"),
        _receive,
        _send_to(accepted),
    )
    assert accepted[0]["status"] == 202
    fake.releases["b" * 32].set()
    await task_b


@pytest.mark.asyncio
async def test_malformed_first_frame_releases_reservation(state):
    fake = FakeSseApp(b"event: message\r\ndata: {}\r\n\r\n")
    guard = AuthenticatedMcpSse(fake)
    guard._validate = lambda raw: _token(raw)
    await state.enable()
    sent = []
    await guard(_scope("GET", "/sse", token=TOKEN_A), _receive, _send_to(sent))
    assert sent[0]["status"] == 503
    assert guard.registry_snapshot() == {"reservations": {}, "bindings": {}}


@pytest.mark.asyncio
async def test_delayed_first_frame_hits_exact_deadline_and_releases_slot(state, monkeypatch):
    import app.routers.mcp as mcp_router

    monkeypatch.setattr(mcp_router, "FIRST_FRAME_TIMEOUT_SECONDS", 0.01)

    async def delayed(_scope, _receive, _send):
        await asyncio.Event().wait()

    guard = AuthenticatedMcpSse(delayed)
    guard._validate = lambda raw: _token(raw)
    await state.enable()
    request_task = asyncio.create_task(
        guard(_scope("GET", "/sse", token=TOKEN_A), _receive, _discard)
    )
    with pytest.raises(asyncio.CancelledError):
        await request_task
    assert guard.registry_snapshot() == {"reservations": {}, "bindings": {}}


@pytest.mark.asyncio
async def test_process_cap_counts_reservations_before_sdk_sessions(state):
    class PendingApp:
        def __init__(self):
            self.entered = 0
            self.release = asyncio.Event()

        async def __call__(self, scope, receive, send):
            self.entered += 1
            await self.release.wait()

    fake = PendingApp()
    guard = AuthenticatedMcpSse(fake)
    guard._validate = lambda raw: _token(raw)
    await state.enable()
    tasks = [
        asyncio.create_task(guard(_scope("GET", "/sse", token=TOKEN_A), _receive, _discard))
        for _ in range(MAX_MCP_CONNECTIONS)
    ]
    for _ in range(100):
        if len(guard.registry_snapshot()["reservations"]) == MAX_MCP_CONNECTIONS:
            break
        await asyncio.sleep(0.001)
    assert fake.entered == MAX_MCP_CONNECTIONS

    rejected = []
    await guard(_scope("GET", "/sse", token=TOKEN_A), _receive, _send_to(rejected))
    assert rejected[0]["status"] == 503
    assert fake.entered == MAX_MCP_CONNECTIONS
    actions = await guard.detach_all()
    for action in actions:
        action()
    await asyncio.gather(*tasks, return_exceptions=True)


def test_close_action_logs_exception_type_without_sensitive_message(caplog):
    def fail_close():
        raise RuntimeError("sensitive-close-detail")

    AuthenticatedMcpSse._run_close_actions((fail_close,))

    assert "RuntimeError" in caplog.text
    assert "sensitive-close-detail" not in caplog.text


@pytest.mark.asyncio
async def test_scheduled_connection_close_is_tracked_and_exception_safe(state, monkeypatch, caplog):
    guard = AuthenticatedMcpSse(FakeSseApp())

    async def fail_close(_nonce, _session_id_getter):
        raise ValueError("sensitive-async-close-detail")

    monkeypatch.setattr(guard, "_close_owned_connection", fail_close)
    guard._schedule_connection_close("nonce", lambda: None)

    assert len(guard._close_tasks) == 1
    await asyncio.sleep(0)
    await asyncio.sleep(0)
    assert not guard._close_tasks
    assert "ValueError" in caplog.text
    assert "sensitive-async-close-detail" not in caplog.text


@pytest.mark.asyncio
async def test_http_contexts_are_isolated_and_never_fall_back_to_stdio(monkeypatch):
    import app.mcp_server as server

    observed = []
    orchestrator = SimpleNamespace(validate_token=lambda raw: observed.append(raw) or raw)
    monkeypatch.setattr(server, "_get_orchestrator", lambda: orchestrator)
    monkeypatch.setattr(server, "_stdio_token_raw", "stdio-secret")

    async def validate(raw):
        bearer = server.http_bearer_context.set(raw)
        execution = server.http_execution_context.set(True)
        try:
            await asyncio.sleep(0)
            return server._validate_token()
        finally:
            server.http_execution_context.reset(execution)
            server.http_bearer_context.reset(bearer)

    assert await asyncio.gather(validate(TOKEN_A), validate(TOKEN_B)) == [TOKEN_A, TOKEN_B]
    assert observed == [TOKEN_A, TOKEN_B]

    execution = server.http_execution_context.set(True)
    try:
        with pytest.raises(ConnectivityError):
            server._validate_token()
    finally:
        server.http_execution_context.reset(execution)
    assert "stdio-secret" not in observed
