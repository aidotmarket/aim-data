from types import SimpleNamespace

import httpx
import pytest

from app.services import s3_broker_client
from app.services.s3_broker_client import S3BrokerClient, S3BrokerNotActivatedError, S3BrokerRateLimited


class FakeHttpClient:
    calls = []
    response = httpx.Response(200, json={"external_id": "serial-derived-external-id"})

    def __init__(self, *args, **kwargs):
        self.args = args
        self.kwargs = kwargs

    def __enter__(self):
        return self

    def __exit__(self, exc_type, exc, tb):
        return None

    def request(self, method, url, *, params=None, json=None, headers=None):
        self.calls.append(
            {
                "method": method,
                "url": url,
                "params": params,
                "json": json,
                "headers": headers,
            }
        )
        return self.response


def _store(serial="AIM-serial-123", install_token="install-token-abc"):
    return SimpleNamespace(state=SimpleNamespace(serial=serial, install_token=install_token))


def test_broker_client_sends_serial_query_and_bearer_install_token(monkeypatch):
    FakeHttpClient.calls = []
    FakeHttpClient.response = httpx.Response(200, json={"external_id": "serial-derived-external-id"})
    monkeypatch.setattr(s3_broker_client, "get_serial_store", lambda: _store())
    monkeypatch.setattr(s3_broker_client.httpx, "Client", FakeHttpClient)

    external_id = S3BrokerClient(base_url="https://backend.example").get_external_id()

    assert external_id == "serial-derived-external-id"
    assert FakeHttpClient.calls == [
        {
            "method": "GET",
            "url": "https://backend.example/api/v1/serials/AIM-serial-123/s3-connections/external-id",
            "params": {"serial": "AIM-serial-123"},
            "json": None,
            "headers": {"Authorization": "Bearer install-token-abc"},
        }
    ]


def test_broker_client_fails_closed_without_activation_credentials(monkeypatch):
    FakeHttpClient.calls = []
    FakeHttpClient.response = httpx.Response(200, json={"external_id": "serial-derived-external-id"})
    monkeypatch.setattr(s3_broker_client, "get_serial_store", lambda: _store(serial="", install_token=None))
    monkeypatch.setattr(s3_broker_client.httpx, "Client", FakeHttpClient)

    with pytest.raises(S3BrokerNotActivatedError, match="not connected/activated"):
        S3BrokerClient(base_url="https://backend.example").get_external_id()

    assert FakeHttpClient.calls == []


def test_broker_client_raises_rate_limited_with_retry_after(monkeypatch):
    FakeHttpClient.calls = []
    FakeHttpClient.response = httpx.Response(
        429,
        json={"detail": {"error": "rate_limit_exceeded"}},
        headers={"Retry-After": "17"},
    )
    monkeypatch.setattr(s3_broker_client, "get_serial_store", lambda: _store())
    monkeypatch.setattr(s3_broker_client.httpx, "Client", FakeHttpClient)

    with pytest.raises(S3BrokerRateLimited) as exc_info:
        S3BrokerClient(base_url="https://backend.example").list_objects(
            role_arn="arn:aws:iam::210987654321:role/aim-data",
            region="us-east-1",
            bucket="seller-bucket",
        )

    assert exc_info.value.retry_after == 17.0
    assert FakeHttpClient.calls[0]["json"]["max_keys"] == 1000


def test_broker_client_caps_unparseable_retry_after(monkeypatch):
    FakeHttpClient.calls = []
    FakeHttpClient.response = httpx.Response(
        429,
        json={"detail": {"error": "rate_limit_exceeded"}},
        headers={"Retry-After": "not-seconds"},
    )
    monkeypatch.setattr(s3_broker_client, "get_serial_store", lambda: _store())
    monkeypatch.setattr(s3_broker_client.httpx, "Client", FakeHttpClient)

    with pytest.raises(S3BrokerRateLimited) as exc_info:
        S3BrokerClient(base_url="https://backend.example").get_external_id()

    assert exc_info.value.retry_after == 5.0


def test_broker_client_caps_large_retry_after(monkeypatch):
    FakeHttpClient.calls = []
    FakeHttpClient.response = httpx.Response(
        429,
        json={"detail": {"error": "rate_limit_exceeded"}},
        headers={"Retry-After": "120"},
    )
    monkeypatch.setattr(s3_broker_client, "get_serial_store", lambda: _store())
    monkeypatch.setattr(s3_broker_client.httpx, "Client", FakeHttpClient)

    with pytest.raises(S3BrokerRateLimited) as exc_info:
        S3BrokerClient(base_url="https://backend.example").get_external_id()

    assert exc_info.value.retry_after == 60.0
