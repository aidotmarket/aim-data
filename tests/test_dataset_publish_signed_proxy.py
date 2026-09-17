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
        for version in (mp.VersionPublishEmit(**fields), mp.VersionPublishEmit(**fields, source_kind='s3', members_total=None, sample_members_total=None, members_upload_id=None)):
            assert canonical_json_bytes(mp._build_version_emit(version)) == expected


def test_directory_wire_has_exact_manifest_and_agent_version(local_dataset, monkeypatch):
    monkeypatch.setattr(settings, 'app_version', '1.24.0')
    local = mp._local_publish_snapshot(local_dataset, 'v1')
    body = mp.MarketplacePublishRequest(title='Test', description='Test set', price_cents=2500, vz_dataset_id=local_dataset)
    payload = mp._build_publish_payload(body, None, [local['version']])
    assert payload['agent_version'] == '1.24.0'
    version = payload['versions'][0]
    assert version['source_kind'] == 'aim_data_local'
    assert version['sample_members_total'] == 1
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

def _signed_local_client(monkeypatch, handler):
    """Use real httpx request serialization and Ed25519 signatures, no network."""
    import httpx
    key = Ed25519PrivateKey.from_private_bytes(bytes([9]) * 32)
    seller_id, install_id = str(uuid4()), str(uuid4())
    monkeypatch.setattr(settings, 'publish_member_chunk', 2)
    monkeypatch.setattr(settings, 'app_version', '1.24.0')
    monkeypatch.setattr(mp, '_get_crypto', lambda: SimpleNamespace(get_or_create_keypairs=lambda: (key, None, None, None)))
    monkeypatch.setattr(mp, 'get_serial_store', lambda: SimpleNamespace(state=SimpleNamespace(last_status_cache={}, ai_market_seller_id=seller_id, ai_market_access_token='token')))
    async def register(*args, **kwargs): return install_id
    monkeypatch.setattr(mp, 'ensure_vz_install_registered', register)
    monkeypatch.setattr(mp, 'resolve_s3_publish_source', lambda *args: None)
    original = httpx.AsyncClient
    nonces = set()

    async def receive(request):
        claims = jwt.decode(request.headers['Authorization'].removeprefix('Bearer '), key.public_key(), algorithms=['EdDSA'])
        assert claims['sub'] == seller_id and claims['iss'] == install_id
        assert claims['exp'] - claims['iat'] == 300
        assert claims['jti'] not in nonces
        nonces.add(claims['jti'])
        assert 'metadata_hash' in claims and 'payload_hash' not in claims
        return await handler(request, claims)

    monkeypatch.setattr(mp.httpx, 'AsyncClient', lambda **kwargs: original(transport=httpx.MockTransport(receive), **kwargs))


def _receiver_hash(payload):
    # Mirrored verbatim serialization options from ai-market-backend 7002ab34:
    # app/services/vz_publish_service.py:68-86 (_jcs_serialize / compute_metadata_hash).
    # Keep independent of the sender helper: this is the receiver hash oracle.
    import hashlib
    import json
    encoded = json.dumps(payload, sort_keys=True, separators=(",", ":"),
                         ensure_ascii=False, allow_nan=False).encode("utf-8")
    assert canonical_json_bytes(payload) == encoded
    return hashlib.sha256(encoded).hexdigest()


