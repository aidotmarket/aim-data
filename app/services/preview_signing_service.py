"""Non-custodial F2 signing. Never allocates candidates or submits previews.

The existing encrypted install identity is the only seller signing key. Platform
signatures are verification-only; fixture generation lives exclusively in tests.
"""
from datetime import datetime, timedelta, timezone
import hashlib
import os
from pathlib import Path
import stat
from typing import Literal

from cryptography.exceptions import InvalidSignature
from cryptography.hazmat.primitives import serialization
from cryptography.hazmat.primitives.asymmetric.ed25519 import Ed25519PublicKey
from pydantic import ValidationError

from app.core.crypto import DeviceCrypto
from app.models.dataset_commitment_schemas import (
    WireModel, UUIDText, Timestamp, HexDigest, DatasetCommitmentContract,
    DatasetPreviewProofContract, TransparencyCheckpointContract,
)
from app.services.dataset_merkle_service import (
    canonical_json_bytes, decode_base64url, encode_base64url,
    checkpoint_signing_bytes,
)


class SigningError(ValueError):
    """Only fixed codes may cross the producer boundary."""


class RegistrationEvidence(WireModel):
    install_id: UUIDText
    seller_id: UUIDText
    fingerprint: HexDigest
    status: Literal["active", "rotated", "revoked"]
    observed_at: Timestamp


def closed(model, value):
    try:
        return model.model_validate(value).model_dump(mode="json")
    except (ValueError, TypeError, ValidationError):
        raise SigningError("contract_mismatch") from None


def serialize(model, value):
    """The single closed signing serializer; all included nulls/defaults remain."""
    return canonical_json_bytes(closed(model, value))


def public_bytes(key):
    return key.public_bytes(serialization.Encoding.Raw, serialization.PublicFormat.Raw)


def fingerprint(raw):
    if not isinstance(raw, bytes) or len(raw) != 32:
        raise SigningError("invalid_public_key")
    return hashlib.sha256(raw).hexdigest()


def check_evidence(evidence, *, install_id, seller_id, raw_key, now, max_age):
    if max_age <= timedelta(0) or now.tzinfo is None:
        raise SigningError("invalid_evidence_policy")
    e = RegistrationEvidence(**closed(RegistrationEvidence, evidence))
    observed = datetime.fromisoformat(e.observed_at.replace("Z", "+00:00"))
    if e.install_id != install_id or e.seller_id != seller_id:
        raise SigningError("registration_owner_mismatch")
    if e.fingerprint != fingerprint(raw_key):
        raise SigningError("registration_key_mismatch")
    if e.status != "active":
        raise SigningError("registration_inactive")
    if observed > now or now - observed >= max_age:
        raise SigningError("registration_evidence_stale")
    return e


def proof_bytes(context, proof):
    p = closed(DatasetPreviewProofContract, proof)
    # Context uses the commitment model, so no ad hoc dictionaries can be signed.
    c = closed(DatasetCommitmentContract, context)
    p.pop("signature")
    return b"aim-preview-proof-signature-v1\0" + canonical_json_bytes({
        **{k: c[k] for k in ("commitment_id", "listing_id", "seller_dataset_version", "schema_digest", "dataset_merkle_root")},
        "proof": p,
    })


def commitment_bytes(commitment):
    c = closed(DatasetCommitmentContract, commitment)
    c.pop("seller_signature")
    return b"aim-dataset-commitment-signature-v1\0" + canonical_json_bytes(c)


def disclosure_bytes(binding):
    from app.models.preview_disclosure_schemas import DisclosureBinding
    return b"aim-preview-disclosure-signature-v1\0" + serialize(DisclosureBinding, binding)


def platform_envelope_bytes(envelope):
    from app.models.preview_disclosure_schemas import PlatformEnvelope
    e = closed(PlatformEnvelope, envelope)
    e.pop("signature")
    # T explicitly permits omission only for open-ended signer valid_until.
    for key in e["signer_keys"]:
        if key["valid_until"] is None:
            key.pop("valid_until")
    return b"aim-preview-platform-envelope-v1\0" + canonical_json_bytes(e)


