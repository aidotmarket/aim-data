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

from pydantic import BaseModel


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
