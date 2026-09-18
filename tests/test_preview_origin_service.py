import hashlib
import http.client
import json
import socket
import threading
from email.message import Message
from pathlib import Path
from unittest.mock import Mock

import pytest

from app.services import preview_origin_service as origin
from app.services.preview_package_service import (
    PublicationStore,
    CommitmentPreviewBuilder,
    MEDIA_TYPE,
)

URL = "https://seller.example/previews/package.json"
BROWSER = "https://ai.market"
TIME = "2026-09-17T12:00:00.000000Z"


def headers():
    result = Message()
    for key, value in {
        "content-type": MEDIA_TYPE,
        "cache-control": "no-store",
        "access-control-allow-origin": BROWSER,
        "access-control-allow-methods": "GET, OPTIONS",
    }.items():
        result[key] = value
    return result


def capture(h=None, method="GET", status=200, **kwargs):
    return origin.capture_receipt(
        URL, method, status, h or headers(), captured_at=TIME, origin=BROWSER, **kwargs
    )


def test_closed_receipt():
    result = capture()
    assert set(result) == {
        "url",
        "method",
        "status",
        "captured_at",
        "headers",
        "no_set_cookie",
    }
    assert set(result["headers"]) == set(origin.HEADER_KEYS)
    assert result["headers"]["access-control-allow-headers"] is None
    assert result["headers"]["access-control-allow-credentials"] is None
    assert result["no_set_cookie"] is True
    capture(method="OPTIONS", status=204)
    capture(status=410, retired=True)


@pytest.mark.parametrize(
    "key,value,code",
    [
        ("access-control-allow-origin", None, "cors_origin"),
        ("access-control-allow-origin", "https://wrong.example", "cors_origin"),
    ],
)
def test_header_failure_classes(key, value, code, caplog):
    h = headers()
    del h[key]
    if value is not None:
        h[key] = value
    with pytest.raises(origin.OriginError, match="^" + code + "$") as exc:
        capture(h)
    assert "unique_synthetic_cookie_marker" not in str(exc.value) + caplog.text


@pytest.mark.parametrize(
    "status,code",
    [(301, "redirect"), (302, "redirect"), (500, "http_status"), (404, "http_status")],
)
def test_status_failures(status, code):
    with pytest.raises(origin.OriginError, match=code):
        capture(status=status)


def test_transport_headers_and_preflight_are_observations():
    h = headers()
    h.replace_header("access-control-allow-methods", "POST")
    assert capture(h, method="OPTIONS", status=500)["status"] == 500
    h = headers()
    h["cache-control"] = "no-store"
    assert capture(h)["headers"]["cache-control"] == "no-store, no-store"
    for key, value in (
        ("content-type", "application/json"),
        ("cache-control", None),
        ("access-control-allow-credentials", "true"),
        ("set-cookie", "synthetic=value"),
        ("content-encoding", "gzip"),
    ):
        observed = headers()
        if key in observed:
            del observed[key]
        if value is not None:
            observed[key] = value
        assert capture(observed)["status"] == 200


@pytest.mark.parametrize("size", [8191, 8192, 8193])
def test_receipt_byte_boundaries(size):
    h = headers()
    result = capture(h)
    # Pad a legal comma-list without truncation; exactly measured UTF-8 receipt.
    padding = size - len(origin.receipt_bytes(result))
    h.replace_header("cache-control", "no-store" + " " * padding)
    if size > 8192:
        with pytest.raises(origin.OriginError, match="receipt_limit"):
            capture(h)
    else:
        assert len(origin.receipt_bytes(capture(h))) == size


@pytest.mark.parametrize(
    "url",
    [
        "http://seller.example/a",
        "https://user@seller.example/a",
        "https://seller.example/a?sig=x",
        "https://seller.example/a#x",
        "https://127.0.0.1/a",
        "https://[::1]/a",
        "https://10.0.0.1/a",
        "https://169.254.169.254/a",
        "https://ai.market/a",
        "https://api.ai.market/a",
        "https://seller.r2.dev/a",
        "https://seller.example/" + "a" * 2048,
        "https://localhost/a",
        "https://seller.example./a",
        "https://seller.example/a?",
        "https://seller.example/a#",
        "https://seller.example/\n",
    ],
    ids=lambda x: "url-" + str(len(x)),
)
def test_url_failure_classes(url):
    with pytest.raises(origin.OriginError):
        origin.validate_url(url)


def test_dns_private_mixed_and_unavailable(monkeypatch):
    parsed = origin.validate_url(URL)
    monkeypatch.setattr(
        socket,
        "getaddrinfo",
        lambda *a, **k: [
            (2, 1, 6, "", ("8.8.8.8", 443)),
            (2, 1, 6, "", ("10.0.0.1", 443)),
        ],
    )
    with pytest.raises(origin.OriginError, match="private_address"):
        origin.public_addresses(parsed)


@pytest.mark.parametrize(
    "address", ["224.0.0.1", "ff0e::1", "::ffff:10.0.0.1", "2002:0a00:0001::"]
)
def test_non_public_multicast_and_transition_addresses(monkeypatch, address):
    monkeypatch.setattr(
        socket, "getaddrinfo", lambda *a, **k: [(2, 1, 6, "", (address, 443))]
    )
    with pytest.raises(origin.OriginError, match="private_address"):
        origin.public_addresses(origin.validate_url(URL))
    monkeypatch.setattr(
        socket, "getaddrinfo", Mock(side_effect=OSError("private synthetic diagnostic"))
    )
    with pytest.raises(origin.OriginError, match="^dns_unavailable$"):
        origin.public_addresses(origin.validate_url(URL))