def verify_bytes(raw_key, signature, message):
    try:
        sig = decode_base64url(signature)
        if len(sig) != 64 or len(raw_key) != 32:
            return False
        Ed25519PublicKey.from_public_bytes(raw_key).verify(sig, message)
        return True
    except (ValueError, TypeError, InvalidSignature):
        return False


def verify_platform_envelope(envelope, trusted_keys):
    from app.models.preview_disclosure_schemas import PlatformEnvelope
    e = closed(PlatformEnvelope, envelope)
    _check_trust_ids(trusted_keys)
    if e["key_id"] not in trusted_keys or not verify_bytes(trusted_keys[e["key_id"]], e["signature"], platform_envelope_bytes(e)):
        raise SigningError("platform_signature_invalid")
    return e  # Seller evidence must never be consumed before this succeeds.


def _check_trust_ids(trusted_keys):
    if len(set(trusted_keys.values())) != len(trusted_keys):
        raise SigningError("platform_key_alias")


def verify_checkpoint(checkpoint, trusted_keys):
    c = closed(TransparencyCheckpointContract, checkpoint)
    _check_trust_ids(trusted_keys)
    preimage = checkpoint_signing_bytes(c["log_id"], c["tree_size"], c["root_hash"], c["checkpoint_at"])
    if c["key_id"] not in trusted_keys or not verify_bytes(trusted_keys[c["key_id"]], c["signature"], preimage):
        raise SigningError("checkpoint_signature_invalid")
    return c


class PreviewSigningService:
    def __init__(self, crypto: DeviceCrypto, *, install_id, seller_id, evidence_reader,
                 evidence_max_age: timedelta, clock=lambda: datetime.now(timezone.utc)):
        self.crypto = crypto
        self.install_id = str(install_id)
        self.seller_id = str(seller_id)
        self.evidence_reader = evidence_reader
        self.max_age = evidence_max_age
        self.clock = clock

    def _keys(self):
        path = self.crypto.keystore_path
        try:
            if not self.crypto._passphrase or path.is_symlink() or path.with_suffix(".rotation-pending").exists():
                raise SigningError("keystore_unavailable")
            if stat.S_IMODE(path.stat().st_mode) != 0o600 or path.parent.stat().st_mode & 0o022:
                raise SigningError("keystore_permissions")
            # Never call get_or_create_keypairs: missing/corrupt identity must fail.
            keys = self.crypto._load_keys(self.crypto._read_keystore())
            raw = public_bytes(keys[1])
            if public_bytes(keys[0].public_key()) != raw:
                raise SigningError("keystore_key_mismatch")
            check_evidence(self.evidence_reader(), install_id=self.install_id,
                           seller_id=self.seller_id, raw_key=raw, now=self.clock(), max_age=self.max_age)
            return keys
        except Exception:
            raise SigningError("signing_authority_unavailable") from None

    @property
    def signer_reference(self):
        return self.install_id + ":" + fingerprint(public_bytes(self._keys()[1]))

    def _sign(self, preimage, reference):
        keys = self._keys()  # Read status afresh for EVERY signature.
        if reference != self.install_id + ":" + fingerprint(public_bytes(keys[1])):
            raise SigningError("signer_reference_mismatch")
        return encode_base64url(keys[0].sign(preimage))

    def sign_proof(self, context, proof):
        p = closed(DatasetPreviewProofContract, proof)
        p["signature"] = self._sign(proof_bytes(context, p), p["signer_reference"])
        return p

    def sign_commitment(self, commitment):
        c = closed(DatasetCommitmentContract, commitment)
        for p in c["proofs"]:
            if p["signer_reference"] != c["aim_data_signer_reference"] or not verify_bytes(public_bytes(self._keys()[1]), p["signature"], proof_bytes(c, p)):
                raise SigningError("proof_signature_invalid")
        c["seller_signature"] = self._sign(commitment_bytes(c), c["aim_data_signer_reference"])
        return c

    def sign_disclosure(self, binding):
        from app.models.preview_disclosure_schemas import DisclosureBinding
        b = closed(DisclosureBinding, binding)
        if b["seller_id"] != self.seller_id:
            raise SigningError("registration_owner_mismatch")
        return self._sign(disclosure_bytes(b), b["signer_reference"])


