"""Unchanged-root re-attestation uses synthetic local files and keys only."""

from types import SimpleNamespace

import httpx
import pytest
from fastapi import HTTPException
from starlette.requests import Request

from app.models.dataset_commitment_schemas import DatasetReattestationContract
from app.core.crypto import DeviceCrypto
from app.routers import marketplace_publish
from app.services.dataset_canonicalization import ParsingDeclaration
from app.services.dataset_merkle_service import run_commitment_job
from app.services.dataset_reattestation_service import (
    COMMITMENT_METADATA_KEY,
    TRANSIENT_REATTESTATION_CODES,
    _local_failure,
)
from app.services import dataset_reattestation_service
from app.services.preview_signing_service import (
    PreviewSigningService,
    fingerprint,
    public_bytes,
    reattestation_bytes,
    verify_bytes,
)
from app.services.processing_service import DatasetRecord


OWNER = "00000000-0000-4000-8000-000000000002"
LISTING = "00000000-0000-4000-8000-000000000004"
COMMITMENT = "00000000-0000-4000-8000-000000000003"
PARSING = {
    "format": "csv",
    "encoding": "utf-8",
    "delimiter": ",",
    "quote": '"',
    "escape": "",
    "header": True,
    "locale": "C",
    "null_token": "",
    "source_timezone": None,
}
DESCRIPTORS = [["id", "string", False, {}], ["name", "string", False, {}]]


@pytest.fixture
def signer(tmp_path):
    crypto = DeviceCrypto(str(tmp_path / "keystore.json"), "synthetic-test-passphrase")
    crypto._pbkdf2_iterations = 1
    keys = crypto.get_or_create_keypairs()
    return (
        PreviewSigningService(crypto, install_id="00000000-0000-4000-8000-000000000001", seller_id=OWNER),
        fingerprint(public_bytes(keys[1])),
    )


class Processing:
    def __init__(self, record):
        self.record = record
        self.saved = 0

    def get_dataset(self, dataset_id):
        return self.record if dataset_id == self.record.id else None

    def _save_record(self, record, _storage_name):
        self.record = record
        self.saved += 1


class Response:
    def __init__(self, status_code, body):
        self.status_code = status_code
        self._body = body
        self.text = str(body)

    def json(self):
        return self._body


class Client:
    def __init__(self, *, response=None, error=None, capture=None, **_kwargs):
        self.response = response
        self.error = error
        self.capture = capture

    async def __aenter__(self):
        return self

    async def __aexit__(self, *_args):
        return False

    async def post(self, url, **kwargs):
        if self.capture is not None:
            self.capture.update(url=url, kwargs=kwargs)
        if self.error:
            raise self.error
        return self.response


def local_fixture(tmp_path, *, descriptors=DESCRIPTORS):
    source = tmp_path / "dataset.csv"
    source.write_text("id,name\n1,alpha\n2,beta\n")
    initial = run_commitment_job(
        [source],
        ParsingDeclaration(**PARSING),
        descriptors,
        tmp_path / "initial-worker",
    )["commitment"]
    record = DatasetRecord("dataset", "dataset.csv", "csv")
    record.upload_path = source
    record.listing_id = LISTING
    record.metadata = {
        "preview_owner_id": OWNER,
        COMMITMENT_METADATA_KEY: {
            "commitment_id": COMMITMENT,
            "listing_id": LISTING,
            "seller_dataset_version": "fixture-v1",
            "schema_digest": initial["schema_digest"],
            "dataset_merkle_root": initial["dataset_merkle_root"],
            "leaf_count": initial["leaf_count"],
            "sample_hash": "1" * 64,
            "rights_basis_digest": "2" * 64,
            "parsing": PARSING,
            "schema_descriptors": descriptors,
        },
    }
    return source, record, Processing(record)


def request():
    return Request(
        {
            "type": "http",
            "method": "POST",
            "path": f"/marketplace/listings/{LISTING}/commitments/{COMMITMENT}/reattest",
            "headers": [(b"authorization", b"Bearer test")],
        }
    )


def test_local_failure_taxonomy_is_closed():
    for code in TRANSIENT_REATTESTATION_CODES:
        failure = _local_failure(code)
        assert (failure.code, failure.retryable) == (code, True)
    for code in ("invalid_integer", "invalid_decimal", "future_parser_error"):
        failure = _local_failure(code)
        assert (failure.code, failure.retryable) == ("dataset_changed", False)


