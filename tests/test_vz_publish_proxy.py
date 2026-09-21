from contextlib import nullcontext
from types import SimpleNamespace
from unittest.mock import AsyncMock

import jwt
import pytest
from cryptography.hazmat.primitives.asymmetric.ed25519 import Ed25519PrivateKey
from fastapi import HTTPException
from pydantic import ValidationError
from starlette.requests import Request

from app.routers import marketplace_publish
from app.models.compliance_schemas import ComplianceReport
from app.models.listing_metadata_schemas import ColumnSummary, ListingMetadata
from app.services.processing_service import DatasetRecord, ProcessingStatus
from app.services import registration_service
from app.services.s3_publish_source_resolver import (
    NotS3PublishSource,
    S3PublishSourceResolution,
    S3PublishSourceResolutionError,
)
from app.services import serial_store


class _MockResponse:
    def __init__(self, status_code: int, data: dict):
        self.status_code = status_code
        self._data = data
        self.text = str(data)

    def json(self):
        return self._data


class _MockAsyncClient:
    def __init__(self, response: _MockResponse, capture: dict | None = None, **_kwargs):
        self._response = response
        self._capture = capture

    async def __aenter__(self):
        return self

    async def __aexit__(self, *_args):
        return False

    async def post(self, url, **kwargs):
        if self._capture is not None:
            self._capture["url"] = url
            self._capture["kwargs"] = kwargs
        return self._response


class _Store:
    def __init__(self):
        self.state = SimpleNamespace(
            state=serial_store.UNPROVISIONED,
            serial="",
            install_token=None,
            vz_install_serial_bound=False,
            vz_install_id=None,
            vz_install_token=None,
            ai_market_access_token="seller-token",
            ai_market_seller_id="seller-uuid",
            serial_id="11111111-2222-3333-4444-555555555555",
            last_status_cache={},
        )

    def persist_vz_install(self, install_id: str, install_token: str | None = None, serial_bound: bool = False):
        self.state.vz_install_id = install_id
        self.state.vz_install_token = install_token
        self.state.vz_install_serial_bound = serial_bound

    def save(self):
        pass


class _Processing:
    def __init__(self, record):
        self.record = record
        self.saved = 0

    def get_dataset(self, dataset_id: str):
        return self.record if dataset_id == self.record.id else None

    def _save_record(self, record, storage_filename: str):
        self.saved += 1
        self.record = record
        self.storage_filename = storage_filename


def _disclosure_body(**overrides):
    body = {
        "dataset_id": "ds-1",
        "approved_fields": {
            "title": "Customer Spend",
            "description": "Buyer-facing spend data.",
            "category": "retail",
            "tags": ["customers"],
            "schema": {"columns": [{"name": "segment", "type": "string"}]},
            "data_format": "csv",
            "source_row_count": 10,
            "source_column_count": 1,
            "compliance_summary": {},
            "source_delivery_public_metadata": {"file_format": "csv"},
        },
        "sample_decision": "none",
        "approved_sample": None,
        "ai_training_notification_ack": True,
        "ai_training_notification_text": "When I publish, my approved title, description, tags, category, schema, and sample-row choice become public on ai.market.",
        "license": "standard_marketplace",
        "approval_source": "aim_channel",
        "source_publish_operation_id": "op-1",
    }
    body.update(overrides)
    return marketplace_publish.DisclosureSnapshotProxyRequest(**body)


@pytest.mark.asyncio
async def test_ensure_vz_install_registered_posts_public_key_and_persists(monkeypatch):
    store = _Store()
    public_key = "a" * 43
    crypto = SimpleNamespace(get_public_keys_b64=lambda: (public_key, "x-public"))
    capture: dict = {}

    monkeypatch.setattr(serial_store, "get_serial_store", lambda: store)
    monkeypatch.setattr(registration_service.settings, "ai_market_url", "https://ai.market.test")
    monkeypatch.setattr(
        registration_service.httpx,
        "AsyncClient",
        lambda **kwargs: _MockAsyncClient(
            _MockResponse(201, {"install_id": "install-uuid", "install_token": "vzi_token"}),
            capture,
            **kwargs,
        ),
    )

    install_id = await registration_service.ensure_vz_install_registered(
        crypto,
        access_token="seller-token",
        seller_id="seller-uuid",
    )

    assert install_id == "install-uuid"
    assert store.state.vz_install_id == "install-uuid"
    assert store.state.vz_install_token == "vzi_token"
    assert capture["url"] == "https://ai.market.test/api/v1/vz/register"
    assert capture["kwargs"]["json"] == {"public_key_b64": public_key}
    assert capture["kwargs"]["headers"]["Authorization"] == "Bearer seller-token"