@pytest.mark.asyncio
async def test_local_publish_round_trip_signs_chunks_and_retains_before_upload(local_dataset, monkeypatch):
    import hashlib
    import httpx
    import json
    from app.models.published_manifest import PublishedManifest
    calls = []
    version_id = str(uuid4())
    # Non-ASCII text exercises ensure_ascii=False in signed member JSON.
    with get_session_context() as session:
        member = session.get(DatasetMember, (local_dataset, 2))
        member.relative_path = 'données/界.csv'
        session.add(member); session.commit()
    local = mp._local_publish_snapshot(local_dataset)
    upload_id = str(local['version'].members_upload_id)

    async def receive(request, claims):
        path = request.url.path
        calls.append(path)
        if '/samples/' in path:
            assert path == f'/api/v1/vz/versions/{version_id}/samples/0'
            assert dict(request.url.params) == {'members_upload_id': upload_id}
            assert request.headers['Content-Type'] == 'application/octet-stream'
            assert request.headers['Content-Length'] == '2'
            assert request.content == b'0\n'
            signed_payload = dict(version_id=version_id, members_upload_id=upload_id,
                index=0, size_bytes=2, sha256=hashlib.sha256(b'0\n').hexdigest())
            assert claims['action'] == 'publish_sample_member'
            assert claims['metadata_hash'] == _receiver_hash(signed_payload)
            return httpx.Response(200, json={'version_id': version_id, 'status': 'active', 'index': 0})
        payload = json.loads(request.content)
        assert request.headers['Content-Type'] == 'application/json'
        assert claims['metadata_hash'] == _receiver_hash(payload)
        if path.endswith('/publish'):
            assert claims['action'] == 'publish_listing'
            assert payload['agent_version'] == '1.24.0'
            assert payload['versions'][0] == mp._build_version_emit(local['version'])
            return httpx.Response(200, json={'listing_id': 'listing', 'versions': [dict(version_id=version_id,
                version_label=local['version'].version_label, status='pending_members', quarantine_reason=None)]})
        assert claims['action'] == 'publish_version_members'
        offset = 0 if len(calls) == 2 else 2
        assert payload == dict(version_id=version_id, members_upload_id=upload_id,
                               members=local['manifest']['members'][offset:offset + 2])
        with get_session_context() as session:
            record = session.get(DBDatasetRecord, local_dataset)
            state = json.loads(record.metadata_json)['local_publish']
            snapshot = session.get(PublishedManifest, (version_id, state['manifest_hash']))
            assert snapshot is not None and record.listing_id == 'listing'
        return httpx.Response(200, json=dict(version_id=version_id, version_label=local['version'].version_label,
                                           status='pending_members', quarantine_reason=None))

    _signed_local_client(monkeypatch, receive)
    body = mp.MarketplacePublishRequest(title='Test', description='Test', price_cents=2500, vz_dataset_id=local_dataset)
    result = await mp.publish_via_signed_proxy(body, _request(), SimpleNamespace())
    assert result['status'] == 'published'
    assert result['version']['version_id'] == version_id
    assert result['versions'][0]['status'] == 'active'
    assert calls == ['/api/v1/vz/publish', f'/api/v1/vz/versions/{version_id}/members',
                     f'/api/v1/vz/versions/{version_id}/members', f'/api/v1/vz/versions/{version_id}/samples/0']


def test_full_s3_payload_golden_is_independent_of_flag_and_app_version(monkeypatch):
    import hashlib
    monkeypatch.setattr(mp, "CHANNEL", SimpleNamespace(value="direct"))
    body = mp.MarketplacePublishRequest(title='Test', description='Test', price_cents=2500, vz_dataset_id='dataset')
    fields = dict(version_label='v1', object_count=1, total_size_bytes=42, manifest_hash='a'*64)
    source_fields = dict(bucket='example-bucket', region='us-east-1', role_arn='arn:aws:iam::123456789012:role/example', prefix='datasets/', serial_id='example-serial')
    from app.services.s3_publish_source_resolver import S3PublishSourceResolution
    source = S3PublishSourceResolution(**source_fields)
    golden = canonical_json_bytes(dict(title='Test', description='Test', tags=[], pricing_type='one_time',
        price_cents=2500, vz_raw_listing_id='dataset', download_channel='direct', versions=[fields], s3_connection=source_fields))
    # Captured from _build_publish_payload at base 97da3f09, direct channel.
    assert hashlib.sha256(golden).hexdigest() == '7a9ca30f3ff128453ca0d2cabc1deeda74be071de04ebb21acf86037e9ff087e'
    for flag in (False, True):
        monkeypatch.setattr(settings, 'multi_file_datasets_enabled', flag)
        monkeypatch.setattr(settings, 'app_version', '1.24.0' if flag else 'dev')
        assert canonical_json_bytes(mp._build_publish_payload(body, source, [mp.VersionPublishEmit(**fields)])) == golden


def test_migrated_legacy_dataset_republish_uses_retained_original_binding(local_dataset):
    with get_session_context() as session:
        row = session.get(DBDatasetRecord, local_dataset)
        row.file_type = "csv"; row.processed_path = "/unchanged/legacy.parquet"
        row.batch_id = "shared"; row.listing_id = "legacy-listing"
        session.add(row); session.commit()
    local = mp._local_publish_snapshot(local_dataset)
    assert local['version'].source_kind == 'aim_data_local'
    mp._record_local_publish(local, 'legacy-listing', str(uuid4()), 'pending_members')
    with get_session_context() as session:
        row = session.get(DBDatasetRecord, local_dataset)
        assert row.file_type == 'csv' and row.processed_path == '/unchanged/legacy.parquet'
        assert row.batch_id == 'shared' and row.listing_id == 'legacy-listing'


