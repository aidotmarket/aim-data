"""Official MCP client and customer-port configuration proof for legacy SSE."""

import asyncio
import socket
import threading
from datetime import datetime, timezone

import pytest
import uvicorn
from fastapi import FastAPI
from mcp import ClientSession
from mcp.client.sse import sse_client

from app.models.connectivity import ConnectivityToken, DatasetListResponse
from app.routers.mcp import mount_mcp_sse
from app.services.connectivity_state import get_connectivity_state

TOKEN_A = "vzmcp_AAAAAAAA_aaaaaaaaaaaaaaaaaaaaaaaaaaaaaaaa"
TOKEN_B = "vzmcp_BBBBBBBB_bbbbbbbbbbbbbbbbbbbbbbbbbbbbbbbb"


def test_nginx_and_compose_keep_the_customer_port_sse_safe():
    nginx = open("deploy/nginx.conf", encoding="utf-8").read()
    compose = open("docker-compose.aim-data.yml", encoding="utf-8").read()
    entrypoint = open("deploy/entrypoint.sh", encoding="utf-8").read()

    block = nginx.split("location ^~ /mcp/ {", 1)[1].split("}", 1)[0]
    for directive in (
        "proxy_pass http://127.0.0.1:8000;",
        "proxy_http_version 1.1;",
        "proxy_set_header Authorization $http_authorization;",
        'proxy_set_header Connection "";',
        "proxy_buffering off;",
        "proxy_cache off;",
        "proxy_request_buffering off;",
        "proxy_read_timeout 86400s;",
        "access_log off;",
    ):
        assert directive in block
    assert '${AIM_DATA_PORT:-8080}:80' in compose
    assert "AIM_DATA_CONNECTIVITY_ENABLED=${AIM_DATA_CONNECTIVITY_ENABLED:-false}" in compose
    assert "curl -sf http://localhost:80/api/health" in compose
    assert "--no-access-log" in entrypoint


@pytest.mark.asyncio
async def test_official_181_client_initialize_list_and_read_only_call_are_isolated(tmp_path, monkeypatch):
    state = get_connectivity_state()
    state.reset_for_tests(tmp_path / "connectivity_state.json")
    app = FastAPI(redirect_slashes=False)
    guard = mount_mcp_sse(app)
    assert guard.transport_available
    state._persist(True)
    state._enabled = True

    observed_validations = []
    observed_tools = []

    class Orchestrator:
        def validate_token(self, raw):
            observed_validations.append(raw)
            token_id = "AAAAAAAA" if raw == TOKEN_A else "BBBBBBBB" if raw == TOKEN_B else None
            if token_id is None:
                raise RuntimeError("invalid fixture token")
            return ConnectivityToken(
                id=token_id,
                label="fixture",
                scopes=["ext:datasets"],
                secret_last4=raw[-4:],
                created_at=datetime.now(timezone.utc),
            )

        async def list_datasets(self, token):
            observed_tools.append(token.id)
            return DatasetListResponse(datasets=[], count=0)

    orchestrator = Orchestrator()
    guard._validate = orchestrator.validate_token
    import app.mcp_server as server_module
    monkeypatch.setattr(server_module, "_orchestrator", orchestrator)

    sock = socket.socket()
    sock.bind(("127.0.0.1", 0))
    sock.listen(128)
    port = sock.getsockname()[1]
    server = uvicorn.Server(
        uvicorn.Config(app, log_level="error", access_log=False, lifespan="off")
    )
    thread = threading.Thread(
        target=server.run,
        kwargs={"sockets": [sock]},
        daemon=True,
    )
    thread.start()
    for _ in range(200):
        if server.started:
            break
        await asyncio.sleep(0.01)
    assert server.started

    async def exercise(raw_token, expected_id):
        async with sse_client(
            f"http://127.0.0.1:{port}/mcp/sse",
            headers={"Authorization": f"Bearer {raw_token}"},
            timeout=5,
            sse_read_timeout=10,
        ) as streams:
            async with ClientSession(*streams) as session:
                await session.initialize()
                tools = await session.list_tools()
                assert "vectoraiz_list_datasets" in {tool.name for tool in tools.tools}
                result = await session.call_tool("vectoraiz_list_datasets", {})
                assert result.isError is False
                assert '"count":0' in result.content[0].text
                assert guard.registry_snapshot()["bindings"]
        return expected_id

    try:
        assert await asyncio.gather(
            exercise(TOKEN_A, "AAAAAAAA"),
            exercise(TOKEN_B, "BBBBBBBB"),
        ) == ["AAAAAAAA", "BBBBBBBB"]
    finally:
        server.should_exit = True
        thread.join(timeout=5)
        sock.close()

    assert sorted(observed_tools) == ["AAAAAAAA", "BBBBBBBB"]
    assert TOKEN_A in observed_validations and TOKEN_B in observed_validations
    assert guard.registry_snapshot() == {"reservations": {}, "bindings": {}}