@pytest.mark.asyncio
async def test_publish_proxy_jwt_iss_is_backend_install_id(monkeypatch):
    private_key = Ed25519PrivateKey.generate()
    store = _Store()
    store.state.vz_install_id = "11111111-1111-4111-8111-111111111111"
    capture: dict = {}

    monkeypatch.setattr(marketplace_publish, "get_serial_store", lambda: store)
    monkeypatch.setattr(marketplace_publish, "_get_crypto", lambda: SimpleNamespace(
        get_or_create_keypairs=lambda: (private_key, None, None, None),
        has_platform_keys=lambda: True,
    ))
    ensure_mock = AsyncMock(return_value=store.state.vz_install_id)
    monkeypatch.setattr(marketplace_publish, "ensure_vz_install_registered", ensure_mock)
    monkeypatch.setattr(
        marketplace_publish,
        "resolve_s3_publish_source",
        lambda *_args, **_kwargs: NotS3PublishSource(),
    )
    monkeypatch.setattr(marketplace_publish.settings, "ai_market_url", "https://ai.market.test")
    monkeypatch.setattr(
        marketplace_publish.httpx,
        "AsyncClient",
        lambda **kwargs: _MockAsyncClient(
            _MockResponse(201, {"listing_id": "listing-1", "marketplace_url": "https://ai.market/listings/listing-1"}),
            capture,
            **kwargs,
        ),
    )

    request = Request({
        "type": "http",
        "method": "POST",
        "path": "/api/marketplace/publish",
        "headers": [(b"authorization", b"Bearer seller-token")],
    })
    body = marketplace_publish.MarketplacePublishRequest(
        title="A file",
        description="Buyer-facing description",
        tags=["finance"],
        category="financial",
        price_cents=2500,
        vz_dataset_id="raw-file-1",
    )

    response = await marketplace_publish.publish_to_marketplace(
        body,
        request,
        user=SimpleNamespace(user_id="seller-uuid", key_id="ai_market_bearer"),
    )

    token = capture["kwargs"]["headers"]["Authorization"].removeprefix("Bearer ")
    claims = jwt.decode(token, options={"verify_signature": False}, algorithms=["EdDSA"])
    assert claims["iss"] == store.state.vz_install_id
    assert claims["sub"] == "seller-uuid"
    assert capture["kwargs"]["json"]["vz_raw_listing_id"] == "raw-file-1"
    assert "s3_connection" not in capture["kwargs"]["json"]
    assert response.status == "published"


def _publish_request() -> Request:
    return Request({
        "type": "http",
        "method": "POST",
        "path": "/api/marketplace/publish",
        "headers": [(b"authorization", b"Bearer seller-token")],
    })


def _publish_body(**overrides) -> marketplace_publish.MarketplacePublishRequest:
    data = {
        "title": "A file",
        "description": "Buyer-facing description",
        "tags": ["finance"],
        "category": "financial",
        "price_cents": 2500,
        "vz_dataset_id": "raw-file-1",
    }
    data.update(overrides)
    return marketplace_publish.MarketplacePublishRequest(**data)


async def _capture_router_publish(monkeypatch, body, processing):
    captured = {}

    async def _publish(outbound, request, user):
        captured["body"] = outbound
        return {"listing_id": "listing-1", "status": "published"}

    monkeypatch.setattr(marketplace_publish, "publish_via_signed_proxy", _publish)
    await marketplace_publish.publish_to_marketplace(
        body,
        _publish_request(),
        user=SimpleNamespace(user_id="seller-uuid", key_id="ai_market_bearer"),
        processing=processing,
    )
    return captured["body"]


@pytest.mark.asyncio
async def test_ui_publish_fills_schema_from_pipeline_outputs(monkeypatch, tmp_path):
    dataset_id = "pipeline-dataset"
    processed_root = tmp_path / "processed"
    output_dir = processed_root / dataset_id
    output_dir.mkdir(parents=True)
    (output_dir / "listing_metadata.json").write_text("{}", encoding="utf-8")
    listing_metadata = ListingMetadata(
        title="Generated title",
        description="Generated description",
        tags=["generated"],
        column_summary=[
            ColumnSummary(
                name="amount",
                type="DOUBLE",
                null_percentage=1.5,
                uniqueness_ratio=0.75,
                sample_values=["private-value"],
            )
        ],
        row_count=42,
        column_count=1,
        file_format="parquet",
        size_bytes=1234,
        privacy_score=9.0,
    )
    compliance = ComplianceReport(
        dataset_id=dataset_id,
        compliance_score=95,
        pii_entities_found=[],
        flags=[],
        generated_at="2026-09-21T12:00:00Z",
    )
    record = DatasetRecord(dataset_id, "seller.parquet", "parquet")
    record.status = ProcessingStatus.PREVIEW_READY
    record.metadata["source_type"] = "document"
    processing = _Processing(record)

    monkeypatch.setattr(marketplace_publish.settings, "processed_directory", str(processed_root))
    monkeypatch.setattr(marketplace_publish, "load_listing_metadata", lambda path: listing_metadata)
    monkeypatch.setattr(marketplace_publish, "load_compliance_report", lambda path: compliance)
    monkeypatch.setattr(marketplace_publish, "load_attestation", lambda path: None)

    outbound = await _capture_router_publish(
        monkeypatch,
        _publish_body(
            vz_dataset_id=dataset_id,
            title="Seller title",
            description="Seller description",
            tags=["seller-tag"],
            category="seller-category",
            price_cents=7300,
        ),
        processing,
    )

    assert isinstance(outbound, marketplace_publish.MarketplacePublishRequest)
    assert (outbound.title, outbound.description, outbound.tags) == (
        "Seller title",
        "Seller description",
        ["seller-tag"],
    )
    assert (outbound.category, outbound.price_cents) == ("seller-category", 7300)
    assert outbound.schema_info == {
        "columns": [
            {
                "name": "amount",
                "type": "DOUBLE",
                "null_percentage": 1.5,
                "uniqueness_ratio": 0.75,
            }
        ],
        "row_count": 42,
        "column_count": 1,
        "file_format": "parquet",
        "size_bytes": 1234,
    }
    assert outbound.compliance_status == "low_risk"
    assert outbound.compliance_details == {"score": 95.0, "pii_entities": [], "flags": []}
    assert "sample_values" not in str(outbound.schema_info)


