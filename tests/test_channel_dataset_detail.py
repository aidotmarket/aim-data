"""
Tests for aim-data publish presentation and optional verification.
"""

import json
import re
from pathlib import Path
from types import SimpleNamespace
from uuid import uuid4

import httpx
import pytest

from app.config import settings
from app.core.database import get_session_context
from app.models.dataset import DatasetRecord as DBDatasetRecord
from app.routers import datasets, marketplace_publish as mp
from app.services.marketplace_action_signer import canonical_json_bytes
from app.services.s3_publish_source_resolver import S3PublishSourceResolution
# Reuse the existing publish helpers; this module originally had no fixtures.
from tests.test_dataset_publish_signed_proxy import (
    _insert_db_record,
    _Processing,
    _processing_record,
    _request,
    _signed_local_client,
)
from tests.test_member_upload_client import local_dataset  # noqa: F401


REPO_ROOT = Path(__file__).resolve().parent.parent
DATASET_DETAIL_PATH = REPO_ROOT / "frontend" / "src" / "pages" / "DatasetDetail.tsx"


def test_aim_data_channel_gets_primary_publish_button_variant():
    assert DATASET_DETAIL_PATH.exists(), f"DatasetDetail.tsx not found at {DATASET_DETAIL_PATH}"
    content = DATASET_DETAIL_PATH.read_text()

    assert 'variant={channel === "marketplace" || channel === "aim-data" ? "default" : "ghost"}' in content


def test_aim_data_channel_shows_publish_to_aimarket_label():
    content = DATASET_DETAIL_PATH.read_text()
    assert '{channel === "marketplace" || channel === "aim-data" ? "Publish to ai.market" : "Publish"}' in content


def test_aim_data_channel_gets_ring_two_styling():
    content = DATASET_DETAIL_PATH.read_text()
    assert 'channel === "marketplace" || channel === "aim-data" ? " ring-2 ring-primary/30" : ""' in content


def _assert_no_verification_keys(value):
    """Check every serialized key, including nested schema/version members."""
    if isinstance(value, dict):
        for key, child in value.items():
            assert not re.search(r"verification|verified|scan|shape[\W_]*label", key, re.I), key
            _assert_no_verification_keys(child)
    elif isinstance(value, list):
        for child in value:
            _assert_no_verification_keys(child)


@pytest.mark.asyncio
async def test_directory_publish_without_opening_verification(local_dataset, monkeypatch):
    assert settings.multi_file_datasets_enabled is True
    # A freshly uploaded directory has no verification receipts or panel action.
    with get_session_context() as session:
        row = session.get(DBDatasetRecord, local_dataset)
        assert json.loads(row.metadata_json or "{}") == {}
    record = _processing_record(local_dataset)
    record.file_type = "directory"
    monkeypatch.setattr(datasets, "load_compliance_report", lambda _: None)
    monkeypatch.setattr(datasets, "load_attestation", lambda _: None)
    version_id = str(uuid4())
    seen = []

    async def receive(request, claims):
        seen.append(request.url.path)
        version = {"version_id": version_id, "version_label": "v1", "status": "pending_members"}
        if request.url.path.endswith("/publish"):
            payload = json.loads(request.content)
            _assert_no_verification_keys(payload)
            assert payload["vz_raw_listing_id"] == local_dataset
            assert len(payload["versions"]) == 1
            emitted = payload["versions"][0]
            version["version_label"] = emitted["version_label"]
            assert emitted["source_kind"] == "aim_data_local"
            assert emitted["members_total"] == 3
            assert emitted["sample_members_total"] == 1
            return httpx.Response(200, json={"listing_id": "listing-directory", "versions": [version]})
        if request.url.path.endswith("/members"):
            _assert_no_verification_keys(json.loads(request.content))
            return httpx.Response(200, json=version)
        assert request.url.path.endswith("/samples/0")
        assert request.content == b"0\n"
        return httpx.Response(200, json={**version, "status": "active", "index": 0})

    _signed_local_client(monkeypatch, receive)
    result = await datasets.publish_to_marketplace(
        local_dataset, _request(),
        body=datasets.PublishDatasetRequest(title="Dataset", description="A folder of data", tags=[]),
        processing=_Processing(record),
        user=SimpleNamespace(user_id="seller-uuid", key_id="ai_market_bearer"), _meter=None,
    )
    assert result["status"] == "published"
    assert result["listing_id"] == "listing-directory"
    assert len(seen) == 4  # listing, two member chunks, selected sample
    assert sum(path.endswith("/publish") for path in seen) == 1
    with get_session_context() as session:
        assert session.get(DBDatasetRecord, local_dataset).listing_id == "listing-directory"


@pytest.mark.asyncio
@pytest.mark.parametrize("enabled", [True, False], ids=["flag-on", "flag-off"])
async def test_single_file_publish_keeps_legacy_payload_bytes(monkeypatch, enabled):
    monkeypatch.setattr(settings, "multi_file_datasets_enabled", enabled)
    dataset_id = str(uuid4())
    _insert_db_record(dataset_id)
    # The literal legacy expectation comes from
    # test_s1097_sender_version_publish::test_no_versions_legacy_publish_payload_is_unchanged.
    expected = {
        "title": "Dataset", "description": "Buyer-facing description",
        "tags": ["finance"], "category": "financial", "pricing_type": "one_time",
        "price_cents": 2500, "vz_raw_listing_id": dataset_id, "download_channel": "direct",
        "s3_connection": {
            "bucket": "seller-bucket", "region": "us-east-1",
            "role_arn": "arn:aws:iam::123456789012:role/aim-data",
            "prefix": "exports/dataset-1", "serial_id": "11111111-2222-3333-4444-555555555555",
        },
    }
    captured = []

    async def receive(request, claims):
        assert request.url.path.endswith("/publish")
        captured.append(request.content)
        payload = json.loads(request.content)
        _assert_no_verification_keys(payload)
        assert canonical_json_bytes(payload) == canonical_json_bytes(expected)
        # Also pin the actual HTTP JSON bytes, including exact keys and order.
        assert request.content == httpx.Request("POST", request.url, json=expected).content
        return httpx.Response(200, json={"listing_id": "listing-single"})

    _signed_local_client(monkeypatch, receive)
    source = S3PublishSourceResolution(**expected["s3_connection"])
    monkeypatch.setattr(mp, "resolve_s3_publish_source", lambda *args: source)
    result = await mp.publish_via_signed_proxy(
        mp.MarketplacePublishRequest(title="Dataset", description="Buyer-facing description",
            tags=["finance"], category="financial", price_cents=2500, vz_dataset_id=dataset_id),
        _request(), SimpleNamespace(user_id="seller-uuid", key_id="ai_market_bearer"),
    )
    assert result["listing_id"] == "listing-single"
    assert len(captured) == 1
