"""Fixed-issuer OAuth transport and bounded, process-local browser transactions."""
import asyncio
import base64
import hashlib
import secrets
import time
from urllib.parse import urlparse

import httpx
from app.config import settings
from app.services.connected_login import failure, token_pair

CLIENT = "aim_data_desktop_v1"
SCOPE = "aim_data.session"
PATH = "/api/auth/aim-market"
HEADERS = {"Cache-Control": "no-store", "Referrer-Policy": "no-referrer"}
lock = asyncio.Lock()
records = {}


def digest(value):
    return hashlib.sha256(value.encode()).hexdigest()


def origin():
    return f"http://127.0.0.1:{settings.oauth_loopback_port}"


def issuer():
    url = settings.ai_market_url
    if settings.oauth_test_mode:
        parsed = urlparse(url)
        frontend = urlparse(settings.oauth_frontend_origin)
        local_test = parsed.scheme == "http" and parsed.hostname == "127.0.0.1"
        https_test = parsed.scheme == "https" and parsed.hostname == frontend.hostname
        if url == "https://api.ai.market" or frontend.hostname == "ai.market" or not (local_test or https_test):
            raise failure(503, "backend_unsupported")
        if parsed.username or parsed.password or parsed.path or parsed.query or parsed.fragment:
            raise failure(503, "backend_unsupported")
    elif url != "https://api.ai.market":
        raise failure(503, "backend_unsupported")
    return url


async def readiness():
    if not settings.oauth_enabled:
        async with lock:
            for key in list(records):
                if "status" in records[key]:
                    records[key] = {"status": "disabled", "expires": records[key]["expires"]}
        return "local_disabled"
    try:
        async with httpx.AsyncClient(timeout=5.0, follow_redirects=False) as client:
            response = await client.get(f"{issuer()}/api/v1/oauth/clients/{CLIENT}/status")
        if response.status_code == 404:
            return "backend_unsupported"
        data = response.json()
        if response.status_code != 200 or not isinstance(data, dict) or data.get("client_id") != CLIENT or type(data.get("protocol_version")) is not int or data["protocol_version"] != 1 or type(data.get("enabled")) is not bool:
            return "backend_unavailable"
        return None if data["enabled"] else "client_disabled"
    except (httpx.RequestError, ValueError):
        return "backend_unavailable"


async def exchange_tokens(data):
    if not settings.oauth_enabled:
        raise failure(403, "client_disabled")
    try:
        async with httpx.AsyncClient(timeout=15.0, follow_redirects=False) as client:
            response = await client.post(f"{issuer()}/api/v1/oauth/token", data={**data, "client_id": CLIENT})
        body = response.json()
        if not isinstance(body, dict):
            raise ValueError
        if response.status_code != 200:
            code = body.get("error_code")
            status = 403 if code == "client_disabled" else 401 if body.get("error") == "invalid_grant" else response.status_code
            status = status if status in (401, 403, 429) else 503
            headers = {"Retry-After": response.headers["retry-after"]} if "retry-after" in response.headers else None
            raise failure(status, "client_disabled" if code == "client_disabled" else "upstream_unavailable", headers)
        if body.get("scope") != SCOPE or body.get("token_type", "").lower() != "bearer":
            raise ValueError
        return token_pair(body)
    except httpx.TimeoutException:
        raise failure(504, "upstream_timeout") from None
    except httpx.RequestError:
        raise failure(503, "upstream_unavailable") from None
    except (ValueError, AttributeError):
        raise failure(502, "upstream_invalid_response") from None


def cleanup():
    now = time.monotonic()
    for key in list(records):
        if records[key]["expires"] <= now:
            if records[key].get("status") in ("pending", "exchanging", "complete"):
                # Bounded tombstone distinguishes an expired attempt from a forged cookie.
                # No state, verifier or completed credentials survive their TTL.
                records[key] = {"status": "expired", "expires": now + 600}
            else:
                del records[key]


async def cleanup_loop():
    while True:
        await asyncio.sleep(30)
        async with lock:
            cleanup()


def allocate(key, data, ttl=600):
    cleanup()
    if key not in records and len(records) >= 1024:
        raise failure(429, "rate_limited")
    records[key] = {**data, "expires": time.monotonic() + ttl}


def random_value():
    return secrets.token_urlsafe(32)


def challenge(verifier):
    return base64.urlsafe_b64encode(hashlib.sha256(verifier.encode()).digest()).decode().rstrip("=")


# Suppress request-scoped credential/exception output before log buffers/export.
import logging
from contextvars import ContextVar
from fastapi import HTTPException
from fastapi.responses import JSONResponse
from fastapi.routing import APIRoute

_sensitive_auth = ContextVar("aim_data_sensitive_auth", default=False)


class SensitiveAuthFilter(logging.Filter):
    def filter(self, record):
        return not _sensitive_auth.get() and not any(
            path in record.getMessage() for path in ("/auth/aim-market", "/oauth/authorize", "/oauth/token")
        )


class AuthRoute(APIRoute):
    def get_route_handler(self):
        handler = super().get_route_handler()
        if "aim-market" not in self.path:
            return handler
        async def guarded(request):
            guard = SensitiveAuthFilter()
            handlers = list(logging.getLogger().handlers)
            for target in handlers:
                target.addFilter(guard)
            token = _sensitive_auth.set(True)
            try:
                response = await handler(request)
            except HTTPException as exc:
                body = exc.detail if isinstance(exc.detail, dict) else {"error_code": "access_denied" if exc.status_code == 403 else "upstream_invalid_response"}
                response = JSONResponse(body, status_code=exc.status_code, headers=exc.headers)
            except Exception:
                response = JSONResponse({"error_code": "upstream_invalid_response"}, status_code=502)
            finally:
                _sensitive_auth.reset(token)
                for target in handlers:
                    target.removeFilter(guard)
            response.headers.update(HEADERS)
            return response
        return guarded


logging.getLogger("uvicorn.access").addFilter(SensitiveAuthFilter())