@pytest.mark.asyncio
async def test_ui_publish_fills_schema_from_processed_parquet(monkeypatch, tmp_path):
    dataset_id = "parquet-only-dataset"
    processed_path = tmp_path / f"{dataset_id}.parquet"
    processed_path.write_bytes(b"parquet-placeholder")
    record = DatasetRecord(dataset_id, "seller.csv", "csv")
    record.status = ProcessingStatus.PREVIEW_READY
    record.processed_path = processed_path
    processing = _Processing(record)
    enhanced = {
        "row_count": 17,
        "column_count": 2,
        "file_type": "parquet",
        "size_bytes": 987,
        "column_profiles": [
            {
                "name": "customer_id",
                "type": "BIGINT",
                "null_percentage": 0.0,
                "uniqueness_ratio": 1.0,
                "sample_values": ["123"],
            },
            {
                "name": "segment",
                "type": "VARCHAR",
                "null_percentage": 2.0,
                "uniqueness_ratio": 0.2,
                "sample_values": ["private"],
            },
        ],
    }
    duckdb = SimpleNamespace(get_enhanced_metadata=lambda path: enhanced)
    monkeypatch.setattr(
        marketplace_publish,
        "ephemeral_duckdb_service",
        lambda: nullcontext(duckdb),
    )

    outbound = await _capture_router_publish(
        monkeypatch,
        _publish_body(vz_dataset_id=dataset_id),
        processing,
    )

    assert isinstance(outbound, marketplace_publish.MarketplacePublishRequest)
    assert outbound.row_count == 17
    assert outbound.file_format == "parquet"
    assert outbound.file_size_bytes == 987
    assert outbound.schema_info == {
        "columns": [
            {
                "name": "customer_id",
                "type": "BIGINT",
                "null_percentage": 0.0,
                "uniqueness_ratio": 1.0,
            },
            {
                "name": "segment",
                "type": "VARCHAR",
                "null_percentage": 2.0,
                "uniqueness_ratio": 0.2,
            },
        ],
        "row_count": 17,
        "column_count": 2,
        "file_format": "parquet",
        "size_bytes": 987,
    }
    assert outbound.compliance_details is None
    assert outbound.compliance_status is None
    assert "sample_values" not in str(outbound.schema_info)


@pytest.mark.asyncio
async def test_ui_publish_malformed_pipeline_metadata_falls_back_to_processed_file(
    monkeypatch, tmp_path, caplog
):
    dataset_id = "malformed-pipeline-metadata"
    processed_root = tmp_path / "processed"
    output_dir = processed_root / dataset_id
    output_dir.mkdir(parents=True)
    (output_dir / "listing_metadata.json").write_text('{"title":', encoding="utf-8")
    processed_path = tmp_path / f"{dataset_id}.parquet"
    processed_path.write_bytes(b"parquet-placeholder")
    record = DatasetRecord(dataset_id, "seller.csv", "csv")
    record.status = ProcessingStatus.PREVIEW_READY
    record.processed_path = processed_path
    processing = _Processing(record)
    enhanced = {
        "row_count": 3,
        "column_count": 1,
        "file_type": "parquet",
        "size_bytes": 42,
        "column_profiles": [
            {
                "name": "seller_column",
                "type": "VARCHAR",
                "null_percentage": 0.0,
                "uniqueness_ratio": 1.0,
            }
        ],
    }
    duckdb = SimpleNamespace(get_enhanced_metadata=lambda path: enhanced)
    monkeypatch.setattr(marketplace_publish.settings, "processed_directory", str(processed_root))
    monkeypatch.setattr(
        marketplace_publish,
        "ephemeral_duckdb_service",
        lambda: nullcontext(duckdb),
    )

    outbound = await _capture_router_publish(
        monkeypatch,
        _publish_body(vz_dataset_id=dataset_id),
        processing,
    )

    assert outbound.schema_info["columns"][0]["name"] == "seller_column"
    assert outbound.row_count == 3
    warnings = [record for record in caplog.records if record.levelname == "WARNING"]
    assert len(warnings) == 1
    assert dataset_id in warnings[0].getMessage()
    assert "JSONDecodeError" in warnings[0].getMessage()


