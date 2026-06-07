"""
Raw Listing Service
===================

Business logic for raw file marketplace listing CRUD and lifecycle.

Phase: BQ-VZ-RAW-LISTINGS
Created: 2026-03-05
"""

import logging
from pathlib import Path
import uuid
from datetime import datetime, timezone
from typing import Any, Dict, List, Optional, Tuple

import httpx
from sqlmodel import select, func

from app.config import settings
from app.models.raw_file import RawFile
from app.models.raw_listing import RawListing
from app.services.serial_store import get_serial_store

logger = logging.getLogger(__name__)

RAW_MARKETPLACE_CATEGORY_DEFAULT = "other"
RAW_MARKETPLACE_MODEL_PROVIDER = "local"
RAW_MARKETPLACE_LISTING_TYPE = "raw"
RAW_MARKETPLACE_COMPLIANCE_STATUS = "not_checked"
RAW_MARKETPLACE_PRICING_TYPE = "one_time"


class MarketplacePublishError(RuntimeError):
    """Base error for raw listing marketplace publish failures."""

    status_code = 502


class MarketplaceConnectionError(MarketplacePublishError):
    status_code = 502


class MarketplaceAuthError(MarketplacePublishError):
    status_code = 409


class MarketplaceNotConnectedError(MarketplacePublishError):
    status_code = 409


def _get_db_session():
    from app.core.database import get_session_context
    return get_session_context()


def _extract_preview_snippet(raw_file: RawFile) -> str:
    metadata = raw_file.metadata_ or {}
    preview = metadata.get("preview_snippet")
    if isinstance(preview, str):
        return preview[:500]

    path = Path(raw_file.file_path)
    if not path.is_file():
        return ""

    try:
        sample = path.read_bytes()[:2048]
        return sample.decode("utf-8", errors="ignore").strip()[:500]
    except OSError:
        return ""


def _resolve_category(listing: RawListing, raw_file: RawFile) -> str:
    listing_metadata = listing.auto_metadata or {}
    file_metadata = raw_file.metadata_ or {}
    category = listing_metadata.get("category") or file_metadata.get("category")
    if isinstance(category, str) and category.strip():
        return category.strip()
    return RAW_MARKETPLACE_CATEGORY_DEFAULT


