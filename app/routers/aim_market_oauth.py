"""Same-origin loopback OAuth endpoints; no credentials in final navigation."""
import hmac
import json
import time
from urllib.parse import urlencode

from fastapi import APIRouter, HTTPException, Request
from fastapi.responses import JSONResponse, RedirectResponse
from app.services import aim_market_oauth as flow
from app.services.connected_login import complete_connected_login, failure

router = APIRouter(prefix=flow.PATH, route_class=flow.AuthRoute)


def cookie_name(kind):
    return f"aim_data_oauth_{kind}_{flow.settings.oauth_loopback_port}"


def cookie(response, kind, value, request, age=600):
    response.set_cookie(cookie_name(kind), value, max_age=age, path=flow.PATH, httponly=True, samesite="lax", secure=request.url.scheme == "https" or request.url.hostname != "127.0.0.1")


async def csrf(request, origin_error="origin_mismatch", *, allow_provider=False):
    if request.headers.get("origin") != flow.origin():
        raise failure(403, origin_error)
    if request.headers.get("content-type", "").split(";")[0] != "application/json":
        raise failure(403, "csrf_failed")
    try:
        raw = bytearray()
        async for chunk in request.stream():
            raw.extend(chunk[:2049 - len(raw)])
            if len(raw) > 2048:
                raise ValueError
        request._body = bytes(raw)
        def unique(pairs):
            if len(dict(pairs)) != len(pairs):
                raise ValueError
            return dict(pairs)
        data = json.loads(raw, object_pairs_hook=unique)
        record = flow.records.get(flow.digest(request.cookies.get(cookie_name("bootstrap"), "")))
        if not record or not isinstance(data, dict) or set(data) - ({"provider"} if allow_provider else set()) != {"csrf_nonce"} or not isinstance(data["csrf_nonce"], str) or not hmac.compare_digest(record["nonce"], flow.digest(data["csrf_nonce"])):
            raise ValueError
    except (ValueError, TypeError, KeyError):
        raise failure(403, "csrf_failed") from None
    return data


@router.get("/bootstrap")
async def bootstrap(request: Request):
    async with flow.lock:
        flow.cleanup()
        ip_key = "ip:" + flow.digest(request.client.host if request.client else "unknown")
        browser = request.cookies.get(cookie_name("bootstrap")) or flow.random_value()
        key = flow.digest(browser)
        for limit_key in (ip_key, key):
            record = flow.records.get(limit_key, {})
            hits = [t for t in record.get("hits", []) if t > time.monotonic() - 60]
            if len(hits) >= 10:
                raise failure(429, "rate_limited")
            flow.allocate(limit_key, {**record, "hits": hits + [time.monotonic()]})
        nonce = flow.random_value()
        flow.records[key]["nonce"] = flow.digest(nonce)
    try:
        reason = await flow.readiness()
    except HTTPException:
        reason = "backend_unsupported"
    response = JSONResponse({"enabled": reason is None, "reason": reason, "csrf_nonce": nonce, "loopback_origin": flow.origin()}, headers=flow.HEADERS)
    cookie(response, "bootstrap", browser, request)
    return response


@router.post("/start")
async def start(request: Request):
    if request.headers.get("origin") == f"http://localhost:{flow.settings.oauth_loopback_port}":
        raise failure(400, "loopback_origin_required")
    async with flow.lock:
        flow.cleanup()
        data = await csrf(request, allow_provider=True)
    provider = flow.provider_hint(data)
    reason = await flow.readiness()
    if reason:
        raise failure(409 if reason in ("client_disabled", "local_disabled") else 503, "client_disabled" if reason == "local_disabled" else reason)
    async with flow.lock:
        flow.cleanup()
        await csrf(request, allow_provider=True)
        flow.records[flow.digest(request.cookies[cookie_name("bootstrap")])].pop("nonce", None)
        old = flow.digest(request.cookies.get(cookie_name("binding"), ""))
        flow.records.pop(old, None)
        binding, state, verifier = (flow.random_value() for _ in range(3))
        uri = flow.origin() + flow.PATH + "/callback"
        issuer = flow.issuer()
        flow.allocate(flow.digest(binding), {
            "state": flow.digest(state), "verifier": verifier, "uri": uri,
            "issuer": issuer, "client": flow.CLIENT, "scope": flow.SCOPE,
            "created_at": time.monotonic(), "success": "/datasets", "status": "pending",
        })
    response = JSONResponse({"authorization_url": issuer + "/api/v1/oauth/authorize?" + urlencode({
        "response_type": "code", "client_id": flow.CLIENT, "redirect_uri": uri, "scope": flow.SCOPE,
        "state": state, "code_challenge": flow.challenge(verifier), "code_challenge_method": "S256",
        **({"provider": provider} if provider is not None else {}),
    })}, headers=flow.HEADERS)
    cookie(response, "binding", binding, request)
    return response