@pytest.mark.asyncio
async def test_ui_publish_corrupt_processed_file_is_unchanged(monkeypatch, tmp_path, caplog):
    dataset_id = "corrupt-processed-file"
    processed_path = tmp_path / f"{dataset_id}.parquet"
    processed_path.write_bytes(b"not-a-supported-parquet")
    record = DatasetRecord(dataset_id, "seller.csv", "csv")
    record.status = ProcessingStatus.PREVIEW_READY
    record.processed_path = processed_path
    original = _publish_body(vz_dataset_id=dataset_id, row_count=None, column_names=None)

    def _raise_corrupt_file(_path):
        raise RuntimeError("corrupt parquet")

    duckdb = SimpleNamespace(get_enhanced_metadata=_raise_corrupt_file)
    monkeypatch.setattr(
        marketplace_publish,
        "ephemeral_duckdb_service",
        lambda: nullcontext(duckdb),
    )

    outbound = await _capture_router_publish(monkeypatch, original, _Processing(record))

    assert outbound.model_dump() == original.model_dump()
    warnings = [record for record in caplog.records if record.levelname == "WARNING"]
    assert len(warnings) == 1
    assert dataset_id in warnings[0].getMessage()
    assert "RuntimeError" in warnings[0].getMessage()


@pytest.mark.asyncio
async def test_ui_publish_document_skips_processed_block_columns(monkeypatch, tmp_path):
    dataset_id = "document-dataset"
    processed_path = tmp_path / f"{dataset_id}.parquet"
    processed_path.write_bytes(b"parquet-placeholder")
    record = DatasetRecord(dataset_id, "seller.pdf", "pdf")
    record.status = ProcessingStatus.PREVIEW_READY
    record.processed_path = processed_path
    record.metadata["source_type"] = "document"
    original = _publish_body(vz_dataset_id=dataset_id, row_count=None, column_names=None)
    monkeypatch.setattr(
        marketplace_publish,
        "ephemeral_duckdb_service",
        lambda: pytest.fail("document block columns must not be published"),
    )

    outbound = await _capture_router_publish(monkeypatch, original, _Processing(record))

    assert outbound.model_dump() == original.model_dump()


@pytest.mark.asyncio
async def test_ui_publish_without_local_evidence_is_unchanged(monkeypatch, tmp_path):
    dataset_id = "no-local-evidence"
    record = DatasetRecord(dataset_id, "seller.csv", "csv")
    record.status = ProcessingStatus.PREVIEW_READY
    record.processed_path = tmp_path / "missing.parquet"
    original = _publish_body(vz_dataset_id=dataset_id, row_count=None, column_names=None)

    outbound = await _capture_router_publish(monkeypatch, original, _Processing(record))

    assert isinstance(outbound, marketplace_publish.MarketplacePublishRequest)
    assert outbound.model_dump() == original.model_dump()


@pytest.mark.asyncio
async def test_ui_publish_preserves_caller_supplied_schema(monkeypatch, tmp_path):
    dataset_id = "caller-schema"
    processed_root = tmp_path / "processed"
    output_dir = processed_root / dataset_id
    output_dir.mkdir(parents=True)
    (output_dir / "listing_metadata.json").write_text("{}", encoding="utf-8")
    processed_path = tmp_path / f"{dataset_id}.parquet"
    processed_path.write_bytes(b"parquet-placeholder")
    record = DatasetRecord(dataset_id, "seller.csv", "csv")
    record.status = ProcessingStatus.PREVIEW_READY
    record.processed_path = processed_path
    supplied_schema = {"columns": [{"name": "seller_column", "type": "seller_type"}]}
    original = _publish_body(vz_dataset_id=dataset_id, schema_info=supplied_schema)

    monkeypatch.setattr(marketplace_publish.settings, "processed_directory", str(processed_root))
    monkeypatch.setattr(
        marketplace_publish,
        "load_listing_metadata",
        lambda path: pytest.fail("caller-supplied schema must bypass local enrichment"),
    )
    outbound = await _capture_router_publish(monkeypatch, original, _Processing(record))

    assert isinstance(outbound, marketplace_publish.MarketplacePublishRequest)
    assert outbound.model_dump() == original.model_dump()
    assert outbound.schema_info is original.schema_info


def _rich_listing_fields() -> dict:
    return {
        "schema_info": {
            "columns": [
                {
                    "name": "customer_segment",
                    "type": "string",
                    "null_percentage": 0.0,
                    "uniqueness_ratio": 0.92,
                },
                {
                    "name": "monthly_spend",
                    "type": "float",
                    "null_percentage": 1.2,
                    "uniqueness_ratio": 0.87,
                },
            ],
            "row_count": 1200,
            "column_count": 2,
            "file_format": "csv",
            "size_bytes": 4096,
            "attestation": {
                "data_hash": "a" * 64,
                "attestation_hash": "B" * 128,
                "completeness_score": 0.99,
                "type_consistency_score": 0.98,
                "freshness_score": 0.97,
                "quality_grade": "A",
                "generated_at": "2026-06-30T12:00:00Z",
            },
        },
        "compliance_details": {
            "score": 9.1,
            "pii_entities": [],
            "flags": ["aggregated", "deidentified"],
        },
        "privacy_score": 8.7,
        "secondary_categories": ["analytics", "retail"],
        "model_provider": "open-source-tabular-model",
    }


