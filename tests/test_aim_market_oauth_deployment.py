from pathlib import Path
import subprocess
import time

import pytest
from app.config import Settings, enforce_single_worker
from app.services import aim_market_oauth as flow


def test_oauth_enabled_by_default(monkeypatch):
    monkeypatch.delenv("AIM_DATA_OAUTH_ENABLED", raising=False)
    settings = Settings(_env_file=None)
    assert settings.oauth_enabled is True


@pytest.mark.parametrize("port", [8080, 8099, 18081])
def test_ports_8080_8099_18081(monkeypatch, port):
    monkeypatch.setenv("AIM_DATA_OAUTH_LOOPBACK_PORT", str(port))
    settings = Settings()
    monkeypatch.setattr(flow.settings, "oauth_loopback_port", settings.oauth_loopback_port)
    assert flow.origin() == f"http://127.0.0.1:{port}"
    compose = Path("docker-compose.aim-data.yml").read_text()
    assert '127.0.0.1:${AIM_DATA_PORT:-8080}:80' in compose
    assert '${AIM_DATA_OAUTH_LOOPBACK_PORT:-${AIM_DATA_PORT:-8080}}' in compose


@pytest.mark.parametrize("key", ["WORKERS", "WEB_CONCURRENCY", "UVICORN_WORKERS", "VECTORAIZ_WORKERS"])
def test_multiworker_startup_rejected(monkeypatch, key):
    monkeypatch.setenv(key, "2")
    with pytest.raises(RuntimeError, match=key):
        enforce_single_worker()
    for script in ("entrypoint.sh", "deploy/entrypoint.sh"):
        result = subprocess.run(["sh", script], capture_output=True, text=True)
        assert result.returncode != 0 and "requires one uvicorn worker" in result.stderr


@pytest.mark.parametrize("port", ["08080", "8080.0", " 8080", "1023", "65536", True])
def test_invalid_port(port):
    with pytest.raises(ValueError):
        Settings(AIM_DATA_OAUTH_LOOPBACK_PORT=port)


def test_bootstrap_origin_and_ttl_bounds():
    flow.records.clear()
    flow.allocate("expired", {"verifier": "secret"}, ttl=-1)
    flow.cleanup()
    assert not flow.records
    for index in range(1024):
        flow.allocate(str(index), {})
    with pytest.raises(Exception) as exc:
        flow.allocate("overflow", {})
    assert exc.value.status_code == 429
    assert all(record["expires"] <= time.monotonic() + 600 for record in flow.records.values())
    flow.records.clear()


def test_callback_logs_and_final_urls_redacted():
    config = Path("deploy/nginx.conf").read_text().split("location = /api/auth/aim-market/callback")[1].split("}")[0]
    assert all(value in config for value in ("access_log off", "error_log /dev/null crit", "no-store", "no-referrer"))
    for script in ("entrypoint.sh", "deploy/entrypoint.sh"):
        assert "--no-access-log" in Path(script).read_text()
