"""
Fulfillment Protocol Schemas (BQ-D1)
=====================================

Pydantic models for the vai.fulfillment.* transfer protocol messages
between vectorAIz and ai.market.
"""
from __future__ import annotations

from datetime import datetime
from typing import Optional
from uuid import UUID

from pydantic import BaseModel, Field, HttpUrl, field_validator, model_validator


# ---------------------------------------------------------------------------
# Inbound messages (vectorAIz → ai.market)
# ---------------------------------------------------------------------------

class FulfillmentMetadataParams(BaseModel):
    filename: str
    content_type: str
    total_bytes: int
    total_chunks: int
    chunk_size: int = 65536
    sha256_hash: str
    hash_algorithm: str = "sha256"


class FulfillmentMetadataMessage(BaseModel):
    action: str = "vai.fulfillment.metadata"
    message_id: Optional[str] = None
    transfer_id: str
    order_id: str
    listing_id: str
    parameters: FulfillmentMetadataParams


class FulfillmentChunkMessage(BaseModel):
    action: str = "vai.fulfillment.chunk"
    message_id: Optional[str] = None
    transfer_id: str
    chunk_index: int
    byte_offset: int
    payload_length: int
    chunk_sha256: str
    payload: str  # base64 encoded


class FulfillmentCompleteParams(BaseModel):
    status: str = "fulfilled"
    file_size_bytes: int
    chunk_count: int
    sha256_hash: str


class FulfillmentCompleteMessage(BaseModel):
    action: str = "vai.fulfillment.complete"
    message_id: Optional[str] = None
    transfer_id: str
    order_id: str
    parameters: FulfillmentCompleteParams


class FulfillmentResponseParams(BaseModel):
    success: bool
    access_url: HttpUrl
    access_token: Optional[str] = None
    expires_at: datetime
    file_hash: Optional[str] = Field(default=None, pattern=r"^[0-9a-fA-F]{64}$")
    file_size_bytes: Optional[int] = Field(default=None, ge=0)
    error: Optional[str] = None

    @field_validator("access_url")
    @classmethod
    def require_https(cls, value: HttpUrl) -> HttpUrl:
        if value.scheme != "https":
            raise ValueError("access_url must use https")
        return value


class FulfillmentResponseMessage(BaseModel):
    action: str = "vai.fulfillment.response"
    request_id: str = Field(min_length=1)
    order_id: UUID
    # AIM Data fe2cfd75 omits listing_id; resolve it from the owned order.
    listing_id: Optional[UUID] = None
    parameters: FulfillmentResponseParams

    @model_validator(mode="before")
    @classmethod
    def accept_client_identifiers(cls, value):
        if isinstance(value, dict):
            value = dict(value)
            params = value.get("parameters")
            if isinstance(params, dict):
                for field in ("order_id", "listing_id"):
                    if field in params:
                        if field in value and UUID(str(value[field])) != UUID(str(params[field])):
                            raise ValueError(f"Conflicting {field}")
                        value[field] = params[field]
        return value


class FulfillmentErrorParams(BaseModel):
    status: str = "failed"
    error_code: str
    error_message: str


class FulfillmentErrorMessage(BaseModel):
    action: str = "vai.fulfillment.error"
    message_id: Optional[str] = None
    transfer_id: str
    order_id: str
    parameters: FulfillmentErrorParams


# ---------------------------------------------------------------------------
# Outbound messages (ai.market → vectorAIz)
# ---------------------------------------------------------------------------

class FulfillmentAckMessage(BaseModel):
    action: str = "vai.fulfillment.ack"
    transfer_id: str
    acked_through_index: int
    status: str = "continue"
    last_chunk_hash: Optional[str] = None


# ---------------------------------------------------------------------------
# Download endpoint schemas
# ---------------------------------------------------------------------------

class DownloadTokenResponse(BaseModel):
    order_id: UUID
    filename: str
    file_size_bytes: int
    content_type: str
    downloads_remaining: int
    expires_at: datetime