def test_pinned_transport_and_exact_requests(monkeypatch):
    body = b'{"synthetic":"seller-only bytes"}'
    dns = Mock(return_value=[(2, 1, 6, "", ("8.8.8.8", 443))])
    monkeypatch.setattr(socket, "getaddrinfo", dns)
    requests = []

    class Connection:
        def __init__(self, host, port, address):
            assert (host, port, address) == ("seller.example", 443, "8.8.8.8")

        def request(self, method, path, headers):
            requests.append((method, path, headers))

        def getresponse(self):
            return Mock(status=200, headers=headers(), read=lambda n: body)

        def close(self):
            pass

    monkeypatch.setattr(origin, "PinnedHTTPSConnection", Connection)
    receipts = origin.verify_hosted_package(
        URL,
        origin=BROWSER,
        expected_sha256=hashlib.sha256(body).hexdigest(),
        expected_bytes=len(body),
    )
    assert dns.call_count == 1
    assert requests == [
        (
            "GET",
            "/previews/package.json",
            {"Origin": BROWSER, "Accept-Encoding": "identity"},
        ),
        (
            "OPTIONS",
            "/previews/package.json",
            {
                "Origin": BROWSER,
                "Accept-Encoding": "identity",
                "Access-Control-Request-Method": "GET",
            },
        ),
    ]
    assert body.decode() not in json.dumps(receipts)
    with pytest.raises(origin.OriginError, match="package_mismatch"):
        origin.verify_hosted_package(
            URL, origin=BROWSER, expected_sha256="0" * 64, expected_bytes=len(body)
        )


def test_tls_hostname_and_peer_pin(monkeypatch):
    raw = Mock()
    raw.getpeername.return_value = ("8.8.8.8", 443)
    connect = Mock(return_value=raw)
    monkeypatch.setattr(socket, "create_connection", connect)
    client = origin.PinnedHTTPSConnection("seller.example", 443, "8.8.8.8")
    client._context = Mock()
    client.connect()
    connect.assert_called_once_with(("8.8.8.8", 443), 5)
    client._context.wrap_socket.assert_called_once_with(
        raw, server_hostname="seller.example"
    )
    raw.getpeername.return_value = ("10.0.0.1", 443)
    with pytest.raises(origin.OriginError, match="address_changed"):
        client.connect()


def test_real_local_origin_retirement_and_isolation(tmp_path, capsys):
    payload = Path("tests/fixtures/aim_preview_package_v2.json").read_bytes()
    envelope = json.loads(payload)
    store = PublicationStore(tmp_path / "public", tmp_path / "private")
    from app.services.dataset_canonicalization import CanonicalSchema
    from app.services.dataset_merkle_service import (
        build_disk_tree,
        canonical_json_bytes,
    )

    golden = json.loads(Path("tests/fixtures/aim_dataset_merkle_v1.json").read_text())
    schema = CanonicalSchema(golden["canonical_schema"])
    private = tmp_path / "tree"
    private.mkdir()
    tree = build_disk_tree(
        (canonical_json_bytes(r["canonical_row"]) for r in golden["rows"]),
        schema.digest,
        private,
    )
    builder = CommitmentPreviewBuilder(tree, schema.descriptors)
    package = builder.prepare(
        list(range(5)),
        proof_ids=[p["proof_id"] for p in envelope["entries"]],
        commitment_id=envelope["commitment_id"],
        disclosure_version=envelope["disclosure_version"],
        scanned_at=TIME,
        rights_confirmed=True,
        public_preview_permission=True,
        restricted_content_confirmed=True,
        manifest_bytes=1000,
        package_url=URL,
    )
    assert package.payload == payload
    store.export(package)
    path = "/" + store.path(envelope["disclosure_version"], envelope["sample_hash"])
    server = origin.make_origin(store, origin=BROWSER)
    thread = threading.Thread(target=server.serve_forever, daemon=True)
    thread.start()
    receipts = []

    def request(method, target, expected):
        connection = http.client.HTTPConnection(*server.server_address)
        connection.request(method, target, headers={"Origin": BROWSER})
        response = connection.getresponse()
        body = response.read()
        assert response.status == expected
        assert response.getheader("content-type") == MEDIA_TYPE
        assert response.getheader("cache-control") == "no-store"
        assert response.getheader("set-cookie") is None
        if expected in {200, 204, 410}:
            receipts.append(
                origin.capture_receipt(
                    "https://seller.example" + path,
                    method,
                    expected,
                    response.headers,
                    captured_at=TIME,
                    origin=BROWSER,
                    retired=expected == 410,
                )
            )
        connection.close()
        return body

    try:
        assert request("GET", path, 200) == payload
        request("OPTIONS", path, 204)
        for target in [
            "/",
            "/../private",
            "/%2e%2e/private",
            path + "?unique_synthetic_marker",
            "/unknown",
        ]:
            assert request("GET", target, 404) == b""
        request("POST", path, 501)
        store.retire(envelope["disclosure_version"], envelope["sample_hash"])
        request("GET", path, 410)
        request("OPTIONS", path, 204)
        assert "unique_synthetic_marker" not in json.dumps(receipts)
    finally:
        server.shutdown()
        server.server_close()
        thread.join()
    assert "unique_synthetic_marker" not in str(capsys.readouterr())


def test_source_connection_never_authorizes_preview():
    from app.services.s3_publish_source_resolver import S3PublishSourceResolution

    source = S3PublishSourceResolution("bucket", "region", "arn", "prefix", "serial")
    assert source.preview_host_eligible is False


@pytest.fixture(autouse=True)
def pinned_identity(monkeypatch):
    from app.services import preview_content_policy as policy

    monkeypatch.setattr(policy, "detector_identity", lambda: policy.DETECTOR_IDENTITY)