def _patch_publish_dependencies(monkeypatch, *, store=None, capture=None, s3_source=None):
    private_key = Ed25519PrivateKey.generate()
    store = store or _Store()
    capture = capture if capture is not None else {}
    store.state.vz_install_id = "11111111-1111-4111-8111-111111111111"

    monkeypatch.setattr(marketplace_publish, "get_serial_store", lambda: store)
    monkeypatch.setattr(marketplace_publish, "_get_crypto", lambda: SimpleNamespace(
        get_or_create_keypairs=lambda: (private_key, None, None, None),
        has_platform_keys=lambda: True,
    ))
    monkeypatch.setattr(
        marketplace_publish,
        "ensure_vz_install_registered",
        AsyncMock(return_value=store.state.vz_install_id),
    )
    monkeypatch.setattr(marketplace_publish.settings, "ai_market_url", "https://ai.market.test")
    monkeypatch.setattr(
        marketplace_publish,
        "resolve_s3_publish_source",
        lambda *_args, **_kwargs: s3_source if s3_source is not None else NotS3PublishSource(),
    )
    monkeypatch.setattr(
        marketplace_publish.httpx,
        "AsyncClient",
        lambda **kwargs: _MockAsyncClient(
            _MockResponse(201, {"listing_id": "listing-1", "marketplace_url": "https://ai.market/listings/listing-1"}),
            capture,
            **kwargs,
        ),
    )
    return store, capture


def test_build_publish_payload_includes_rich_listing_fields_without_privacy_status():
    rich_fields = _rich_listing_fields()

    payload = marketplace_publish._build_publish_payload(
        _publish_body(**rich_fields),
        NotS3PublishSource(),
    )

    for key, value in rich_fields.items():
        assert payload[key] == value
    assert payload["vz_raw_listing_id"] == "raw-file-1"
    assert "privacy_scan_status" not in payload


@pytest.mark.parametrize(
    ("field", "value"),
    [
        ("schema_info", {"columns": [{"name": "VZ-ABC1234XY", "type": "string"}]}),
        ("compliance_details", {"flags": ["0123456789abcdef0123456789abcdef"]}),
    ],
)
def test_sensitive_assertion_recurses_into_rich_listing_fields(field, value):
    payload = marketplace_publish._build_publish_payload(
        _publish_body(**{field: value}),
        NotS3PublishSource(),
    )

    with pytest.raises(HTTPException) as exc_info:
        marketplace_publish._assert_no_sensitive_publish_values(payload)

    assert exc_info.value.status_code == 409
    assert exc_info.value.detail == "Publish payload contains sensitive material"


def test_sensitive_assertion_allows_valid_attestation_hashes():
    payload = marketplace_publish._build_publish_payload(
        _publish_body(schema_info=_rich_listing_fields()["schema_info"]),
        NotS3PublishSource(),
    )

    marketplace_publish._assert_no_sensitive_publish_values(payload)


@pytest.mark.parametrize(
    "attestation",
    [
        {"data_hash": "a" * 63, "attestation_hash": "b" * 128},
        {"data_hash": "g" * 64, "attestation_hash": "b" * 128},
        {"data_hash": "a" * 64, "attestation_hash": "b" * 127},
    ],
)
def test_sensitive_assertion_rejects_malformed_attestation_hash(attestation):
    schema_info = {
        **_rich_listing_fields()["schema_info"],
        "attestation": {
            **_rich_listing_fields()["schema_info"]["attestation"],
            **attestation,
        },
    }
    payload = marketplace_publish._build_publish_payload(
        _publish_body(schema_info=schema_info),
        NotS3PublishSource(),
    )

    with pytest.raises(HTTPException) as exc_info:
        marketplace_publish._assert_no_sensitive_publish_values(payload)

    assert exc_info.value.status_code == 409
    assert exc_info.value.detail == "Publish payload contains invalid attestation hash"


@pytest.mark.asyncio
async def test_s3_publish_attaches_s3_connection_and_hash_covers_it(monkeypatch):
    s3_source = S3PublishSourceResolution(
        bucket="seller-bucket",
        region="us-east-1",
        role_arn="arn:aws:iam::123456789012:role/aim-data-delivery",
        prefix="exports/dataset-1",
        serial_id="11111111-2222-3333-4444-555555555555",
    )
    _store, capture = _patch_publish_dependencies(monkeypatch, s3_source=s3_source)

    response = await marketplace_publish.publish_to_marketplace(
        _publish_body(),
        _publish_request(),
        user=SimpleNamespace(user_id="seller-uuid", key_id="ai_market_bearer"),
    )

    payload = capture["kwargs"]["json"]
    assert response.status == "published"
    assert payload["s3_connection"] == {
        "bucket": "seller-bucket",
        "region": "us-east-1",
        "role_arn": "arn:aws:iam::123456789012:role/aim-data-delivery",
        "prefix": "exports/dataset-1",
        "serial_id": "11111111-2222-3333-4444-555555555555",
    }

    token = capture["kwargs"]["headers"]["Authorization"].removeprefix("Bearer ")
    claims = jwt.decode(token, options={"verify_signature": False}, algorithms=["EdDSA"])
    assert claims["metadata_hash"] == marketplace_publish._jcs_hash(payload)

    changed_serial = {
        **payload,
        "s3_connection": {**payload["s3_connection"], "serial_id": "99999999-2222-3333-4444-555555555555"},
    }
    changed_prefix = {
        **payload,
        "s3_connection": {**payload["s3_connection"], "prefix": "exports/dataset-2"},
    }
    assert marketplace_publish._jcs_hash(changed_serial) != claims["metadata_hash"]
    assert marketplace_publish._jcs_hash(changed_prefix) != claims["metadata_hash"]