@pytest.mark.asyncio
@pytest.mark.parametrize(
    "dataset_id,user_id,auth_headers,status,code,message",
    [
        (
            "missing",
            OWNER,
            {"Authorization": "Bearer test"},
            404,
            "dataset_not_found",
            "The local dataset could not be found.",
        ),
        (
            "dataset",
            "00000000-0000-4000-8000-000000000099",
            {"Authorization": "Bearer test"},
            403,
            "dataset_owner_unverified",
            "This signed-in seller does not own the local dataset.",
        ),
        (
            "dataset",
            OWNER,
            {},
            403,
            "seller_auth_required",
            "Your ai.market sign-in has expired or is unavailable. Sign in and confirm again.",
        ),
    ],
    ids=["not-found", "dataset-owner", "seller-auth"],
)
async def test_preflight_refusals_are_structured_and_never_construct_http_client(
    tmp_path, monkeypatch, dataset_id, user_id, auth_headers, status, code, message
):
    _, _, processing = local_fixture(tmp_path)
    monkeypatch.setattr(marketplace_publish, "_seller_auth_headers", lambda _request: auth_headers)

    def network_forbidden(**_kwargs):
        pytest.fail(f"httpx.AsyncClient constructed after {code} refusal")

    monkeypatch.setattr(marketplace_publish.httpx, "AsyncClient", network_forbidden)
    with pytest.raises(HTTPException) as exc:
        await marketplace_publish.reattest_dataset_commitment(
            LISTING,
            COMMITMENT,
            marketplace_publish.DatasetReattestationProxyRequest(dataset_id=dataset_id),
            request(),
            user=SimpleNamespace(user_id=user_id),
            processing=processing,
        )
    assert exc.value.status_code == status
    assert exc.value.detail == {"code": code, "message": message, "retryable": False}


def test_registration_owner_mismatch_is_structured(monkeypatch):
    state = SimpleNamespace(
        vz_install_id="00000000-0000-4000-8000-000000000001",
        ai_market_seller_id="00000000-0000-4000-8000-000000000099",
    )
    monkeypatch.setattr(
        marketplace_publish, "get_serial_store", lambda: SimpleNamespace(state=state)
    )
    with pytest.raises(HTTPException) as exc:
        marketplace_publish._preview_signer(OWNER)
    assert exc.value.status_code == 403
    assert exc.value.detail == {
        "code": "registration_owner_mismatch",
        "message": "This AIM Data install is registered to a different seller. Sign in with the matching ai.market account.",
        "retryable": False,
    }


@pytest.mark.asyncio
async def test_registration_owner_mismatch_refuses_before_recompute(
    tmp_path, monkeypatch
):
    _, _, processing = local_fixture(tmp_path)
    state = SimpleNamespace(
        vz_install_id="00000000-0000-4000-8000-000000000001",
        ai_market_seller_id="00000000-0000-4000-8000-000000000099",
    )
    monkeypatch.setattr(
        marketplace_publish, "get_serial_store", lambda: SimpleNamespace(state=state)
    )
    monkeypatch.setattr(marketplace_publish.settings, "upload_directory", str(tmp_path))
    monkeypatch.setattr(marketplace_publish.settings, "data_directory", str(tmp_path))
    original_run_commitment_job = dataset_reattestation_service.run_commitment_job
    recompute_calls = []

    def tracked_recompute(*args, **kwargs):
        recompute_calls.append((args, kwargs))
        return original_run_commitment_job(*args, **kwargs)

    monkeypatch.setattr(
        dataset_reattestation_service, "run_commitment_job", tracked_recompute
    )
    with pytest.raises(HTTPException) as exc:
        await marketplace_publish.reattest_dataset_commitment(
            LISTING,
            COMMITMENT,
            marketplace_publish.DatasetReattestationProxyRequest(dataset_id="dataset"),
            request(),
            user=SimpleNamespace(user_id=OWNER),
            processing=processing,
        )
    assert exc.value.status_code == 403
    assert exc.value.detail == {
        "code": "registration_owner_mismatch",
        "message": "This AIM Data install is registered to a different seller. Sign in with the matching ai.market account.",
        "retryable": False,
    }
    assert not recompute_calls, (
        "run_commitment_job invoked before registration mismatch refusal"
    )


