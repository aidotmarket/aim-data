"""
Marketplace Publish Router
==========================

BQ-VZ-PUBLISH Phase 3: Proxies listing publish requests from VZ frontend
to ai.market backend, signing with Ed25519 JWT.

Flow: VZ Frontend -> VZ Backend (this router) -> ai.market Backend
The Ed25519 private key lives on VZ backend only.
"""

import hashlib
import json
import logging
import re
from datetime import datetime, timezone
from typing import Any, Literal, Optional
from uuid import uuid4

import httpx
import jwt
from fastapi import APIRouter, Depends, HTTPException, Request
from pydantic import BaseModel, Field

from app.auth.api_key_auth import get_current_user
from app.config import settings
from app.core.channel_config import CHANNEL
from app.core.crypto import DeviceCrypto
from app.services.registration_service import ensure_vz_install_registered
from app.services.s3_publish_source_resolver import (
    NotS3PublishSource,
    S3PublishSourceResolution,
    S3PublishSourceResolutionError,
    resolve_s3_publish_source,
)
from app.services.serial_store import get_serial_store

logger = logging.getLogger(__name__)

router = APIRouter()


# ---------------------------------------------------------------------------
# Request / Response models
# ---------------------------------------------------------------------------

class MarketplacePublishRequest(BaseModel):
    title: str = Field(..., max_length=200)
    description: str = Field(..., max_length=5000)
    tags: list[str] = Field(default_factory=list)
    category: Optional[str] = None
    pricing_type: Literal["one_time", "subscription"] = "one_time"
    price_cents: int = Field(..., ge=0)
    row_count: Optional[int] = None
    column_names: Optional[list[str]] = None
    column_types: Optional[list[str]] = None
    file_format: Optional[str] = None
    file_size_bytes: Optional[int] = None
    vz_dataset_id: str  # local VZ dataset ID, becomes vz_raw_listing_id


class MarketplacePublishResponse(BaseModel):
    status: str
    listing_id: Optional[str] = None
    marketplace_url: Optional[str] = None
    error: Optional[str] = None


class S3ConnectionPublishEmit(BaseModel):
    """Strict allowlist for the receiver's S3ConnectionPublish contract."""

    model_config = {"extra": "forbid"}

    bucket: str
    region: str
    role_arn: str
    prefix: str
    serial_id: str


# ---------------------------------------------------------------------------
# Helpers
# ---------------------------------------------------------------------------

VZ_SERIAL_RE = re.compile(r"\bVZ-[A-Za-z0-9][A-Za-z0-9_-]{6,}\b")
HMAC_HEX_RE = re.compile(r"\b[0-9a-fA-F]{32,}\b")


def _jcs_canonical_bytes(body: dict) -> bytes:
    """RFC 8785 JCS-style canonical bytes used by sender/backend parity tests."""
    return json.dumps(
        body, sort_keys=True, separators=(",", ":"), ensure_ascii=False, allow_nan=False
    ).encode("utf-8")


def _jcs_hash(body: dict) -> str:
    """RFC 8785 JCS-style canonical hash (sorted keys, compact separators)."""
    return hashlib.sha256(_jcs_canonical_bytes(body)).hexdigest()


def _build_jwt(seller_id: str, install_id: str, metadata_hash: str, ed_priv) -> str:
    """Create a short-lived EdDSA JWT for the publish action."""
    now = datetime.now(timezone.utc)
    claims = {
        "sub": seller_id,
        "iss": install_id,
        "action": "publish_listing",
        "metadata_hash": metadata_hash,
        "exp": now.timestamp() + 300,
        "iat": now.timestamp(),
        "jti": str(uuid4()),
    }
    return jwt.encode(claims, ed_priv, algorithm="EdDSA")


def _get_crypto() -> DeviceCrypto:
    """Get an initialized DeviceCrypto instance."""
    if not settings.keystore_passphrase:
        raise HTTPException(
            status_code=503,
            detail="Keystore passphrase not configured — cannot sign marketplace requests",
        )
    return DeviceCrypto(
        keystore_path=settings.keystore_path,
        passphrase=settings.keystore_passphrase,
    )


def _build_s3_connection_emit(resolution: S3PublishSourceResolution) -> dict[str, str]:
    """Build the exact allowlisted s3_connection block sent to ai.market."""
    return S3ConnectionPublishEmit(
        bucket=resolution.bucket,
        region=resolution.region,
        role_arn=resolution.role_arn,
        prefix=resolution.prefix,
        serial_id=str(resolution.serial_id),
    ).model_dump(mode="json")


def _build_publish_payload(
    body: MarketplacePublishRequest,
    s3_source: NotS3PublishSource | S3PublishSourceResolution,
) -> dict[str, Any]:
    """Build the single canonical publish payload used for wire body and hash."""
    payload = body.model_dump(exclude_none=True)
    payload["vz_raw_listing_id"] = payload.pop("vz_dataset_id")
    payload["download_channel"] = CHANNEL.value
    if isinstance(s3_source, S3PublishSourceResolution):
        payload["s3_connection"] = _build_s3_connection_emit(s3_source)
    return payload