@router.get("/callback")
async def callback(request: Request):
    if len(request.scope.get("query_string", b"")) > 2048:
        raise failure(400, "invalid_callback")
    pairs = list(request.query_params.multi_items())
    data = dict(pairs)
    if len(pairs) != 2 or set(data) not in ({"code", "state"}, {"error", "state"}) or not data.get("state") or ("error" in data and data["error"] != "access_denied") or ("code" in data and not data["code"]):
        raise failure(400, "invalid_callback")
    if not request.cookies.get(cookie_name("binding")):
        raise failure(400, "invalid_callback")
    key = flow.digest(request.cookies.get(cookie_name("binding"), ""))
    async with flow.lock:
        flow.cleanup()
        record = flow.records.get(key)
        if record is None or "status" not in record:
            raise failure(400, "invalid_callback")
        if record.get("status") != "pending":
            raise failure(410, "transaction_expired")
        if not hmac.compare_digest(record["state"], flow.digest(data["state"])) or record["issuer"] != flow.issuer() or record["uri"] != flow.origin() + flow.PATH + "/callback":
            raise failure(400, "invalid_callback")
        if record["client"] != flow.CLIENT or record["scope"] != flow.SCOPE or record["success"] != "/datasets":
            raise failure(400, "invalid_callback")
        record["status"] = "exchanging"
        verifier = record.pop("verifier")
    try:
        if "error" in data:
            raise failure(400, "access_denied")
        tokens = await flow.exchange_tokens({"grant_type": "authorization_code", "code": data.pop("code"), "code_verifier": verifier, "redirect_uri": record["uri"]})
        async with flow.lock:
            flow.cleanup()
            if flow.records.get(key) is not record:
                raise failure(410, "transaction_expired")
            result = await complete_connected_login(tokens, "oauth")
            status = 200
    except HTTPException as exc:
        detail = exc.detail if isinstance(exc.detail, dict) else {}
        if exc.status_code == 403 and detail.get("error_code") == "client_disabled":
            status, code = 409, "client_disabled"
        elif exc.status_code in (400, 401, 403):
            status, code = 400, "access_denied"
        else:
            status = 503 if exc.status_code == 429 else exc.status_code if exc.status_code in (409, 502, 503, 504) else 502
            code = {409: "client_disabled", 502: "upstream_invalid_response",
                    503: "upstream_unavailable", 504: "upstream_timeout"}[status]
        result = {"error_code": code}
    except Exception:
        status, result = 502, {"error_code": "upstream_invalid_response"}
    finally:
        verifier = None
        data.clear()
    async with flow.lock:
        if flow.records.get(key) is record:
            flow.allocate(key, {"status": "complete", "body": result, "http_status": status}, ttl=60)
    return RedirectResponse("/login/complete", status_code=303, headers=flow.HEADERS)


@router.post("/complete")
async def complete(request: Request):
    async with flow.lock:
        flow.cleanup()
        await csrf(request, origin_error="csrf_failed")
        key = flow.digest(request.cookies.get(cookie_name("binding"), ""))
        record = flow.records.get(key)
        if not record or "status" not in record:
            response = JSONResponse({"error_code": "binding_failed"}, status_code=403, headers=flow.HEADERS)
            cookie(response, "binding", "", request, age=0)
            return response
        if not flow.settings.oauth_enabled:
            flow.records.pop(key, None)
            response = JSONResponse({"error_code": "client_disabled"}, status_code=409, headers=flow.HEADERS)
            cookie(response, "binding", "", request, age=0)
            return response
        if record.get("status") != "complete":
            flow.records.pop(key, None)
            response = JSONResponse({"error_code": "completion_expired"}, status_code=410, headers=flow.HEADERS)
            cookie(response, "binding", "", request, age=0)
            return response
        del flow.records[key]
    response = JSONResponse(record["body"], status_code=record["http_status"], headers=flow.HEADERS)
    cookie(response, "binding", "", request, age=0)
    return response