@pytest.mark.asyncio
@pytest.mark.parametrize(
    "replacement",
    [
        "id,name\n1,omega\n2,beta\n",
        "id,label\n1,alpha\n2,beta\n",
        "id,name\n1,alpha\n",
    ],
    ids=["changed-value", "changed-schema", "changed-row-membership"],
)
async def test_changed_dataset_refuses_before_network(
    tmp_path, monkeypatch, signer, replacement
):
    source, record, processing = local_fixture(tmp_path)
    source.write_text(replacement)
    monkeypatch.setattr(marketplace_publish.settings, "upload_directory", str(tmp_path))
    monkeypatch.setattr(marketplace_publish.settings, "data_directory", str(tmp_path))
    signing_service, _ = signer
    monkeypatch.setattr(marketplace_publish, "_preview_signer", lambda _owner: signing_service)

    def network_forbidden(**_kwargs):
        pytest.fail("network called after local freshness refusal")

    monkeypatch.setattr(marketplace_publish.httpx, "AsyncClient", network_forbidden)
    with pytest.raises(HTTPException) as exc:
        await marketplace_publish.reattest_dataset_commitment(
            LISTING,
            COMMITMENT,
            marketplace_publish.DatasetReattestationProxyRequest(dataset_id="dataset"),
            request(),
            user=SimpleNamespace(user_id=OWNER),
            processing=processing,
        )
    assert exc.value.status_code == 409
    assert exc.value.detail["retryable"] is False
    assert "Publish a new version" in exc.value.detail["message"]
    assert record.metadata["dataset_reattestation"]["status"] == "new_commitment_required"


@pytest.mark.asyncio
async def test_declared_integer_changed_to_text_requires_new_version_before_network(
    tmp_path, monkeypatch, signer
):
    descriptors = [["id", "signed_integer", False, {}], ["name", "string", False, {}]]
    source, record, processing = local_fixture(tmp_path, descriptors=descriptors)
    source.write_text("id,name\nnot-an-integer,alpha\n2,beta\n")
    monkeypatch.setattr(marketplace_publish.settings, "upload_directory", str(tmp_path))
    monkeypatch.setattr(marketplace_publish.settings, "data_directory", str(tmp_path))
    signing_service, _ = signer
    monkeypatch.setattr(marketplace_publish, "_preview_signer", lambda _owner: signing_service)

    def network_forbidden(**_kwargs):
        pytest.fail("httpx.AsyncClient constructed after invalid_integer refusal")

    monkeypatch.setattr(marketplace_publish.httpx, "AsyncClient", network_forbidden)
    with pytest.raises(HTTPException) as exc:
        await marketplace_publish.reattest_dataset_commitment(
            LISTING,
            COMMITMENT,
            marketplace_publish.DatasetReattestationProxyRequest(dataset_id="dataset"),
            request(),
            user=SimpleNamespace(user_id=OWNER),
            processing=processing,
        )
    assert exc.value.status_code == 409
    assert exc.value.detail == {
        "code": "dataset_changed",
        "message": "The data changed. Publish a new version.",
        "retryable": False,
    }
    assert record.metadata["dataset_reattestation"]["status"] == "new_commitment_required"


@pytest.mark.asyncio
async def test_untouched_dataset_signs_backend_contract_and_persists(
    tmp_path, monkeypatch, signer
):
    _, record, processing = local_fixture(tmp_path)
    signing_service, _ = signer
    capture = {}
    monkeypatch.setattr(marketplace_publish.settings, "upload_directory", str(tmp_path))
    monkeypatch.setattr(marketplace_publish.settings, "data_directory", str(tmp_path))
    monkeypatch.setattr(marketplace_publish.settings, "ai_market_url", "https://ai.market.test")
    monkeypatch.setattr(marketplace_publish, "_preview_signer", lambda _owner: signing_service)
    monkeypatch.setattr(
        marketplace_publish.httpx,
        "AsyncClient",
        lambda **kwargs: Client(
            response=Response(
                201,
                {
                    "attestation_id": "00000000-0000-4000-8000-000000000009",
                    "commitment_id": COMMITMENT,
                    "signed_at": "2026-09-20T12:00:00.000000Z",
                },
            ),
            capture=capture,
            **kwargs,
        ),
    )
    result = await marketplace_publish.reattest_dataset_commitment(
        LISTING,
        COMMITMENT,
        marketplace_publish.DatasetReattestationProxyRequest(dataset_id="dataset"),
        request(),
        user=SimpleNamespace(user_id=OWNER),
        processing=processing,
    )
    payload = capture["kwargs"]["json"]
    DatasetReattestationContract.model_validate(payload)
    assert verify_bytes(
        public_bytes(signing_service._keys()[1]),
        payload["seller_signature"],
        reattestation_bytes(payload),
    )
    assert capture["url"].endswith(
        f"/api/v1/listings/{LISTING}/commitments/{COMMITMENT}/reattest"
    )
    assert result.status == "complete"
    assert record.metadata["dataset_reattestation"]["last_confirmed_at"] == result.signed_at


