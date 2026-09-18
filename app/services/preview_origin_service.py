"""Isolated seller origin and seller-local, pinned HTTPS header verification."""

import hashlib
import http.client
import ipaddress
import json
import re
import socket
import ssl
import zlib
from datetime import datetime, timezone
from http.server import BaseHTTPRequestHandler, ThreadingHTTPServer
from urllib.parse import urlsplit

from app.services.dataset_merkle_service import (
    canonical_json_bytes,
    canonical_rfc3339_utc,
)
from app.services.preview_package_service import CAPS, MEDIA_TYPE, PackageError

HEADER_KEYS = (
    "content-type",
    "cache-control",
    "access-control-allow-origin",
    "access-control-allow-methods",
    "access-control-allow-headers",
    "access-control-allow-credentials",
)
OPERATED_HOSTS = frozenset({"ai.market"})
PATH = re.compile(r"/previews/([0-9a-f-]{36})/([0-9a-f]{64})\.json\Z")


class OriginError(ValueError):
    pass


def validate_url(url, *, operated_hosts=()):
    """No DNS or HTTP yet. Extra operated hosts supplement, never replace denials."""
    try:
        if (
            type(url) is not str
            or len(url) > 2048
            or not url.isascii()
            or any(ord(c) <= 32 or ord(c) == 127 for c in url)
        ):
            raise ValueError
        parsed = urlsplit(url)
        host = parsed.hostname
        if (
            parsed.scheme != "https"
            or not host
            or parsed.username is not None
            or parsed.password is not None
            or "?" in url
            or "#" in url
            or "\\" in url
            or "%" in host
            or host.endswith(".")
            or parsed.port == 0
        ):
            raise ValueError
        from app.config import settings

        configured = {
            urlsplit(settings.ai_market_url).hostname,
            urlsplit(settings.aimarket_url).hostname,
        } - {None}
        if any(
            host == h or host.endswith("." + h)
            for h in OPERATED_HOSTS | frozenset(operated_hosts) | configured
        ):
            raise OriginError("platform_origin")
        if host == "r2.dev" or host.endswith(".r2.dev"):
            raise OriginError("development_origin")
        try:
            address = ipaddress.ip_address(host)
        except ValueError:
            if "." not in host or not re.fullmatch(r"[a-z0-9.-]+", host):
                raise ValueError
        else:
            if not is_public_address(address):
                raise OriginError("private_address")
        return parsed
    except OriginError:
        raise
    except (ValueError, TypeError):
        raise OriginError("invalid_url") from None


def is_public_address(address):
    return (
        address.is_global
        and not address.is_multicast
        and not address.is_reserved
        and not address.is_loopback
        and not address.is_link_local
        and not address.is_unspecified
        and not (
            address.version == 6
            and (address.ipv4_mapped or address.sixtofour or address.teredo)
        )
    )


def public_addresses(parsed):
    try:
        answers = socket.getaddrinfo(
            parsed.hostname, parsed.port or 443, type=socket.SOCK_STREAM
        )
        addresses = list(dict.fromkeys(answer[4][0] for answer in answers))
        if not addresses or any(
            not is_public_address(ipaddress.ip_address(a)) for a in addresses
        ):
            raise OriginError("private_address")
        return addresses
    except OriginError:
        raise
    except Exception:
        raise OriginError("dns_unavailable") from None


class PinnedHTTPSConnection(http.client.HTTPSConnection):
    def __init__(self, host, port, address):
        super().__init__(
            host, port=port, timeout=5, context=ssl.create_default_context()
        )
        self.address = address

    def connect(self):
        # Address is already validated and is numeric; never resolve hostname again.
        raw = socket.create_connection((self.address, self.port), self.timeout)
        try:
            if ipaddress.ip_address(raw.getpeername()[0]) != ipaddress.ip_address(
                self.address
            ):
                raise OriginError("address_changed")
            self.sock = self._context.wrap_socket(raw, server_hostname=self.host)
        except BaseException:
            raw.close()
            raise


def receipt_bytes(receipt):
    encoded = canonical_json_bytes(receipt)
    if len(encoded) > 8192:
        raise OriginError("receipt_limit")
    return encoded


def capture_receipt(
    url, method, status, headers, *, captured_at, origin, retired=False
):
    """Capture transport observations; only GET CORS is an admission condition."""
    if method not in {"GET", "OPTIONS"} or type(status) is not int:
        raise OriginError("invalid_response")
    validate_url(url)
    if 300 <= status <= 399:
        raise OriginError("redirect")
    if method == "GET" and status not in ({404, 410} if retired else {200}):
        raise OriginError("http_status")
    values = {}
    for key in HEADER_KEYS:
        items = headers.get_all(key)
        if items is not None and len(items) != 1:
            raise OriginError("ambiguous_headers")
        values[key] = items[0] if items else None
    receipt = {
        "url": url,
        "method": method,
        "status": status,
        "captured_at": canonical_rfc3339_utc(captured_at),
        "headers": values,
        "no_set_cookie": True,
    }
    receipt_bytes(receipt)
    if (
        method == "GET"
        and not retired
        and values["access-control-allow-origin"] not in {"*", origin}
    ):
        raise OriginError("cors_origin")
    return receipt