@pytest.mark.asyncio
async def test_non_s3_publish_attaches_no_s3_connection(monkeypatch):
    _store, capture = _patch_publish_dependencies(monkeypatch, s3_source=NotS3PublishSource())

    await marketplace_publish.publish_to_marketplace(
        _publish_body(),
        _publish_request(),
        user=SimpleNamespace(user_id="seller-uuid", key_id="ai_market_bearer"),
    )

    assert "s3_connection" not in capture["kwargs"]["json"]


@pytest.mark.asyncio
async def test_publish_accepts_clean_realistic_full_payload(monkeypatch):
    _store, capture = _patch_publish_dependencies(monkeypatch, s3_source=NotS3PublishSource())

    response = await marketplace_publish.publish_to_marketplace(
        _publish_body(**_rich_listing_fields()),
        _publish_request(),
        user=SimpleNamespace(user_id="seller-uuid", key_id="ai_market_bearer"),
    )

    payload = capture["kwargs"]["json"]
    assert response.status == "published"
    assert payload["schema_info"]["columns"][0]["name"] == "customer_segment"
    assert payload["schema_info"]["attestation"]["data_hash"] == "a" * 64
    assert payload["schema_info"]["attestation"]["attestation_hash"] == "B" * 128
    assert payload["compliance_details"]["flags"] == ["aggregated", "deidentified"]
    assert payload["privacy_score"] == 8.7
    assert payload["secondary_categories"] == ["analytics", "retail"]
    assert payload["model_provider"] == "open-source-tabular-model"
    assert "privacy_scan_status" not in payload


@pytest.mark.asyncio
async def test_non_s3_publish_allows_sensitive_words_in_user_free_text(monkeypatch):
    _store, capture = _patch_publish_dependencies(monkeypatch, s3_source=NotS3PublishSource())

    await marketplace_publish.publish_to_marketplace(
        _publish_body(
            title="Trade Secrets Dataset",
            description="Tokenizer notes include secret handling and external id mapping.",
            tags=["tokenizer", "secret", "token"],
            column_names=["external_id", "token", "secret"],
            column_types=["string"],
        ),
        _publish_request(),
        user=SimpleNamespace(user_id="seller-uuid", key_id="ai_market_bearer"),
    )

    payload = capture["kwargs"]["json"]
    assert payload["description"].startswith("Tokenizer notes")
    assert payload["column_names"] == ["external_id", "token", "secret"]
    assert "s3_connection" not in payload


@pytest.mark.asyncio
async def test_s3_publish_allows_serial_shaped_s3_connection_serial_id(monkeypatch):
    s3_source = S3PublishSourceResolution(
        bucket="seller-bucket",
        region="us-east-1",
        role_arn="arn:aws:iam::123456789012:role/aim-data-delivery",
        prefix="exports/dataset-1",
        serial_id="VZ-ABC1234XY",
    )
    _store, capture = _patch_publish_dependencies(monkeypatch, s3_source=s3_source)

    await marketplace_publish.publish_to_marketplace(
        _publish_body(),
        _publish_request(),
        user=SimpleNamespace(user_id="seller-uuid", key_id="ai_market_bearer"),
    )

    payload = capture["kwargs"]["json"]
    assert payload["s3_connection"]["bucket"] == "seller-bucket"
    assert payload["s3_connection"]["serial_id"] == "VZ-ABC1234XY"


@pytest.mark.asyncio
async def test_resolver_error_fails_closed_without_publish(monkeypatch):
    store = _Store()
    capture: dict = {}
    private_key = Ed25519PrivateKey.generate()

    monkeypatch.setattr(marketplace_publish, "get_serial_store", lambda: store)
    monkeypatch.setattr(marketplace_publish, "_get_crypto", lambda: SimpleNamespace(
        get_or_create_keypairs=lambda: (private_key, None, None, None),
        has_platform_keys=lambda: True,
    ))
    monkeypatch.setattr(
        marketplace_publish,
        "ensure_vz_install_registered",
        AsyncMock(return_value="11111111-1111-4111-8111-111111111111"),
    )

    def _fail(*_args, **_kwargs):
        raise S3PublishSourceResolutionError("missing_serial_id")

    monkeypatch.setattr(marketplace_publish, "resolve_s3_publish_source", _fail)
    monkeypatch.setattr(
        marketplace_publish.httpx,
        "AsyncClient",
        lambda **kwargs: _MockAsyncClient(_MockResponse(201, {"listing_id": "listing-1"}), capture, **kwargs),
    )

    with pytest.raises(HTTPException) as exc_info:
        await marketplace_publish.publish_to_marketplace(
            _publish_body(),
            _publish_request(),
            user=SimpleNamespace(user_id="seller-uuid", key_id="ai_market_bearer"),
        )

    assert exc_info.value.status_code == 409
    assert "missing_serial_id" in exc_info.value.detail
    assert "s3_connection" not in str(exc_info.value.detail)
    assert capture == {}


