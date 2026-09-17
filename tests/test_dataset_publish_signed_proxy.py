from types import SimpleNamespace
from uuid import uuid4

import pytest
import jwt
from cryptography.hazmat.primitives.asymmetric.ed25519 import Ed25519PrivateKey
from fastapi import HTTPException
from starlette.requests import Request

from app.core.database import get_session_context
from app.models.attestation_schemas import ColumnMetrics, QualityAttestation
from app.models.compliance_schemas import ComplianceReport, RegulationFlag
from app.models.dataset import DatasetRecord as DBDatasetRecord
from app.models.listing_metadata_schemas import ColumnSummary, ListingMetadata
from app.routers import datasets
from app.services import marketplace_push_service
from app.services.marketplace_action_signer import build_action_jwt, canonical_payload_hash
from app.services.processing_service import DatasetRecord, ProcessingStatus


def _request() -> Request:
    return Request({
        "type": "http",
        "method": "POST",
        "path": "/api/datasets/ds-1/publish",
        "headers": [(b"authorization", b"Bearer seller-token")],
    })


def test_generic_marketplace_action_signer_binds_action_and_canonical_payload_hash():
    key = Ed25519PrivateKey.from_private_bytes(bytes([7]) * 32)
    payload_hash = canonical_payload_hash({"b": 2, "a": 1})
    token = build_action_jwt(
        seller_id="seller-1",
        install_id="install-1",
        action="submit_data_verification_receipt",
        payload_hash=payload_hash,
        private_key=key,
    )
    claims = jwt.decode(token, key.public_key(), algorithms=["EdDSA"])
    assert claims["action"] == "submit_data_verification_receipt"
    assert claims["payload_hash"] == payload_hash
    assert "metadata_hash" not in claims


def _processing_record(dataset_id: str, *, status=ProcessingStatus.PREVIEW_READY) -> DatasetRecord:
    record = DatasetRecord(dataset_id, "customers.csv", "csv")
    record.status = status
    record.file_size_bytes = 4096
    record.metadata = {"row_count": 1200, "column_count": 2}
    return record


class _Processing:
    def __init__(self, record):
        self._record = record

    def get_dataset(self, dataset_id: str):
        assert dataset_id == self._record.id
        return self._record


def _listing_metadata() -> ListingMetadata:
    return ListingMetadata(
        title="Customer Spend",
        description="Buyer-facing customer spend profile.",
        tags=["customers", "spend", "retail"],
        column_summary=[
            ColumnSummary(name="customer_segment", type="string", null_percentage=0.0, uniqueness_ratio=0.92),
            ColumnSummary(name="monthly_spend", type="float", null_percentage=1.2, uniqueness_ratio=0.87),
        ],
        row_count=1200,
        column_count=2,
        file_format="csv",
        size_bytes=4096,
        privacy_score=8.7,
        data_categories=["retail", "analytics", "benchmarking"],
        generated_at="2026-06-30T12:00:00Z",
    )


def _compliance_report(dataset_id: str) -> ComplianceReport:
    return ComplianceReport(
        dataset_id=dataset_id,
        compliance_score=91,
        pii_entities_found=["EMAIL_ADDRESS"],
        flags=[
            RegulationFlag(
                regulation_name="GDPR",
                applicable=True,
                risk_level="low",
                flagged_columns=["email"],
                recommended_actions=["Aggregate before resale"],
            )
        ],
        generated_at="2026-06-30T12:00:00Z",
    )


def _attestation() -> QualityAttestation:
    return QualityAttestation(
        data_hash="a" * 64,
        attestation_hash="b" * 64,
        row_count=1200,
        column_count=2,
        completeness_score=0.99,
        type_consistency_score=0.98,
        freshness_score=0.97,
        null_ratio_per_column=[ColumnMetrics(column_name="customer_segment", null_ratio=0.0)],
        quality_grade="A",
        generated_at="2026-06-30T12:00:00Z",
    )


def _insert_db_record(dataset_id: str) -> None:
    with get_session_context() as session:
        session.add(
            DBDatasetRecord(
                id=dataset_id,
                original_filename="customers.csv",
                storage_filename=f"{dataset_id}_customers.csv",
                file_type="csv",
                file_size_bytes=4096,
                status=ProcessingStatus.PREVIEW_READY.value,
                metadata_json="{}",
            )
        )
        session.commit()