@pytest.mark.asyncio
@pytest.mark.parametrize('terminal', ['active', 'quarantined', 'superseded'])
@pytest.mark.parametrize('terminal_at', ['publish', 'members', 'sample'])
async def test_receiver_terminal_status_stops_uploads(local_dataset, monkeypatch, terminal, terminal_at):
    import httpx
    import json
    version_id = str(uuid4())
    paths = []
    # Two selected samples prove terminal sample responses stop the loop too.
    with get_session_context() as session:
        row = session.get(DatasetMember, (local_dataset, 1)); row.is_sample = True
        session.add(row); session.commit()
    local = mp._local_publish_snapshot(local_dataset)

    async def receive(request, claims):
        path = request.url.path
        paths.append(path)
        status = terminal if path.endswith('/' + terminal_at) else 'pending_members'
        if '/samples/' in path:
            status = terminal
            return httpx.Response(200, json=dict(version_id=version_id, status=status, index=0))
        result = dict(version_id=version_id, version_label=local['version'].version_label,
                      status=status, quarantine_reason='test refusal' if terminal == 'quarantined' else None)
        if path.endswith('/publish'):
            return httpx.Response(200, json={'listing_id': 'listing', 'versions': [result]})
        return httpx.Response(200, json=result)

    _signed_local_client(monkeypatch, receive)
    body = mp.MarketplacePublishRequest(title='Test', description='Test', price_cents=2500, vz_dataset_id=local_dataset)
    result = await mp.publish_via_signed_proxy(body, _request(), SimpleNamespace())
    assert result['status'] == ('published' if terminal == 'active' else terminal)
    assert result['version']['status'] == terminal
    assert len(paths) == {'publish': 1, 'members': 2, 'sample': 4}[terminal_at]
    with get_session_context() as session:
        state = json.loads(session.get(DBDatasetRecord, local_dataset).metadata_json)['local_publish']
        assert state['status'] == terminal


@pytest.mark.asyncio
async def test_no_samples_activates_on_final_member_chunk(local_dataset, monkeypatch):
    import httpx
    import json
    with get_session_context() as session:
        member = session.get(DatasetMember, (local_dataset, 0)); member.is_sample = False
        session.add(member); session.commit()
    local = mp._local_publish_snapshot(local_dataset)
    version_id = str(uuid4())
    received = []

    async def receive(request, claims):
        assert '/samples/' not in request.url.path
        payload = json.loads(request.content)
        result = dict(version_id=version_id, version_label=local['version'].version_label,
                      status='pending_members', quarantine_reason=None)
        if request.url.path.endswith('/publish'):
            return httpx.Response(200, json={'listing_id': 'listing', 'versions': [result]})
        received.extend(payload['members'])
        if len(received) == 3:
            result['status'] = 'active'
        return httpx.Response(200, json=result)

    _signed_local_client(monkeypatch, receive)
    body = mp.MarketplacePublishRequest(title='Test', description='Test', price_cents=2500, vz_dataset_id=local_dataset)
    assert (await mp.publish_via_signed_proxy(body, _request(), SimpleNamespace()))['status'] == 'published'
    assert received == local['manifest']['members']