@pytest.mark.asyncio
async def test_success_without_attestation_id_is_retryable_exchange_failure(
    tmp_path, monkeypatch, signer
):
    _, record, processing = local_fixture(tmp_path)
    signing_service, _ = signer
    monkeypatch.setattr(marketplace_publish.settings, "upload_directory", str(tmp_path))
    monkeypatch.setattr(marketplace_publish.settings, "data_directory", str(tmp_path))
    monkeypatch.setattr(
        marketplace_publish, "_preview_signer", lambda _owner: signing_service
    )
    monkeypatch.setattr(
        marketplace_publish.httpx,
        "AsyncClient",
        lambda **kwargs: Client(
            response=Response(
                201, {"commitment_id": COMMITMENT, "signed_at": "2026-09-20T12:00:00.000000Z"}
            ),
            **kwargs,
        ),
    )
    with pytest.raises(HTTPException) as exc:
        await marketplace_publish.reattest_dataset_commitment(
            LISTING,
            COMMITMENT,
            marketplace_publish.DatasetReattestationProxyRequest(dataset_id="dataset"),
            request(),
            user=SimpleNamespace(user_id=OWNER),
            processing=processing,
        )
    assert exc.value.status_code == 502
    assert exc.value.detail == {
        "code": "upstream_invalid_response",
        "message": "ai.market did not return an attestation ID. Try again.",
        "retryable": True,
    }
    assert record.metadata["dataset_reattestation"]["status"] == "retryable_error"
    assert record.metadata["dataset_reattestation"]["last_error"] == exc.value.detail["message"]
    assert record.metadata["dataset_reattestation"]["retryable"] is True
    assert "attestation_id" not in record.metadata["dataset_reattestation"]


@pytest.mark.asyncio
async def test_proxy_maps_new_commitment_required_without_retry(
    tmp_path, monkeypatch, signer
):
    _, record, processing = local_fixture(tmp_path)
    signing_service, _ = signer
    monkeypatch.setattr(marketplace_publish.settings, "upload_directory", str(tmp_path))
    monkeypatch.setattr(marketplace_publish.settings, "data_directory", str(tmp_path))
    monkeypatch.setattr(marketplace_publish, "_preview_signer", lambda _owner: signing_service)
    monkeypatch.setattr(
        marketplace_publish.httpx,
        "AsyncClient",
        lambda **kwargs: Client(response=Response(409, {"detail": "new_commitment_required"}), **kwargs),
    )
    with pytest.raises(HTTPException) as exc:
        await marketplace_publish.reattest_dataset_commitment(
            LISTING,
            COMMITMENT,
            marketplace_publish.DatasetReattestationProxyRequest(dataset_id="dataset"),
            request(),
            user=SimpleNamespace(user_id=OWNER),
            processing=processing,
        )
    assert exc.value.status_code == 409
    assert exc.value.detail == {
        "code": "new_commitment_required",
        "message": "The data changed. Publish a new version.",
        "retryable": False,
    }
    assert record.metadata["dataset_reattestation"]["retryable"] is False


@pytest.mark.asyncio
@pytest.mark.parametrize(
    "error,status,code,message",
    [
        (
            httpx.ConnectError("offline"),
            502,
            "transport_error",
            "The connection to ai.market was interrupted. Try again.",
        ),
        (httpx.ReadTimeout("slow"), 504, "timeout", "ai.market timed out. Try again."),
        (
            httpx.RemoteProtocolError("peer reset mid-response"),
            502,
            "transport_error",
            "The connection to ai.market was interrupted. Try again.",
        ),
    ],
    ids=["connect-failure", "timeout", "mid-response-transport-loss"],
)
async def test_proxy_keeps_transport_failures_retryable(
    tmp_path, monkeypatch, signer, error, status, code, message
):
    _, record, processing = local_fixture(tmp_path)
    signing_service, _ = signer
    monkeypatch.setattr(marketplace_publish.settings, "upload_directory", str(tmp_path))
    monkeypatch.setattr(marketplace_publish.settings, "data_directory", str(tmp_path))
    monkeypatch.setattr(marketplace_publish, "_preview_signer", lambda _owner: signing_service)
    monkeypatch.setattr(
        marketplace_publish.httpx,
        "AsyncClient",
        lambda **kwargs: Client(error=error, **kwargs),
    )
    with pytest.raises(HTTPException) as exc:
        await marketplace_publish.reattest_dataset_commitment(
            LISTING,
            COMMITMENT,
            marketplace_publish.DatasetReattestationProxyRequest(dataset_id="dataset"),
            request(),
            user=SimpleNamespace(user_id=OWNER),
            processing=processing,
        )
    assert exc.value.status_code == status
    assert exc.value.detail["code"] == code
    assert exc.value.detail["message"] == message
    assert exc.value.detail["retryable"] is True
    assert record.metadata["dataset_reattestation"]["retryable"] is True