@pytest.mark.asyncio
async def test_dataset_publish_builds_signed_proxy_request_and_persists_listing_id(monkeypatch):
    dataset_id = f"ds-{uuid4()}"
    record = _processing_record(dataset_id)
    captured = {}
    _insert_db_record(dataset_id)

    monkeypatch.setattr(datasets, "load_listing_metadata", lambda base_path: _listing_metadata())
    monkeypatch.setattr(datasets, "load_compliance_report", lambda base_path: _compliance_report(dataset_id))
    monkeypatch.setattr(datasets, "load_attestation", lambda base_path: _attestation())

    async def _publish(body, request, user):
        captured["body"] = body
        captured["request"] = request
        captured["user"] = user
        return {"listing_id": "listing-123", "marketplace_url": "https://ai.market/listing/listing-123"}

    monkeypatch.setattr(datasets, "publish_via_signed_proxy", _publish)
    monkeypatch.setattr(
        marketplace_push_service.MarketplacePushService,
        "push_to_marketplace",
        pytest.fail,
    )

    result = await datasets.publish_to_marketplace(
        dataset_id,
        _request(),
        price=12.34,
        category="tabular",
        model_provider="local-model",
        processing=_Processing(record),
        user=SimpleNamespace(user_id="seller-uuid", key_id="ai_market_bearer"),
        _meter=None,
    )

    body = captured["body"]
    assert result["listing_id"] == "listing-123"
    assert body.vz_dataset_id == dataset_id
    assert body.price_cents == 2500
    assert body.category == "retail"
    assert body.secondary_categories == ["analytics", "benchmarking"]
    assert body.model_provider == "local-model"
    assert body.privacy_score == 8.7
    assert body.compliance_status == "low_risk"
    assert body.schema_info == {
        "columns": [
            {"name": "customer_segment", "type": "string", "null_percentage": 0.0, "uniqueness_ratio": 0.92},
            {"name": "monthly_spend", "type": "float", "null_percentage": 1.2, "uniqueness_ratio": 0.87},
        ],
        "row_count": 1200,
        "column_count": 2,
        "file_format": "csv",
        "size_bytes": 4096,
        "attestation": {
            "data_hash": "a" * 64,
            "attestation_hash": "b" * 64,
            "completeness_score": 0.99,
            "type_consistency_score": 0.98,
            "freshness_score": 0.97,
            "quality_grade": "A",
            "generated_at": "2026-06-30T12:00:00Z",
        },
    }
    assert body.compliance_details["score"] == 91
    assert body.compliance_details["pii_entities"] == ["EMAIL_ADDRESS"]
    assert body.compliance_details["flags"][0]["regulation_name"] == "GDPR"
    assert "privacy_scan_status" not in body.model_dump(exclude_none=True)

    with get_session_context() as session:
        db_record = session.get(DBDatasetRecord, dataset_id)
        assert db_record.listing_id == "listing-123"
        assert db_record.updated_at is not None


@pytest.mark.asyncio
async def test_dataset_publish_rejects_non_preview_ready_before_publish(monkeypatch):
    dataset_id = f"ds-{uuid4()}"
    record = _processing_record(dataset_id, status=ProcessingStatus.UPLOADED)

    async def _publish(*_args, **_kwargs):
        raise AssertionError("publish_via_signed_proxy should not be called")

    monkeypatch.setattr(datasets, "publish_via_signed_proxy", _publish)

    with pytest.raises(HTTPException) as exc_info:
        await datasets.publish_to_marketplace(
            dataset_id,
            _request(),
            processing=_Processing(record),
            user=SimpleNamespace(user_id="seller-uuid", key_id="ai_market_bearer"),
            _meter=None,
        )

    assert exc_info.value.status_code == 400
    assert "Dataset not ready for publish" in exc_info.value.detail


@pytest.mark.asyncio
async def test_dataset_publish_honors_metadata_override(monkeypatch):
    dataset_id = f"ds-{uuid4()}"
    record = _processing_record(dataset_id)
    captured = {}

    monkeypatch.setattr(datasets, "load_listing_metadata", lambda _base_path: pytest.fail("metadata override should be used"))
    monkeypatch.setattr(datasets, "load_compliance_report", lambda _base_path: None)
    monkeypatch.setattr(datasets, "load_attestation", lambda _base_path: None)

    async def _publish(body, _request, _user):
        captured["body"] = body
        return {"listing_id": "listing-override"}

    monkeypatch.setattr(datasets, "publish_via_signed_proxy", _publish)

    await datasets.publish_to_marketplace(
        dataset_id,
        _request(),
        body=datasets.PublishDatasetRequest(
            title="Manual Title",
            description="Manual description",
            tags=[" one ", "two", ""],
        ),
        processing=_Processing(record),
        user=SimpleNamespace(user_id="seller-uuid", key_id="ai_market_bearer"),
        _meter=None,
    )

    body = captured["body"]
    assert body.title == "Manual Title"
    assert body.description == "Manual description"
    assert body.tags == ["one", "two"]
    assert body.price_cents == 2500
    assert body.schema_info["row_count"] == 1200

