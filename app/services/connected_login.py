"""Shared connected-account completion. Never issues local operator credentials."""
from http.cookies import CookieError, SimpleCookie

import httpx
from fastapi import HTTPException
from app.config import settings


def failure(status, code, headers=None):
    return HTTPException(status_code=status, detail={"error_code": code}, headers=headers)


def token_pair(data):
    if not isinstance(data, dict) or any(
        not isinstance(data.get(key), str) or not data[key].strip() or data[key] in {"null", "undefined"}
        for key in ("access_token", "refresh_token")
    ):
        raise failure(502, "upstream_invalid_response")
    return data


def password_tokens(response):
    try:
        data = response.json()
        values = []
        for header in response.headers.get_list("set-cookie"):
            cookie = SimpleCookie()
            cookie.load(header)
            if "refresh_token" in cookie:
                values.append(cookie["refresh_token"].value)
        if len(values) != 1 or not isinstance(data, dict):
            raise ValueError
        return token_pair({**data, "refresh_token": values[0]})
    except (ValueError, CookieError):
        raise failure(502, "upstream_invalid_response") from None


async def complete_connected_login(data, mode, db=None):
    from app.routers import auth

    token_pair(data)
    try:
        async with httpx.AsyncClient(timeout=10.0, follow_redirects=False) as client:
            response = await client.get(
                f"{settings.ai_market_url}/api/v1/auth/me",
                headers={"Authorization": f"Bearer {data['access_token']}"},
            )
        if response.status_code != 200:
            raise failure(response.status_code if response.status_code in (401, 403) else 502, "identity_failed")
        me = response.json()
        if not isinstance(me, dict) or not isinstance(me.get("id"), str) or not me["id"]:
            raise failure(502, "upstream_invalid_response")
        await auth._handle_ai_market_token(data["access_token"], user_data=me, db=db)
    except httpx.TimeoutException:
        raise failure(504, "upstream_timeout") from None
    except httpx.RequestError:
        raise failure(503, "upstream_unavailable") from None
    except (ValueError, TypeError):
        raise failure(502, "upstream_invalid_response") from None
    registered = None
    if settings.keystore_passphrase:
        try:
            from app.core.crypto import DeviceCrypto
            from app.services.registration_service import ensure_vz_install_registered
            crypto = DeviceCrypto(keystore_path=settings.keystore_path, passphrase=settings.keystore_passphrase)
            crypto.get_or_create_keypairs()
            registered = await ensure_vz_install_registered(crypto, access_token=data["access_token"], seller_id=me["id"])
        except Exception:
            # Registration is best effort; never disclose upstream exception bodies.
            registered = None
    return {
        "access_token": data["access_token"], "refresh_token": data["refresh_token"],
        "token_type": data.get("token_type", "bearer"), "user": me, "auth_mode": mode,
        "onboarding_required": data.get("onboarding_required", False),
        "onboarding_step": data.get("onboarding_step"),
        "registration_status": "registered" if registered else "not_ready",
    }


async def refresh_connected_login(payload):
    mode = payload.get("auth_mode", "password")
    token = payload.get("refresh_token")
    if mode not in ("oauth", "password") or not isinstance(token, str) or not token.strip() or token in ("null", "undefined"):
        raise failure(400, "invalid_request")
    if mode == "oauth":
        from app.services.aim_market_oauth import exchange_tokens
        data = await exchange_tokens({"grant_type": "refresh_token", "refresh_token": token})
    else:
        try:
            async with httpx.AsyncClient(timeout=15.0, follow_redirects=False) as client:
                response = await client.post(
                    f"{settings.ai_market_url}/api/v1/auth/refresh",
                    cookies={"refresh_token": token}, headers={"Origin": settings.oauth_frontend_origin},
                )
            if response.status_code != 200:
                status = response.status_code if response.status_code in (401, 403, 429) else 503
                headers = {"Retry-After": response.headers["retry-after"]} if "retry-after" in response.headers else None
                raise failure(status, "refresh_failed", headers)
            data = password_tokens(response)
        except httpx.TimeoutException:
            raise failure(504, "upstream_timeout") from None
        except httpx.RequestError:
            raise failure(503, "upstream_unavailable") from None
    return await complete_connected_login(data, mode)