@pytest.mark.asyncio
@pytest.mark.parametrize('code,detail', [
    (400, '0.csv: SAMPLE_MAX_FILE_BYTES: 1'),
    (400, 'SAMPLE_MAX_FILES: 1; SAMPLE_MAX_TOTAL_BYTES: 1'),
    (409, 'SAMPLE_SELLER_QUOTA_BYTES: 1'),
    (429, 'SAMPLE_UPLOAD_RATE: 1'),
    (409, 'version is no longer pending_members'),
    (413, 'sample size mismatch'),
    (400, 'sample size mismatch'),
    (409, 'sample hash mismatch'),
    (503, 'sample_store_unavailable'),
])
async def test_sample_refusals_preserve_named_error_and_retry_pending(local_dataset, monkeypatch, code, detail):
    import httpx
    import json
    local = mp._local_publish_snapshot(local_dataset)
    version_id = str(uuid4())
    member_bodies = []
    sample_bodies = []
    failing = True

    async def receive(request, claims):
        result = dict(version_id=version_id, version_label=local['version'].version_label,
                      status='pending_members', quarantine_reason=None)
        if request.url.path.endswith('/publish'):
            return httpx.Response(200, json={'listing_id': 'listing', 'versions': [result]})
        if request.url.path.endswith('/members'):
            member_bodies.append(json.loads(request.content))
            return httpx.Response(200, json=result)
        sample_bodies.append(request.content)
        assert claims['action'] == 'publish_sample_member'
        if failing:
            return httpx.Response(code, json={'detail': detail})
        return httpx.Response(200, json=dict(version_id=version_id, status='active', index=0))

    _signed_local_client(monkeypatch, receive)
    body = mp.MarketplacePublishRequest(title='Test', description='Test', price_cents=2500, vz_dataset_id=local_dataset)
    with pytest.raises(HTTPException) as exc:
        await mp.publish_via_signed_proxy(body, _request(), SimpleNamespace())
    assert exc.value.status_code == code and exc.value.detail == detail
    state = await mp.publish_status(dataset_id=local_dataset, user=None)
    assert state['status'] == 'pending_members' and state['offset'] == 3
    failing = False
    result = await mp.publish_via_signed_proxy(body, _request(), SimpleNamespace())
    assert result['status'] == 'published'
    assert len(member_bodies) == 2  # acknowledged chunks were not resent
    assert sample_bodies == [b'0\n', b'0\n']


@pytest.mark.asyncio
async def test_lost_final_sample_ack_retry_uses_publish_status_without_sample_replay(local_dataset, monkeypatch):
    import httpx
    local = mp._local_publish_snapshot(local_dataset)
    version_id = str(uuid4())
    activated = False
    sample_calls = 0

    async def receive(request, claims):
        nonlocal activated, sample_calls
        result = dict(version_id=version_id, version_label=local['version'].version_label,
                      status='active' if activated else 'pending_members', quarantine_reason=None)
        if request.url.path.endswith('/publish'):
            return httpx.Response(200, json={'listing_id': 'listing', 'versions': [result]})
        if request.url.path.endswith('/members'):
            return httpx.Response(200, json=result)
        sample_calls += 1
        activated = True
        raise httpx.ReadTimeout('lost final ACK', request=request)

    _signed_local_client(monkeypatch, receive)
    body = mp.MarketplacePublishRequest(title='Test', description='Test', price_cents=2500, vz_dataset_id=local_dataset)
    with pytest.raises(HTTPException, match='upload interrupted'):
        await mp.publish_via_signed_proxy(body, _request(), SimpleNamespace())
    assert (await mp.publish_status(dataset_id=local_dataset, user=None))['status'] == 'pending_members'
    assert (await mp.publish_via_signed_proxy(body, _request(), SimpleNamespace()))['status'] == 'published'
    assert sample_calls == 1


@pytest.mark.asyncio
async def test_lost_member_ack_replays_exact_frozen_body_with_fresh_action_jwt(local_dataset, monkeypatch):
    import httpx
    import json
    local = mp._local_publish_snapshot(local_dataset)
    version_id = str(uuid4())
    chunks = []

    async def receive(request, claims):
        result = dict(version_id=version_id, version_label=local['version'].version_label,
                      status='pending_members', quarantine_reason=None)
        if request.url.path.endswith('/publish'):
            return httpx.Response(200, json={'listing_id': 'listing', 'versions': [result]})
        if request.url.path.endswith('/members'):
            payload = json.loads(request.content)
            assert claims['action'] == 'publish_version_members'
            assert claims['metadata_hash'] == _receiver_hash(payload)
            chunks.append(request.content)
            if len(chunks) == 1:
                raise httpx.ReadTimeout('lost ACK', request=request)
            return httpx.Response(200, json=result)
        return httpx.Response(200, json=dict(version_id=version_id, status='active', index=0))

    _signed_local_client(monkeypatch, receive)
    body = mp.MarketplacePublishRequest(title='Test', description='Test', price_cents=2500, vz_dataset_id=local_dataset)
    with pytest.raises(HTTPException, match='upload interrupted'):
        await mp.publish_via_signed_proxy(body, _request(), SimpleNamespace())
    with get_session_context() as session:
        row = session.get(DatasetMember, (local_dataset, 1)); row.relative_path = 'changed.csv'
        session.add(row); session.commit()
    assert (await mp.publish_via_signed_proxy(body, _request(), SimpleNamespace()))['status'] == 'published'
    assert len(chunks) == 3 and chunks[0] == chunks[1]