# S1717 local sender cases. The S3 byte golden intentionally omits all additions.
from tests.test_member_upload_client import local_dataset
from app.routers import marketplace_publish as mp
from app.models.dataset import DatasetMember
from app.config import settings
from app.services.marketplace_action_signer import canonical_json_bytes
from app.services.s3_publish_source_resolver import NotS3PublishSource


def test_s3_version_bytes_identical_with_explicit_new_defaults(monkeypatch):
    fields = dict(version_label='v1', object_count=1, total_size_bytes=42, manifest_hash='a'*64)
    expected = canonical_json_bytes(fields)
    for flag in (False, True):
        monkeypatch.setattr(settings, 'multi_file_datasets_enabled', flag)
        for version in (mp.VersionPublishEmit(**fields), mp.VersionPublishEmit(**fields, source_kind='s3', members_total=None, members_upload_id=None)):
            assert canonical_json_bytes(mp._build_version_emit(version)) == expected


def test_directory_wire_has_exact_manifest_and_agent_version(local_dataset, monkeypatch):
    monkeypatch.setattr(settings, 'app_version', '1.24.0')
    local = mp._local_publish_snapshot(local_dataset, 'v1')
    body = mp.MarketplacePublishRequest(title='Test', description='Test set', price_cents=2500, vz_dataset_id=local_dataset)
    payload = mp._build_publish_payload(body, None, [local['version']])
    assert payload['agent_version'] == '1.24.0'
    version = payload['versions'][0]
    assert version['source_kind'] == 'aim_data_local'
    assert version['members_total'] == 3
    assert version['object_count'] == 3
    assert version['total_size_bytes'] == 6
    assert version['manifest_hash'] == local['manifest']['manifest_hash']
    assert version['members_upload_id'] == str(local['version'].members_upload_id)
    assert 'root_path' not in str(payload) and 'verification' not in str(payload)
    assert 's3_connection' not in payload


@pytest.mark.asyncio
@pytest.mark.parametrize('kind', ['all_sample', 'all_documentation', 'sample_cap'])
async def test_local_refusal_before_any_signing(local_dataset, monkeypatch, kind):
    with get_session_context() as session:
        from sqlmodel import select
        for row in session.exec(select(DatasetMember).where(DatasetMember.dataset_id == local_dataset)).all():
            if kind == 'all_sample': row.is_sample = True
            elif kind == 'all_documentation': row.role = 'documentation'; row.is_sample = False
            elif row.index == 0: row.size_bytes = settings.sample_max_file_bytes + 1
            session.add(row)
        session.commit()
    monkeypatch.setattr(mp, '_get_crypto', lambda: pytest.fail('Refuse before crypto or signing'))
    body = mp.MarketplacePublishRequest(title='Test', description='Test', price_cents=2500, vz_dataset_id=local_dataset)
    with pytest.raises(HTTPException) as exc:
        await mp.publish_via_signed_proxy(body, _request(), SimpleNamespace())
    assert exc.value.status_code == 409
    assert ('SAMPLE_MAX_FILE_BYTES' in exc.value.detail and '0.csv' in exc.value.detail) if kind == 'sample_cap' else 'paid_set_required' in exc.value.detail


@pytest.mark.asyncio
async def test_missing_version_id_retries_and_never_records_publish(local_dataset, monkeypatch):
    calls = []
    monkeypatch.setattr(mp, '_get_crypto', lambda: SimpleNamespace(get_or_create_keypairs=lambda: (None, None, None, None)))
    monkeypatch.setattr(mp, 'get_serial_store', lambda: SimpleNamespace(state=SimpleNamespace(last_status_cache={}, ai_market_seller_id='seller', ai_market_access_token='token')))
    async def register(*args, **kwargs): return 'install'
    monkeypatch.setattr(mp, 'ensure_vz_install_registered', register)
    monkeypatch.setattr(mp, 'resolve_s3_publish_source', lambda *args: None)
    monkeypatch.setattr(mp, '_build_jwt', lambda *args: 'signed')
    class Client:
        def __init__(self, **kwargs): pass
        async def __aenter__(self): return self
        async def __aexit__(self, *args): pass
        async def post(self, url, **kwargs):
            calls.append(kwargs['json'])
            return SimpleNamespace(status_code=200, json=lambda: {'listing_id': 'listing', 'versions': []})
    monkeypatch.setattr(mp.httpx, 'AsyncClient', Client)
    body = mp.MarketplacePublishRequest(title='Test', description='Test', price_cents=2500, vz_dataset_id=local_dataset)
    with pytest.raises(HTTPException, match='local_publish_missing_version_id'):
        await mp.publish_via_signed_proxy(body, _request(), SimpleNamespace())
    assert len(calls) == 3 and calls[0] == calls[1] == calls[2]
    with get_session_context() as session:
        assert session.get(DBDatasetRecord, local_dataset).listing_id is None