# Candidate bytes are immutable and labelled fixture-only until I.b supplies an
# authenticated platform allocation. No mutable UI object is retained for retry.
from dataclasses import dataclass
import json


@dataclass(frozen=True)
class LocalCandidate:
    binding_bytes: bytes
    kind: Literal["fixture_candidate"] = "fixture_candidate"

    @classmethod
    def validate(cls, binding):
        from app.models.preview_disclosure_schemas import DisclosureBinding
        return cls(serialize(DisclosureBinding, binding))

    def binding(self):
        from app.models.preview_disclosure_schemas import DisclosureBinding
        if self.kind != "fixture_candidate":
            raise SigningError("integration_not_yet_available")
        data = closed(DisclosureBinding, json.loads(self.binding_bytes))
        if serialize(DisclosureBinding, data) != self.binding_bytes:
            raise SigningError("candidate_changed")
        return data


def construct_request(candidate, commitment, proofs, *, signer, approved_p1):
    """Sign an exact immutable candidate after independent P1 and proof checks.

    approved_p1 is the exact local approved P1 reference projection, never a
    generated summary. The full request is journaled by PreviewJournal.freeze.
    """
    from app.models.preview_disclosure_schemas import PreviewDisclosureRequest
    b = candidate.binding()
    fields = ("summary_id", "summary_approval_id", "summary_hash", "render_hash",
              "aggregate_hash", "content_revision", "source_revision", "listing_id",
              "listing_version_id")
    if not isinstance(approved_p1, dict) or set(approved_p1) != set(fields) or any(approved_p1[k] != b[k] for k in fields):
        raise SigningError("p1_reference_mismatch")
    # Validate the whole request before signing, then verify cryptographic material.
    dummy = encode_base64url(bytes(64))
    request = closed(PreviewDisclosureRequest, dict(profile=b["profile"], summary_id=b["summary_id"],
                    binding=b, seller_signature=dummy, commitment=commitment, proofs=proofs))
    if request["commitment"] is not None:
        c = request["commitment"]
        raw = public_bytes(signer._keys()[1])
        if not verify_bytes(raw, c["seller_signature"], commitment_bytes(c)):
            raise SigningError("commitment_signature_invalid")
        for p in request["proofs"]:
            if not verify_bytes(raw, p["signature"], proof_bytes(c, p)):
                raise SigningError("proof_signature_invalid")
    request["seller_signature"] = signer.sign_disclosure(b)
    return closed(PreviewDisclosureRequest, request)


def request_bytes(request):
    from app.models.preview_disclosure_schemas import PreviewDisclosureRequest
    return serialize(PreviewDisclosureRequest, request)


def request_digest(request):
    return hashlib.sha256(request_bytes(request)).hexdigest()


def verify_request(request, *, evidence, raw_key, now, max_age):
    from app.models.preview_disclosure_schemas import PreviewDisclosureRequest
    r = closed(PreviewDisclosureRequest, request)
    b = r["binding"]
    check_evidence(evidence, install_id=b["signer_reference"][:36], seller_id=b["seller_id"],
                   raw_key=raw_key, now=now, max_age=max_age)
    if b["signer_reference"][37:] != fingerprint(raw_key):
        raise SigningError("registration_key_mismatch")
    if not verify_bytes(raw_key, r["seller_signature"], disclosure_bytes(b)):
        raise SigningError("disclosure_signature_invalid")
    if r["commitment"]:
        c = r["commitment"]
        if not verify_bytes(raw_key, c["seller_signature"], commitment_bytes(c)):
            raise SigningError("commitment_signature_invalid")
        for p in r["proofs"]:
            if not verify_bytes(raw_key, p["signature"], proof_bytes(c, p)):
                raise SigningError("proof_signature_invalid")
    return r


def submit_preview_request(request):
    request_bytes(request)
    raise SigningError("preview_integration_not_yet_available")