def browser_body(payload, encoding):
    """Return bytes exposed by browser fetch; compression is an observation."""
    encoding = (encoding or "identity").lower()
    try:
        if encoding == "gzip":
            return zlib.decompress(payload, 16 + zlib.MAX_WBITS)
        if encoding == "deflate":
            return zlib.decompress(payload)
        if encoding in {"", "identity"}:
            return payload
    except zlib.error:
        pass
    raise OriginError("package_unreadable")


def verify_hosted_package(
    url, *, origin, expected_sha256, expected_bytes, retired=False, operated_hosts=()
):
    """Local GET byte check + exact OPTIONS preflight; no redirects/proxies/cookies."""
    parsed = validate_url(url, operated_hosts=operated_hosts)
    validate_browser_origin(origin)
    if not retired and (
        type(expected_bytes) is not int
        or not 0 < expected_bytes <= CAPS["envelope_bytes"]
        or not re.fullmatch("[0-9a-f]{64}", expected_sha256)
    ):
        raise OriginError("invalid_expected_package")
    addresses = public_addresses(parsed)
    receipts = []
    try:
        for method in ("GET", "OPTIONS"):
            # Both requests use the same validated address and original TLS hostname.
            connection = PinnedHTTPSConnection(
                parsed.hostname, parsed.port or 443, addresses[0]
            )
            try:
                request_headers = {"Origin": origin, "Accept-Encoding": "identity"}
                if method == "OPTIONS":
                    request_headers["Access-Control-Request-Method"] = "GET"
                connection.request(method, parsed.path or "/", headers=request_headers)
                response = connection.getresponse()
                receipt = capture_receipt(
                    url,
                    method,
                    response.status,
                    response.headers,
                    captured_at=datetime.now(timezone.utc),
                    origin=origin,
                    retired=retired,
                )
                if method == "GET" and not retired:
                    payload = response.read(CAPS["envelope_bytes"] + 1)
                    payload = browser_body(payload, response.headers.get("content-encoding"))
                    if (
                        len(payload) != expected_bytes
                        or hashlib.sha256(payload).hexdigest() != expected_sha256
                    ):
                        raise OriginError("package_mismatch")
                    try:
                        parsed_payload = json.loads(payload)
                    except (UnicodeDecodeError, json.JSONDecodeError):
                        raise OriginError("package_unreadable") from None
                    if not isinstance(parsed_payload, dict):
                        raise OriginError("package_unreadable")
                receipts.append(receipt)
            except Exception:
                if method != "OPTIONS":
                    raise
                observation = {
                    "url": url,
                    "method": "OPTIONS",
                    "status": 0,
                    "captured_at": canonical_rfc3339_utc(datetime.now(timezone.utc)),
                    "headers": {key: None for key in HEADER_KEYS},
                    "no_set_cookie": True,
                }
                receipt_bytes(observation)
                receipts.append(observation)
            finally:
                connection.close()
    except OriginError:
        raise
    except Exception:
        raise OriginError("transport_failed") from None
    return receipts


def validate_browser_origin(origin):
    try:
        p = urlsplit(origin)
        if (
            len(origin) > 2048
            or p.scheme != "https"
            or not p.hostname
            or p.username is not None
            or p.password is not None
            or p.path
            or p.query
            or p.fragment
            or any(ord(c) <= 32 or ord(c) > 126 for c in origin)
        ):
            raise ValueError
        p.port
    except (TypeError, ValueError):
        raise OriginError("invalid_browser_origin") from None


def make_origin(store, *, origin, bind="127.0.0.1", port=0):
    """Read-only origin. Seller supplies the external HTTPS reverse proxy."""
    validate_browser_origin(origin)

    class Handler(BaseHTTPRequestHandler):
        def log_message(self, *_args):
            pass

        def send_error(self, code, message=None, explain=None):
            self._reply(code)

        def _reply(self, status, payload=b""):
            self.send_response(status)
            self.send_header("Content-Type", MEDIA_TYPE)
            self.send_header("Cache-Control", "no-store")
            self.send_header("Access-Control-Allow-Origin", origin)
            self.send_header("Access-Control-Allow-Methods", "GET, OPTIONS")
            self.send_header("Content-Length", str(len(payload)))
            self.end_headers()
            if payload:
                self.wfile.write(payload)

        def do_GET(self):
            match = PATH.fullmatch(self.path)
            if match is None:
                self._reply(404)
                return
            try:
                status, payload = store.read(*match.groups())
                self._reply(status, payload)
            except (PackageError, OSError, ValueError):
                self._reply(404)

        def do_OPTIONS(self):
            self._reply(204 if PATH.fullmatch(self.path) else 404)

    class Server(ThreadingHTTPServer):
        daemon_threads = True

        def handle_error(self, request, client_address):
            pass  # Never print HTTP requests or seller-local paths in tracebacks.

    return Server((bind, port), Handler)
