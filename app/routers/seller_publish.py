"""
Seller Publish Router (S723)
============================

Bridges scanned S3 objects to ai.market listings for the AIM Data seller flow.

Two steps:
  POST /api/seller/claim          {email, password}
      One-time: signs into ai.market with the seller's account, registers this
      install's Ed25519 public key under that seller (POST /api/v1/vz/register),
      and stores the seller id locally so subsequent publishes are attributed to
      the seller. Password is used once to obtain a token and is NOT stored.

  POST /api/seller/publish-object {connection_id, object_id, title?, description?, price_cents?}
      Builds a listing from a scanned S3 object, signs it with the device key,
      and posts it to ai.market (POST /api/v1/vz/publish). Requires a prior claim.

Reuses the proven signing helpers from marketplace_publish.py.
"""

import logging
from datetime import datetime, timezone
from typing import Optional

import httpx
from fastapi import APIRouter, HTTPException
from pydantic import BaseModel, Field

from app.config import settings
from app.core.channel_config import CHANNEL
from app.core.database import get_session_context
from app.models.s3_connection import S3Connection
from app.models.s3_object_metadata import S3ObjectMetadata
from app.routers.marketplace_publish import _build_jwt, _get_crypto, _jcs_hash
from app.services.registration_service import _get_device_id
from app.services.serial_store import get_serial_store

logger = logging.getLogger(__name__)

router = APIRouter(prefix="/api/seller", tags=["seller"])


class ClaimRequest(BaseModel):
    email: str
    password: str
    first_name: Optional[str] = None
    last_name: Optional[str] = None
    company_name: Optional[str] = None


class PublishObjectRequest(BaseModel):
    connection_id: str
    object_id: str
    title: Optional[str] = Field(default=None, max_length=200)
    description: Optional[str] = Field(default=None, max_length=5000)
    price_cents: Optional[int] = Field(default=None, ge=0)
    pricing_type: Optional[str] = "one_time"
    tags: Optional[list[str]] = None


def _market() -> str:
    return settings.ai_market_url.rstrip("/")


@router.get("/status")
async def seller_status():
    store = get_serial_store()
    cache = store.state.last_status_cache or {}
    seller_id = cache.get("gateway_user_id")
    has_pass = bool(settings.keystore_passphrase)
    return {
        "claimed": bool(seller_id),
        "seller_id": seller_id,
        "keystore_passphrase_configured": has_pass,
        "ai_market_url": _market(),
    }


async def _login(client, email, password):
    r = await client.post(f"{_market()}/api/v1/auth/login", json={"email": email, "password": password})
    if r.status_code == 401:
        raise HTTPException(status_code=401, detail="Incorrect email or password")
    if r.status_code != 200:
        raise HTTPException(status_code=r.status_code, detail=f"ai.market login failed: {r.text[:300]}")
    tok = r.json().get("access_token")
    if not tok:
        raise HTTPException(status_code=501, detail="2FA accounts not supported for claim; disable 2FA on this account.")
    return tok


async def _complete_seller_onboarding(client, token, first_name, last_name, company_name):
    """Walk the ai.market seller onboarding to completion. Returns list of steps performed."""
    headers = {"Authorization": f"Bearer {token}", "Content-Type": "application/json"}
    performed = []
    for _ in range(8):
        st = await client.get(f"{_market()}/api/v1/auth/onboarding/status", headers=headers)
        if st.status_code != 200:
            raise HTTPException(status_code=502, detail=f"onboarding status failed: {st.text[:200]}")
        sd = st.json()
        if sd.get("completed"):
            return performed
        step = sd.get("current_step")
        if not step:
            return performed
        body = {"step": step}
        if step == "profile":
            body["first_name"] = first_name
            body["last_name"] = last_name
        elif step == "role_selection":
            body["role"] = "seller"
        elif step == "company_info":
            body["company_name"] = company_name
        r = await client.patch(f"{_market()}/api/v1/auth/onboarding/step", headers=headers, json=body)
        if r.status_code not in (200, 201):
            raise HTTPException(status_code=r.status_code, detail=f"onboarding step '{step}' failed: {r.text[:200]}")
        performed.append(step)
    return performed


