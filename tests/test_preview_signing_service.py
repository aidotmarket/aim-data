"""Synthetic keys only; no customer keystore is opened by these tests."""
from datetime import datetime, timedelta, timezone
import pytest
from app.core.crypto import DeviceCrypto
from app.services.preview_signing_service import (
    PreviewSigningService, SigningError, public_bytes, fingerprint,
)

NOW = datetime(2026, 9, 17, tzinfo=timezone.utc)
INSTALL = "00000000-0000-4000-8000-000000000001"
SELLER = "00000000-0000-4000-8000-000000000002"


@pytest.fixture
def signer(tmp_path):
    crypto = DeviceCrypto(str(tmp_path / "keystore.json"), "synthetic-test-passphrase")
    crypto._pbkdf2_iterations = 1  # test-only speed; production default untouched
    keys = crypto.get_or_create_keypairs()
    evidence = dict(install_id=INSTALL, seller_id=SELLER,
                    fingerprint=fingerprint(public_bytes(keys[1])), status="active",
                    observed_at=NOW)
    service = PreviewSigningService(crypto, install_id=INSTALL, seller_id=SELLER,
                                   evidence_reader=lambda: evidence, evidence_max_age=timedelta(hours=1), clock=lambda: NOW)
    return service, evidence


def test_existing_install_key_only(signer):
    s, e = signer
    assert s.signer_reference == INSTALL + ":" + e["fingerprint"]
    s.crypto.keystore_path.unlink()
    with pytest.raises(SigningError, match="signing_authority_unavailable"):
        s.signer_reference
    assert not s.crypto.keystore_path.exists()


@pytest.mark.parametrize("field,value", [
    ("status", "revoked"), ("status", "rotated"), ("fingerprint", "0" * 64),
    ("seller_id", INSTALL), ("install_id", SELLER),
    ("observed_at", NOW - timedelta(hours=1)), ("observed_at", NOW + timedelta(seconds=1)),
])
def test_registration_fail_closed(signer, field, value):
    s, e = signer
    e[field] = value
    with pytest.raises(SigningError):
        s.signer_reference


def test_missing_evidence(signer):
    s, e = signer
    e.clear()
    with pytest.raises(SigningError):
        s.signer_reference


def test_missing_passphrase_and_pending_rotation(signer):
    s, _ = signer
    s.crypto.keystore_path.with_suffix(".rotation-pending").touch()
    with pytest.raises(SigningError):
        s.signer_reference
    s.crypto.keystore_path.with_suffix(".rotation-pending").unlink()
    s.crypto._passphrase = b""
    with pytest.raises(SigningError):
        s.signer_reference