class RawListingService:
    """Manages raw file marketplace listing lifecycle."""

    def create_listing(
        self,
        raw_file_id: str,
        title: str,
        description: str,
        tags: Optional[List[str]] = None,
        price_cents: Optional[int] = None,
        category: Optional[str] = None,
    ) -> RawListing:
        listing = RawListing(
            id=str(uuid.uuid4()),
            raw_file_id=raw_file_id,
            title=title,
            description=description,
            tags=tags or [],
            auto_metadata={"category": category} if category else None,
            price_cents=price_cents,
            status="draft",
        )
        with _get_db_session() as session:
            session.add(listing)
            session.commit()
            session.refresh(listing)
            logger.info("Created draft listing %s for file %s", listing.id, raw_file_id)
            return listing

    def update_listing(
        self,
        listing_id: str,
        title: Optional[str] = None,
        description: Optional[str] = None,
        tags: Optional[List[str]] = None,
        price_cents: Optional[int] = None,
        category: Optional[str] = None,
    ) -> Optional[RawListing]:
        with _get_db_session() as session:
            listing = session.exec(
                select(RawListing).where(RawListing.id == listing_id)
            ).first()
            if listing is None:
                return None

            if title is not None:
                listing.title = title
            if description is not None:
                listing.description = description
            if tags is not None:
                listing.tags = tags
            if price_cents is not None:
                listing.price_cents = price_cents
            if category is not None:
                metadata = dict(listing.auto_metadata or {})
                metadata["category"] = category
                listing.auto_metadata = metadata

            listing.updated_at = datetime.now(timezone.utc)
            session.add(listing)
            session.commit()
            session.refresh(listing)
            return listing

    def list_listings(
        self,
        status_filter: Optional[str] = None,
        limit: int = 20,
        offset: int = 0,
    ) -> Tuple[List[RawListing], int]:
        with _get_db_session() as session:
            query = select(RawListing)
            count_query = select(func.count()).select_from(RawListing)

            if status_filter:
                query = query.where(RawListing.status == status_filter)
                count_query = count_query.where(RawListing.status == status_filter)

            total = session.exec(count_query).one()
            listings = list(
                session.exec(
                    query.order_by(RawListing.created_at.desc()).offset(offset).limit(limit)
                ).all()
            )
            return listings, total

    def get_listing_for_file(self, raw_file_id: str) -> Optional[RawListing]:
        """Get the most recent listing for a given raw file, if any."""
        with _get_db_session() as session:
            return session.exec(
                select(RawListing)
                .where(RawListing.raw_file_id == raw_file_id)
                .order_by(RawListing.created_at.desc())
            ).first()

    def get_listing(self, listing_id: str) -> Optional[RawListing]:
        with _get_db_session() as session:
            return session.exec(
                select(RawListing).where(RawListing.id == listing_id)
            ).first()

    def _build_marketplace_payload(self, listing: RawListing, raw_file: RawFile) -> Dict[str, Any]:
        price_cents = listing.price_cents or 0
        price = round(price_cents / 100, 2)
        preview_snippet = _extract_preview_snippet(raw_file)

        return {
            "title": listing.title,
            "description": listing.description,
            "price": price,
            "model_provider": RAW_MARKETPLACE_MODEL_PROVIDER,
            "category": _resolve_category(listing, raw_file),
            "tags": listing.tags or [],
            "schema_info": {
                "type": "raw_file",
                "filename": raw_file.filename,
                "mime_type": raw_file.mime_type,
                "file_size_bytes": raw_file.file_size_bytes,
            },
            "compliance_status": RAW_MARKETPLACE_COMPLIANCE_STATUS,
            "pricing_type": RAW_MARKETPLACE_PRICING_TYPE,
            "data_format": raw_file.mime_type or "application/octet-stream",
            "source_file_name": raw_file.filename,
            "listing_type": RAW_MARKETPLACE_LISTING_TYPE,
            "raw_metadata": {
                "file_size_bytes": raw_file.file_size_bytes,
                "mime_type": raw_file.mime_type,
                "content_hash": raw_file.content_hash,
                "preview_snippet": preview_snippet,
                "tags": listing.tags or [],
            },
            "file_hash": raw_file.content_hash,
        }

    async def _create_marketplace_listing(self, payload: Dict[str, Any]) -> str:
        token = get_serial_store().state.ai_market_access_token
        if not token:
            raise MarketplaceNotConnectedError("Log in to ai.market before publishing this listing.")

        url = f"{settings.ai_market_url.rstrip('/')}/api/v1/listings/"
        try:
            async with httpx.AsyncClient(timeout=30.0) as client:
                response = await client.post(
                    url,
                    json=payload,
                    headers={
                        "Authorization": f"Bearer {token}",
                        "Content-Type": "application/json",
                    },
                )
        except httpx.TimeoutException as exc:
            raise MarketplaceConnectionError("Timed out while publishing to ai.market.") from exc
        except httpx.RequestError as exc:
            raise MarketplaceConnectionError("Could not reach ai.market while publishing.") from exc

        if response.status_code == 201:
            data = response.json()
            marketplace_id = data.get("id") or data.get("listing_id")
            if not marketplace_id:
                raise MarketplacePublishError("ai.market created the listing but did not return a listing id.")
            return str(marketplace_id)

        if response.status_code in (401, 403):
            raise MarketplaceAuthError("ai.market rejected publish; reconnect your account.")

        detail = "ai.market publish failed."
        try:
            body = response.json()
            if isinstance(body, dict):
                detail = str(body.get("detail") or body.get("message") or detail)
        except ValueError:
            if response.text:
                detail = response.text[:300]
        raise MarketplacePublishError(f"{detail} (ai.market status {response.status_code})")

    async def publish_listing(self, listing_id: str) -> RawListing:
        with _get_db_session() as session:
            listing = session.exec(
                select(RawListing).where(RawListing.id == listing_id)
            ).first()
            if listing is None:
                raise FileNotFoundError(f"Listing not found: {listing_id}")
            if listing.status == "listed":
                raise ValueError("Listing is already published")
            if listing.status == "delisted":
                raise ValueError("Cannot publish a delisted listing")

            raw_file = session.exec(
                select(RawFile).where(RawFile.id == listing.raw_file_id)
            ).first()
            if raw_file is None:
                raise FileNotFoundError(f"Raw file not found: {listing.raw_file_id}")

            payload = self._build_marketplace_payload(listing, raw_file)
            marketplace_listing_id = await self._create_marketplace_listing(payload)

            listing.marketplace_listing_id = marketplace_listing_id
            listing.status = "listed"
            listing.published_at = datetime.now(timezone.utc)
            listing.updated_at = datetime.now(timezone.utc)
            session.add(listing)
            session.commit()
            session.refresh(listing)
            logger.info("Published raw listing %s", listing_id)
            return listing

    def delist_listing(self, listing_id: str) -> RawListing:
        with _get_db_session() as session:
            listing = session.exec(
                select(RawListing).where(RawListing.id == listing_id)
            ).first()
            if listing is None:
                raise FileNotFoundError(f"Listing not found: {listing_id}")
            if listing.status != "listed":
                raise ValueError("Only listed listings can be delisted")

            listing.status = "delisted"
            listing.updated_at = datetime.now(timezone.utc)
            session.add(listing)
            session.commit()
            session.refresh(listing)
            logger.info("Delisted raw listing %s", listing_id)
            return listing


_raw_listing_service: Optional[RawListingService] = None


def get_raw_listing_service() -> RawListingService:
    global _raw_listing_service
    if _raw_listing_service is None:
        _raw_listing_service = RawListingService()
    return _raw_listing_service