def _assert_no_sensitive_publish_values(payload: dict[str, Any]) -> None:
    """Reject accidental serial/hash material in seller-controlled publish fields."""

    def _check_string(candidate: str) -> None:
        if VZ_SERIAL_RE.search(candidate) or HMAC_HEX_RE.search(candidate):
            raise HTTPException(status_code=409, detail="Publish payload contains sensitive material")

    for key in ("title", "description", "category", "vz_raw_listing_id"):
        value = payload.get(key)
        if isinstance(value, str):
            _check_string(value)

    tags = payload.get("tags")
    if isinstance(tags, list):
        for tag in tags:
            if isinstance(tag, str):
                _check_string(tag)


# ---------------------------------------------------------------------------
# Endpoints
# ---------------------------------------------------------------------------

@router.post("/marketplace/publish", response_model=MarketplacePublishResponse)
async def publish_to_marketplace(
    body: MarketplacePublishRequest,
    request: Request,
    user=Depends(get_current_user),
):
    """Publish a dataset listing to ai.market via signed JWT proxy."""
    # 1. Load crypto + keypairs
    crypto = _get_crypto()
    ed_priv, _ed_pub, _x_priv, _x_pub = crypto.get_or_create_keypairs()

    # 2. Resolve install_id (iss) and seller_id (sub)
    store = get_serial_store()
    cached = store.state.last_status_cache or {}
    seller_id = (
        store.state.ai_market_seller_id
        or (user.user_id if getattr(user, "key_id", "") == "ai_market_bearer" else None)
        or cached.get("gateway_user_id")
    )
    if not seller_id:
        raise HTTPException(
            status_code=409,
            detail="Seller identity not available — sign in with ai.market before publishing",
        )

    incoming_auth = request.headers.get("Authorization", "")
    incoming_bearer = incoming_auth.removeprefix("Bearer ").strip() if incoming_auth.startswith("Bearer ") else None
    install_id = await ensure_vz_install_registered(
        crypto,
        access_token=store.state.ai_market_access_token or incoming_bearer,
        seller_id=str(seller_id),
    )
    if not install_id:
        raise HTTPException(
            status_code=409,
            detail="VZ install registration not available — sign in with ai.market and try publishing again",
        )

    # 3. Resolve S3 provenance, then build the canonical payload for ai.market.
    try:
        s3_source = resolve_s3_publish_source(body.vz_dataset_id, user, store.state)
    except S3PublishSourceResolutionError as exc:
        raise HTTPException(
            status_code=409,
            detail=(
                "S3-sourced dataset is not eligible for scoped-credential publish yet; "
                f"verify the S3 connection and dataset source, then retry. reason={exc.reason}"
            ),
        )

    payload = _build_publish_payload(body, s3_source)
    _assert_no_sensitive_publish_values(payload)

    # 4. JCS hash + JWT
    metadata_hash = _jcs_hash(payload)
    token = _build_jwt(str(seller_id), install_id, metadata_hash, ed_priv)

    # 5. POST to ai.market
    url = f"{settings.ai_market_url}/api/v1/vz/publish"
    try:
        async with httpx.AsyncClient(timeout=30.0) as client:
            resp = await client.post(
                url,
                json=payload,
                headers={
                    "Authorization": f"Bearer {token}",
                    "Content-Type": "application/json",
                },
            )
    except httpx.ConnectError:
        raise HTTPException(status_code=502, detail="Cannot reach ai.market — check network connectivity")
    except httpx.TimeoutException:
        raise HTTPException(status_code=504, detail="ai.market request timed out")

    # 6. Return response
    if resp.status_code in (200, 201):
        data = resp.json()
        return MarketplacePublishResponse(
            status="published",
            listing_id=data.get("listing_id"),
            marketplace_url=data.get("marketplace_url"),
        )

    # Error passthrough
    try:
        err_data = resp.json()
        detail = err_data.get("detail") or err_data.get("error") or str(err_data)
    except Exception:
        detail = resp.text or f"ai.market returned {resp.status_code}"

    logger.warning("ai.market publish failed (%d): %s", resp.status_code, detail)
    raise HTTPException(status_code=resp.status_code, detail=detail)


@router.get("/marketplace/publish-status")
async def publish_status(user=Depends(get_current_user)):
    """Check if this AIM Data installation is ready to publish to ai.market."""
    # Must have keystore passphrase
    if not settings.keystore_passphrase:
        return {"can_publish": False, "reason": "Keystore passphrase not configured"}

    # Must have keypairs
    try:
        crypto = _get_crypto()
        crypto.get_or_create_keypairs()
    except Exception as e:
        return {"can_publish": False, "reason": f"Keypair error: {e}"}

    # Must have device registration (platform keys)
    if not crypto.has_platform_keys():
        return {"can_publish": False, "reason": "Device not registered with ai.market"}

    return {"can_publish": True, "reason": None}