def test_s3_connection_emit_allowlist_rejects_extra_field():
    with pytest.raises(ValidationError):
        marketplace_publish.S3ConnectionPublishEmit(
            bucket="seller-bucket",
            region="us-east-1",
            role_arn="arn:aws:iam::123456789012:role/aim-data-delivery",
            prefix="exports/dataset-1",
            serial_id="11111111-2222-3333-4444-555555555555",
            external_id="not-allowed",
        )


@pytest.mark.asyncio
async def test_publish_allows_sensitive_column_names(monkeypatch):
    _store, capture = _patch_publish_dependencies(monkeypatch, s3_source=NotS3PublishSource())

    await marketplace_publish.publish_to_marketplace(
        _publish_body(
            column_names=["external_id", "token", "secret"],
            column_types=["string", "string", "string"],
        ),
        _publish_request(),
        user=SimpleNamespace(user_id="seller-uuid", key_id="ai_market_bearer"),
    )

    assert capture["kwargs"]["json"]["column_names"] == ["external_id", "token", "secret"]


@pytest.mark.parametrize(
    "description",
    [
        "Contains source credential VZ-ABC1234XY.",
        "Contains source hash 0123456789abcdef0123456789abcdef.",
    ],
)
def test_sensitive_assertion_rejects_sensitive_description_values(description):
    with pytest.raises(HTTPException) as exc_info:
        marketplace_publish._assert_no_sensitive_publish_values(
            {
                "title": "A file",
                "description": description,
                "tags": ["finance"],
                "category": "financial",
                "vz_raw_listing_id": "raw-file-1",
            }
        )

    assert exc_info.value.status_code == 409
    assert exc_info.value.detail == "Publish payload contains sensitive material"


@pytest.mark.asyncio
async def test_publish_rejects_vz_raw_listing_id_with_vz_serial(monkeypatch):
    _store, capture = _patch_publish_dependencies(monkeypatch, s3_source=NotS3PublishSource())

    with pytest.raises(HTTPException) as exc_info:
        await marketplace_publish.publish_to_marketplace(
            _publish_body(vz_dataset_id="dataset-VZ-ABC1234XY"),
            _publish_request(),
            user=SimpleNamespace(user_id="seller-uuid", key_id="ai_market_bearer"),
        )

    assert exc_info.value.status_code == 409
    assert exc_info.value.detail == "Publish payload contains sensitive material"
    assert capture == {}


def test_sensitive_assertion_scans_only_settled_top_level_fields():
    marketplace_publish._assert_no_sensitive_publish_values(
        {
            "title": "Trade Secrets Dataset",
            "description": "Mentions token and external id in buyer text.",
            "tags": ["token", "finance"],
            "category": "financial",
            "vz_raw_listing_id": "raw-file-1",
            "column_names": ["VZ-ABC1234XY", "tokenizer_secret_checksum"],
            "metadata": [{"auth_token": "0123456789abcdef0123456789abcdef"}],
            "s3_connection": {"serial_id": "VZ-ABC1234XY", "prefix": "external_id=ok"},
        }
    )


def test_jcs_hash_parity_golden_with_s3_connection():
    payload = {
        "title": "A file",
        "description": "Buyer-facing description",
        "tags": ["finance"],
        "category": "financial",
        "pricing_type": "one_time",
        "price_cents": 2500,
        "vz_raw_listing_id": "raw-file-1",
        "download_channel": "docker",
        "s3_connection": {
            "bucket": "seller-bucket",
            "region": "us-east-1",
            "role_arn": "arn:aws:iam::123456789012:role/aim-data-delivery",
            "prefix": "exports/dataset-1",
            "serial_id": "11111111-2222-3333-4444-555555555555",
        },
    }
    # Mirrors backend VZPublishPayload.model_dump(exclude_none=True, exclude_unset=True, mode="json").
    expected_canonical = (
        b'{"category":"financial","description":"Buyer-facing description",'
        b'"download_channel":"docker","price_cents":2500,"pricing_type":"one_time",'
        b'"s3_connection":{"bucket":"seller-bucket","prefix":"exports/dataset-1",'
        b'"region":"us-east-1","role_arn":"arn:aws:iam::123456789012:role/aim-data-delivery",'
        b'"serial_id":"11111111-2222-3333-4444-555555555555"},"tags":["finance"],'
        b'"title":"A file","vz_raw_listing_id":"raw-file-1"}'
    )
    expected_hash = "aa8064fb47b4c8ea7e0bffb425d36922248e3a254697a0f8cc1029d9cae9fc31"

    assert marketplace_publish._jcs_canonical_bytes(payload) == expected_canonical
    assert marketplace_publish._jcs_hash(payload) == expected_hash