@pytest.mark.asyncio
@pytest.mark.parametrize("profile_kind", ["two", "absent", "malformed"])
async def test_section_3a_profiles_and_paid_set_refusal(local_dataset, monkeypatch, profile_kind):
    import json
    import httpx
    columns = [[{"name": "id", "type": "INTEGER", "sample_values": [1]}],
               [{"name": "title", "type": "VARCHAR"}]]
    metadata = {}
    if profile_kind == "two":
        metadata = {"directory_profile": {"members": {
            str(i): {"status": "profiled", "profile": {"columns": cols}}
            for i, cols in enumerate(columns)
        }}}
        # Documentation profiles and unknown indices must not enter the carrier.
        metadata["directory_profile"]["members"].update({
            "2": {"status": "profiled", "profile": {"columns": columns[0]}},
            "99": {"status": "profiled", "profile": {"columns": columns[0]}},
        })
    elif profile_kind == "malformed":
        metadata = {"directory_profile": {"members": {
            "0": {"status": "profiled", "profile": {"columns": [{"name": 1, "type": "INT"}]}},
            "1": {"status": "parse_failed", "profile": {"columns": columns[1]}},
            "2": {"status": "profiled", "profile": None},
        }}}
    with get_session_context() as session:
        record = session.get(DBDatasetRecord, local_dataset)
        record.metadata_json = json.dumps(metadata)
        session.add(record)
        member = session.get(DatasetMember, (local_dataset, 2))
        member.role = "documentation"
        session.add(member)
        session.commit()

    async def receive(request, claims):
        assert request.url.path.endswith("/publish")
        payload = json.loads(request.content)
        assert claims["metadata_hash"] == _receiver_hash(payload)
        assert payload["versions"][0]["sample_members_total"] == 1
        if profile_kind == "two":
            assert payload["schema_info"]["member_profiles"] == [
                {"index": i, "columns": [{"name": c["name"], "type": c["type"]} for c in cols]}
                for i, cols in enumerate(columns)
            ]
        else:
            assert "member_profiles" not in payload["schema_info"]
        return httpx.Response(400, json={"detail": "at least one non-sample data member is required"})

    _signed_local_client(monkeypatch, receive)
    body = mp.MarketplacePublishRequest(title="Test", description="Test", price_cents=2500,
                                       vz_dataset_id=local_dataset)
    with pytest.raises(HTTPException) as exc:
        await mp.publish_via_signed_proxy(body, _request(), SimpleNamespace())
    assert exc.value.status_code == 400
    assert exc.value.detail == "at least one non-sample data member is required"


def test_processable_types_wire_mirror():
    import json
    from pathlib import Path
    from app.services.processing_service import PROCESSABLE_TYPES
    raw = (Path(__file__).parent / "fixtures/multi_file_datasets/processable_types.json").read_bytes()
    expected = sorted(PROCESSABLE_TYPES | {"unsupported"})
    assert json.loads(raw) == expected
    assert raw == (json.dumps(expected, separators=(",", ":")) + "\n").encode()


def test_sample_count_refused_on_s3_and_paid_set_checked_on_local(monkeypatch):
    monkeypatch.setattr(settings, "multi_file_datasets_enabled", True)
    fields = dict(version_label="v1", object_count=1, total_size_bytes=2, manifest_hash="a" * 64)
    with pytest.raises(HTTPException, match="members fields require aim_data_local"):
        mp._build_version_emit(mp.VersionPublishEmit(**fields, sample_members_total=0))
    with pytest.raises(HTTPException, match="paid_set_required"):
        mp._build_version_emit(mp.VersionPublishEmit(**fields, source_kind="aim_data_local",
            members_total=1, sample_members_total=1, members_upload_id=uuid4()))


