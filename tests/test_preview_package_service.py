import json
from uuid import uuid4

import httpx
import pytest

from app.services.dataset_canonicalization import canonicalize_row, compute_schema_digest
from app.services.dataset_merkle_service import DatasetMerkleService
from app.services.preview_package_service import PreviewPackageError, PreviewPackageService


def _commitment(count=3, value="x"):
    schema = [
        {"name": "id", "type": "signed_integer", "nullable": False, "type_parameters": {}},
        {"name": "value", "type": "string", "nullable": False, "type_parameters": {}},
    ]
    digest = compute_schema_digest(schema)
    records = [canonicalize_row(schema, {"id": index, "value": value}, schema_digest=digest) for index in range(count)]
    return DatasetMerkleService().build(records, schema_digest=digest)


def test_package_enforces_closed_shape_caps_and_duplicate_leaf_uniqueness():
    service = PreviewPackageService()
    commitment = _commitment()
    package = service.build(commitment, commitment_id=uuid4(), selected_leaf_indices=[0, 2])
    assert len(package.body["entries"]) == 2
    assert package.canonical_row_bytes == sum(
        len(json.dumps(entry["row"], separators=(",", ":")).encode()) for entry in package.body["entries"]
    )
    with pytest.raises(PreviewPackageError) as duplicate:
        service.build(commitment, commitment_id=uuid4(), selected_leaf_indices=[0, 0])
    assert duplicate.value.code == "duplicate_leaf_index"
    with pytest.raises(PreviewPackageError) as count:
        service.build(_commitment(51), commitment_id=uuid4(), selected_leaf_indices=list(range(51)))
    assert count.value.code == "preview_row_limit_exceeded"


def test_package_validation_distinguishes_leaf_index_and_leaf_identity_collisions():
    service = PreviewPackageService()
    package = service.build(_commitment(), commitment_id=uuid4(), selected_leaf_indices=[0, 1])

    duplicate_index = json.loads(package.encoded)
    duplicate_index["entries"][1]["leaf_index"] = duplicate_index["entries"][0]["leaf_index"]
    with pytest.raises(PreviewPackageError) as index_error:
        service.validate_package(duplicate_index)
    assert index_error.value.code == "duplicate_leaf_index"

    duplicate_identity = json.loads(package.encoded)
    duplicate_identity["entries"][1]["base_row_digest"] = duplicate_identity["entries"][0]["base_row_digest"]
    duplicate_identity["entries"][1]["duplicate_ordinal"] = duplicate_identity["entries"][0]["duplicate_ordinal"]
    with pytest.raises(PreviewPackageError) as identity_error:
        service.validate_package(duplicate_identity)
    assert identity_error.value.code == "duplicate_leaf_identity"


def test_package_rejects_combined_canonical_row_bytes_over_5120():
    with pytest.raises(PreviewPackageError) as exc:
        PreviewPackageService().build(_commitment(2, "x" * 3000), commitment_id=uuid4(), selected_leaf_indices=[0, 1])
    assert exc.value.code == "preview_row_bytes_exceeded"


def test_package_validation_rejects_unknown_carrier_fields():
    service = PreviewPackageService()
    package = service.build(_commitment(), commitment_id=uuid4(), selected_leaf_indices=[0])
    malformed = dict(package.body)
    malformed["response_body"] = "forbidden"
    with pytest.raises(PreviewPackageError) as exc:
        service.validate_package(malformed)
    assert exc.value.code == "preview_package_shape_invalid"


@pytest.mark.asyncio
async def test_customer_side_origin_validation_enforces_media_cors_profile_and_cap():
    service = PreviewPackageService()
    commitment_id = uuid4()
    package = service.build(_commitment(), commitment_id=commitment_id, selected_leaf_indices=[0])
    captured = {}

    async def handler(request: httpx.Request) -> httpx.Response:
        captured["headers"] = dict(request.headers)
        return httpx.Response(
            200,
            content=package.encoded,
            headers={
                "Content-Type": "application/vnd.aim.preview+json",
                "Access-Control-Allow-Origin": "https://ai.market",
            },
        )

    async def resolver(_hostname):
        return ["93.184.216.34"]

    async with httpx.AsyncClient(transport=httpx.MockTransport(handler)) as client:
        validated = await service.validate_origin(
            "https://seller.example/preview.json",
            expected_commitment_id=commitment_id,
            client=client,
            resolver=resolver,
        )
    assert validated.encoded == package.encoded
    assert captured["headers"]["accept"] == "application/vnd.aim.preview+json"
    assert captured["headers"]["origin"] == "https://ai.market"
    assert "authorization" not in captured["headers"]
    assert "cookie" not in captured["headers"]
    assert "referer" not in captured["headers"]