def test_jcs_hash_parity_golden_without_s3_connection():
    payload = {
        "title": "A file",
        "description": "Buyer-facing description",
        "tags": ["finance"],
        "category": "financial",
        "pricing_type": "one_time",
        "price_cents": 2500,
        "vz_raw_listing_id": "raw-file-1",
        "download_channel": "docker",
    }
    # Mirrors backend VZPublishPayload.model_dump(exclude_none=True, exclude_unset=True, mode="json").
    expected_canonical = (
        b'{"category":"financial","description":"Buyer-facing description",'
        b'"download_channel":"docker","price_cents":2500,"pricing_type":"one_time",'
        b'"tags":["finance"],"title":"A file","vz_raw_listing_id":"raw-file-1"}'
    )
    expected_hash = "902d261a556282241b708ad61d15ab82902f7042a78c7f9bf9fe82e04ed1b5f9"

    assert marketplace_publish._jcs_canonical_bytes(payload) == expected_canonical
    assert marketplace_publish._jcs_hash(payload) == expected_hash


@pytest.mark.asyncio
async def test_disclosure_snapshot_proxy_forwards_seller_bearer_not_vz_jwt(monkeypatch):
    record = DatasetRecord("ds-1", "customers.csv", "csv")
    record.status = ProcessingStatus.PREVIEW_READY
    processing = _Processing(record)
    capture: dict = {}

    monkeypatch.setattr(marketplace_publish.settings, "ai_market_url", "https://ai.market.test")
    monkeypatch.setattr(
        marketplace_publish.httpx,
        "AsyncClient",
        lambda **kwargs: _MockAsyncClient(
            _MockResponse(201, {"disclosure_version": "dsv_20260708000000_abcd1234"}),
            capture,
            **kwargs,
        ),
    )

    request = Request({
        "type": "http",
        "method": "POST",
        "path": "/api/marketplace/listings/listing-1/disclosure-snapshots",
        "headers": [(b"authorization", b"Bearer seller-token")],
    })

    response = await marketplace_publish.create_disclosure_snapshot(
        "listing-1",
        _disclosure_body(),
        request,
        user=SimpleNamespace(user_id="seller-uuid", key_id="ai_market_bearer"),
        processing=processing,
    )

    assert response.status == "complete"
    assert response.disclosure_version == "dsv_20260708000000_abcd1234"
    assert capture["url"] == "https://ai.market.test/api/v1/listings/listing-1/disclosure-snapshots"
    assert capture["kwargs"]["headers"]["Authorization"] == "Bearer seller-token"
    assert "publish_listing" not in capture["kwargs"]["headers"]["Authorization"]
    assert capture["kwargs"]["json"]["approved_fields"]["schema"] == {
        "columns": [{"name": "segment", "type": "string"}]
    }


@pytest.mark.asyncio
async def test_disclosure_snapshot_proxy_rejects_missing_listing_id():
    record = DatasetRecord("ds-1", "customers.csv", "csv")
    processing = _Processing(record)
    request = Request({
        "type": "http",
        "method": "POST",
        "path": "/api/marketplace/listings//disclosure-snapshots",
        "headers": [(b"authorization", b"Bearer seller-token")],
    })

    with pytest.raises(HTTPException) as exc:
        await marketplace_publish.create_disclosure_snapshot(
            "",
            _disclosure_body(),
            request,
            user=SimpleNamespace(user_id="seller-uuid", key_id="ai_market_bearer"),
            processing=processing,
        )

    assert exc.value.status_code == 422
    assert exc.value.detail == "listing_id is required"


@pytest.mark.asyncio
async def test_disclosure_snapshot_proxy_persists_local_audit(monkeypatch):
    record = DatasetRecord("ds-1", "customers.csv", "csv")
    record.status = ProcessingStatus.PREVIEW_READY
    processing = _Processing(record)

    monkeypatch.setattr(marketplace_publish.settings, "ai_market_url", "https://ai.market.test")
    monkeypatch.setattr(
        marketplace_publish.httpx,
        "AsyncClient",
        lambda **kwargs: _MockAsyncClient(
            _MockResponse(201, {"disclosure_version": "dsv_20260708000000_abcd1234"}),
            {},
            **kwargs,
        ),
    )

    request = Request({
        "type": "http",
        "method": "POST",
        "path": "/api/marketplace/listings/listing-1/disclosure-snapshots",
        "headers": [(b"authorization", b"Bearer seller-token")],
    })

    await marketplace_publish.create_disclosure_snapshot(
        "listing-1",
        _disclosure_body(),
        request,
        user=SimpleNamespace(user_id="seller-uuid", key_id="ai_market_bearer"),
        processing=processing,
    )

    audit = processing.record.metadata["disclosure_decision"]
    assert processing.saved == 2
    assert processing.record.listing_id == "listing-1"
    assert audit["status"] == "complete"
    assert audit["listing_id"] == "listing-1"
    assert audit["source_publish_operation_id"] == "op-1"
    assert audit["disclosure_version"] == "dsv_20260708000000_abcd1234"
    assert audit["sample_decision"] == "none"
    assert audit["approved_sample_row_count"] == 0
    assert audit["approved_sample_columns"] == []
    assert audit["approved_fields_hash"]
    assert audit["approved_sample_hash"] is None