@pytest.mark.asyncio
async def test_removed_middle_member_publishes_dense_indices_and_source_sample(local_dataset, monkeypatch):
    import json
    import httpx
    from app.models.published_manifest import PublishedManifest
    monkeypatch.setattr(settings, "sample_upload_timeout_s", 1200)
    with get_session_context() as session:
        for index in range(3):
            member = session.get(DatasetMember, (local_dataset, index))
            member.is_sample = index == 2
            if index == 1:
                member.status = "removed"
            session.add(member)
        record = session.get(DBDatasetRecord, local_dataset)
        record.metadata_json = json.dumps({"directory_profile": {"members": {
            "2": {"status": "profiled", "profile": {"columns": [{"name": "sample", "type": "INT"}]}}
        }}})
        session.add(record)
        session.commit()
    local = mp._local_publish_snapshot(local_dataset)
    assert local["registration_to_published_index"] == {"0": 0, "2": 1}
    assert local["member_profiles"] == [{"index": 1, "columns": [{"name": "sample", "type": "INT"}]}]
    version_id = str(uuid4())
    samples = []
    async def receive(request, claims):
        version = {"version_id": version_id, "version_label": local["version"].version_label,
                   "status": "pending_members"}
        if request.url.path.endswith("/publish"):
            payload = json.loads(request.content)
            assert payload["versions"][0]["members_total"] == 2
            assert payload["versions"][0]["sample_members_total"] == 1
            return httpx.Response(200, json={"listing_id": "listing", "versions": [version]})
        if request.url.path.endswith("/members"):
            members = json.loads(request.content)["members"]
            assert [m["index"] for m in members] == [0, 1]
            assert [m["relative_path"] for m in members] == ["0.csv", "2.csv"]
            with get_session_context() as session:
                retained = session.get(PublishedManifest, (version_id, local["manifest"]["manifest_hash"]))
                assert retained.members == members
                assert retained.registration_to_published_index == {"0": 0, "2": 1}
            resumed = mp._local_publish_snapshot(local_dataset)
            assert resumed["registration_to_published_index"] == local["registration_to_published_index"]
            assert resumed["manifest"] == local["manifest"]
            return httpx.Response(200, json=version)
        assert request.url.path.endswith("/samples/1")
        assert all(value >= settings.sample_upload_timeout_s for value in request.extensions["timeout"].values())
        samples.append(request.content)
        signed = dict(version_id=version_id, members_upload_id=str(local["version"].members_upload_id),
                      index=1, size_bytes=2, sha256=local["manifest"]["members"][1]["sha256"])
        assert claims["metadata_hash"] == _receiver_hash(signed)
        return httpx.Response(200, json={**version, "status": "active", "index": 1})
    _signed_local_client(monkeypatch, receive)
    body = mp.MarketplacePublishRequest(title="Test", description="Test", price_cents=2500, vz_dataset_id=local_dataset)
    assert (await mp.publish_via_signed_proxy(body, _request(), SimpleNamespace()))["status"] == "published"
    assert samples == [b"2\n"]
    with get_session_context() as session:
        assert session.get(DatasetMember, (local_dataset, 2)).index == 2


@pytest.mark.parametrize("file_type", ["directory", "csv"])
def test_patch_sample_refreshes_directory_count(local_dataset, file_type):
    import json
    with get_session_context() as session:
        record = session.get(DBDatasetRecord, local_dataset)
        record.file_type = file_type
        session.add(record)
        session.commit()
    for selected, count in [(True, 2), (False, 1)]:
        datasets.patch_dataset_member(local_dataset, 2, datasets.MemberPatch(is_sample=selected), user=None)
        with get_session_context() as session:
            metadata = json.loads(session.get(DBDatasetRecord, local_dataset).metadata_json)
            assert metadata["directory"]["sample_member_count"] == count


@pytest.mark.asyncio
async def test_deleted_dataset_during_upload_is_named_conflict(local_dataset, monkeypatch):
    import json
    import httpx
    local = mp._local_publish_snapshot(local_dataset)
    async def receive(request, claims):
        version = dict(version_id=str(uuid4()), version_label=local["version"].version_label, status="pending_members")
        if request.url.path.endswith("/publish"):
            return httpx.Response(200, json=dict(listing_id="listing", versions=[version]))
        with get_session_context() as session:
            session.delete(session.get(DBDatasetRecord, local_dataset))
            session.commit()
        return httpx.Response(200, json=version)
    _signed_local_client(monkeypatch, receive)
    body = mp.MarketplacePublishRequest(title="Test", description="Test", price_cents=2500, vz_dataset_id=local_dataset)
    with pytest.raises(HTTPException) as exc:
        await mp.publish_via_signed_proxy(body, _request(), SimpleNamespace())
    assert (exc.value.status_code, exc.value.detail) == (409, "dataset_removed_during_publish")
    with pytest.raises(HTTPException, match="dataset_removed_during_publish"):
        await mp.publish_status(dataset_id=local_dataset, user=None)