@router.post("/claim")
async def claim_install(body: ClaimRequest):
    """One-time: complete seller onboarding (if needed) and claim this install under the account."""
    async with httpx.AsyncClient(timeout=20.0) as client:
        # 1. Sign in
        token = await _login(client, body.email, body.password)

        # 2. Resolve account
        me = await client.get(f"{_market()}/api/v1/auth/me", headers={"Authorization": f"Bearer {token}"})
        if me.status_code != 200:
            raise HTTPException(status_code=502, detail=f"Could not read account: {me.text[:200]}")
        me_data = me.json()
        seller_id = me_data.get("id") or me_data.get("user_id")
        if not seller_id:
            raise HTTPException(status_code=502, detail="ai.market /me did not return a user id")

        # 3. Complete seller onboarding if needed (name -> role=seller -> company -> complete)
        fn = body.first_name or me_data.get("first_name") or "Max"
        ln = body.last_name or me_data.get("last_name") or "Robbins"
        co = body.company_name or "Kisa"
        onboarding_steps = await _complete_seller_onboarding(client, token, fn, ln, co)

        # 4. Re-login so the token carries the (possibly new) seller role
        if onboarding_steps:
            token = await _login(client, body.email, body.password)

        # 5. Device Ed25519 public key
        crypto = _get_crypto()
        crypto.get_or_create_keypairs()
        ed_pub_b64, _x_pub_b64 = crypto.get_public_keys_b64()

        # 6. Register this install under the seller
        reg = await client.post(
            f"{_market()}/api/v1/vz/register",
            headers={"Authorization": f"Bearer {token}", "Content-Type": "application/json"},
            json={"public_key_b64": ed_pub_b64},
        )
        if reg.status_code not in (200, 201):
            raise HTTPException(status_code=reg.status_code, detail=f"vz/register failed: {reg.text[:300]}")
        reg_data = reg.json()

    # 7. Persist seller identity locally (no password stored)
    store = get_serial_store()
    store.update_status_cache({"gateway_user_id": seller_id, "install_id": reg_data.get("install_id")}, datetime.now(timezone.utc).isoformat())
    if reg_data.get("install_token"):
        store.transition_to_active(reg_data["install_token"])
    store.save()

    logger.info("Seller claim complete: seller_id=%s install_id=%s onboarding=%s",
                seller_id, reg_data.get("install_id"), onboarding_steps)
    return {
        "claimed": True,
        "seller_id": seller_id,
        "install_id": reg_data.get("install_id"),
        "onboarding_completed_steps": onboarding_steps,
    }


@router.post("/publish-object")
async def publish_object(body: PublishObjectRequest):
    """Publish a scanned S3 object as a listing on ai.market."""
    store = get_serial_store()
    cache = store.state.last_status_cache or {}
    seller_id = cache.get("gateway_user_id")
    if not seller_id:
        raise HTTPException(status_code=409, detail="Install not claimed yet — POST /api/seller/claim first.")

    with get_session_context() as session:
        obj = session.get(S3ObjectMetadata, body.object_id)
        if not obj or obj.connection_id != body.connection_id:
            raise HTTPException(status_code=404, detail="Scanned object not found for this connection")
        conn = session.get(S3Connection, body.connection_id)
        if not conn:
            raise HTTPException(status_code=404, detail="S3 connection not found")
        object_key = obj.object_key
        size_bytes = obj.size_bytes
        content_type = obj.content_type
        bucket = conn.bucket

    fname = object_key.rsplit("/", 1)[-1]
    fmt = (content_type or "").split("/")[-1][:50] or None
    payload = {
        "title": (body.title or fname)[:200],
        "description": (body.description or f"{fname} — data file delivered from S3 bucket {bucket} via AIM Data.")[:5000],
        "tags": body.tags or [],
        "price_cents": body.price_cents if body.price_cents is not None else 2500,
        "pricing_type": body.pricing_type or "one_time",
        "file_format": fmt,
        "file_size_bytes": size_bytes,
        "compliance_status": "not_checked",
        "vz_raw_listing_id": body.object_id,
        "download_channel": CHANNEL.value,
    }
    payload = {k: v for k, v in payload.items() if v is not None}

    install_id = cache.get("install_id")
    if not install_id:
        raise HTTPException(status_code=409, detail="Install id missing — re-run /api/seller/claim.")
    crypto = _get_crypto()
    ed_priv, _ed_pub, _x_priv, _x_pub = crypto.get_or_create_keypairs()
    metadata_hash = _jcs_hash(payload)
    token = _build_jwt(seller_id, install_id, metadata_hash, ed_priv)

    async with httpx.AsyncClient(timeout=30.0) as client:
        resp = await client.post(
            f"{_market()}/api/v1/vz/publish",
            json=payload,
            headers={"Authorization": f"Bearer {token}", "Content-Type": "application/json"},
        )
    if resp.status_code in (200, 201):
        d = resp.json()
        return {
            "status": "published",
            "listing_id": d.get("listing_id"),
            "marketplace_url": d.get("marketplace_url"),
            "object_key": object_key,
        }
    raise HTTPException(status_code=resp.status_code, detail=f"vz/publish failed: {resp.text[:400]}")
