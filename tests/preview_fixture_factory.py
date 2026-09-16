"""Clearly synthetic deterministic key fixtures; NEVER use these keys in an install."""
from datetime import datetime, timedelta, timezone
from cryptography.hazmat.primitives.asymmetric.ed25519 import Ed25519PrivateKey
from app.services.preview_signing_service import (
    public_bytes, fingerprint, proof_bytes, commitment_bytes, disclosure_bytes,
    LocalCandidate, construct_request, PreviewSigningService,
)
from app.services.dataset_merkle_service import encode_base64url as enc, compute_leaf_hash, compute_node_hash
from app.services.dataset_canonicalization import CanonicalSchema
from app.services.preview_content_policy import sampled_leaf_list_digest, scan_attestation_digest
from app.services.preview_package_service import sample_hash

NOW = datetime(2026, 9, 17, tzinfo=timezone.utc)
STAMP = "2026-09-17T00:00:00.000000Z"
def uid(n):
    return f"00000000-0000-4000-8000-{n:012d}"

def test_key(offset=0):
    return Ed25519PrivateKey.from_private_bytes(bytes(range(offset, offset + 32)))
test_key.__test__ = False


def material():
    key = test_key()
    ref = uid(1) + ":" + fingerprint(public_bytes(key.public_key()))
    desc = [["amount", "decimal", False, {"precision": 12, "scale": 2}], ["count", "signed_integer", False, {}]]
    schema = CanonicalSchema(desc)
    bases = [enc(bytes([i]) * 32) for i in (7, 8)]
    leaves = [compute_leaf_hash(base, 0) for base in bases]
    root = enc(compute_node_hash(*leaves))
    dummy = enc(bytes(64))
    proofs = [dict(proof_id=uid(10+i), base_row_digest=base, duplicate_ordinal=0,
                   leaf_index=i, tree_size=2, siblings=[dict(hash=enc(leaves[1-i]), direction="right" if i==0 else "left")],
                   preview_package_url="https://seller.example/previews/package.json", package_media_type="application/vnd.aim.preview+json",
                   package_profile="aim-preview-package-v2", package_byte_ceiling=1048576,
                   scan_policy="aim-preview-policy-v1", scan_policy_version="1.0.0", scan_verdict="passed",
                   scanned_at=STAMP, sampled_leaf_list_digest=enc(bytes(32)), signer_reference=ref,
                   signature_algorithm="ed25519", signature=dummy) for i, base in enumerate(bases)]
    sampled = sampled_leaf_list_digest(proofs)
    for p in proofs:
        p["sampled_leaf_list_digest"] = sampled
    c = dict(commitment_id=uid(3), listing_id=uid(4), seller_dataset_version="fixture-v1",
             previous_commitment_id=None, canonicalization_profile="aim-dataset-merkle-v1", hash_algorithm="sha-256",
             schema_digest=enc(schema.digest), dataset_merkle_root=root, leaf_count=2,
             seller_attestation_digest=enc(bytes([5])*32), aim_data_signer_reference=ref,
             signature_algorithm="ed25519", seller_signature=dummy, signed_at=STAMP, proofs=proofs)
    for p in proofs:
        p["signature"] = enc(key.sign(proof_bytes(c, p)))
    c["seller_signature"] = enc(key.sign(commitment_bytes(c)))
    b = dict(profile="aim-preview-disclosure-v1", decision="approve", summary_id=uid(5), disclosure_version=uid(6),
             seller_id=uid(2), listing_id=uid(4), listing_version_id=None, content_revision="revision-1", source_revision="source-1",
             summary_approval_id="p1:approval:1", summary_hash="a"*64, render_hash="b"*64,
             selected_fields=["amount", "count"], preview_type="table", content_type="tabular", sample_decision="approved",
             sample_hash=sample_hash(proofs), aggregate_hash="c"*64, commitment_id=uid(3), schema_digest=enc(schema.digest),
             seller_dataset_version="fixture-v1", schema_descriptors=desc, proof_ids=[p["proof_id"] for p in proofs],
             sampled_leaf_list_digest=sampled, scan_attestation_digest=scan_attestation_digest(proofs),
             rights_basis_digest="d"*64, rights_basis_code="owner", public_preview_permission=True,
             approved_by=uid(2), approved_at=STAMP, last_attested_by_seller_at=STAMP, update_cadence_days=14,
             approval_expires_at=None, supersedes=None, request_id=uid(7), expected_current_disclosure_id=None,
             signer_reference=ref, signature_algorithm="ed25519", signature_profile="aim-preview-disclosure-signature-v1")
    return key, c, b


def request_fixture():
    key, c, b = material()
    return dict(profile=b["profile"], summary_id=b["summary_id"], binding=b,
                seller_signature=enc(key.sign(disclosure_bytes(b))), commitment=c, proofs=c["proofs"])


def p1(binding):
    return {k: binding[k] for k in ("summary_id", "summary_approval_id", "summary_hash", "render_hash", "aggregate_hash", "content_revision", "source_revision", "listing_id", "listing_version_id")}