@pytest.mark.asyncio
@pytest.mark.parametrize("metadata", [None, "{}", "null", "[]", "invalid", '{"local_publish": null}'])
async def test_local_helpers_guard_metadata(local_dataset, metadata):
    with get_session_context() as session:
        record = session.get(DBDatasetRecord, local_dataset)
        record.metadata_json = metadata
        session.add(record)
        session.commit()
    with pytest.raises(HTTPException) as exc:
        mp._local_progress(local_dataset, 1, "pending_members")
    assert exc.value.status_code == 409
    assert exc.value.detail in ("local_publish_metadata_invalid", "local_publish_progress_missing")
    if metadata not in (None, "{}"):
        with pytest.raises(HTTPException, match="local_publish_metadata_invalid"):
            mp._local_publish_snapshot(local_dataset)
        with pytest.raises(HTTPException, match="local_publish_metadata_invalid"):
            await mp.publish_status(dataset_id=local_dataset, user=None)
    # The suite shares its database: do not leave corrupt rows for list endpoints.
    with get_session_context() as session:
        record = session.get(DBDatasetRecord, local_dataset)
        record.metadata_json = "{}"
        session.add(record)
        session.commit()


@pytest.mark.asyncio
async def test_pending_status_requires_keystore(local_dataset, monkeypatch):
    local = mp._local_publish_snapshot(local_dataset)
    mp._record_local_publish(local, "listing", str(uuid4()), "pending_members")
    monkeypatch.setattr(settings, "keystore_passphrase", "")
    result = await mp.publish_status(dataset_id=local_dataset, user=None)
    assert result["can_publish"] is False
    assert result["status"] == "pending_members"
    assert "Keystore" in result["reason"]


def test_sample_timeout_setting_alias(monkeypatch):
    from app.config import Settings
    assert Settings.model_fields["sample_upload_timeout_s"].default == 930
    monkeypatch.setenv("AIM_DATA_SAMPLE_UPLOAD_TIMEOUT_S", "1200")
    assert Settings(_env_file=None).sample_upload_timeout_s == 1200


@pytest.mark.asyncio
async def test_sample_retry_skips_persisted_acknowledged_sample(local_dataset, monkeypatch):
    import json
    import httpx
    with get_session_context() as session:
        member = session.get(DatasetMember, (local_dataset, 1))
        member.is_sample = True
        session.add(member)
        session.commit()
    local = mp._local_publish_snapshot(local_dataset)
    version_id = str(uuid4())
    seen = []
    async def receive(request, claims):
        version = {"version_id": version_id, "version_label": local["version"].version_label,
                   "status": "pending_members"}
        if request.url.path.endswith("/publish"):
            return httpx.Response(200, json={"listing_id": "listing", "versions": [version]})
        if request.url.path.endswith("/members"):
            return httpx.Response(200, json=version)
        index = int(request.url.path.rsplit("/", 1)[1])
        seen.append(index)
        if seen == [0, 1]:
            raise httpx.ReadError("sample 2 interrupted", request=request)
        return httpx.Response(200, json={**version, "index": index,
            "status": "active" if index == 1 else "pending_members"})
    _signed_local_client(monkeypatch, receive)
    body = mp.MarketplacePublishRequest(title="Test", description="Test", price_cents=2500, vz_dataset_id=local_dataset)
    with pytest.raises(HTTPException) as failure:
        await mp.publish_via_signed_proxy(body, _request(), SimpleNamespace())
    assert failure.value.status_code == 502
    with get_session_context() as session:
        progress = json.loads(session.get(DBDatasetRecord, local_dataset).metadata_json)["local_publish"]
        assert progress["accepted_sample_indices"] == [0]
    assert mp._local_publish_snapshot(local_dataset)["accepted_sample_indices"] == [0]
    assert (await mp.publish_via_signed_proxy(body, _request(), SimpleNamespace()))["status"] == "published"
    assert seen == [0, 1, 1]
