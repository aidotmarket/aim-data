"""Clearly synthetic deterministic key fixtures; NEVER use these keys in an install."""

from datetime import datetime, timezone
from cryptography.hazmat.primitives.asymmetric.ed25519 import Ed25519PrivateKey
from app.services.preview_signing_service import (
    public_bytes,
    fingerprint,
    proof_bytes,
    commitment_bytes,
    disclosure_bytes,
)
from app.services.dataset_merkle_service import (
    encode_base64url as enc,
    compute_leaf_hash,
    compute_node_hash,
)
from app.services.dataset_canonicalization import CanonicalSchema
from app.services.preview_content_policy import (
    sampled_leaf_list_digest,
    scan_attestation_digest,
)
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
    desc = [
        ["amount", "decimal", False, {"precision": 12, "scale": 2}],
        ["count", "signed_integer", False, {}],
    ]
    schema = CanonicalSchema(desc)
    bases = [enc(bytes([i]) * 32) for i in (7, 8)]
    leaves = [compute_leaf_hash(base, 0) for base in bases]
    root = enc(compute_node_hash(*leaves))
    dummy = enc(bytes(64))
    proofs = [
        dict(
            proof_id=uid(10 + i),
            base_row_digest=base,
            duplicate_ordinal=0,
            leaf_index=i,
            tree_size=2,
            siblings=[
                dict(hash=enc(leaves[1 - i]), direction="right" if i == 0 else "left")
            ],
            preview_package_url="https://seller.example/previews/package.json",
            package_media_type="application/vnd.aim.preview+json",
            package_profile="aim-preview-package-v2",
            package_byte_ceiling=1048576,
            scan_policy="aim-preview-policy-v1",
            scan_policy_version="1.0.0",
            scan_verdict="passed",
            scanned_at=STAMP,
            sampled_leaf_list_digest=enc(bytes(32)),
            signer_reference=ref,
            signature_algorithm="ed25519",
            signature=dummy,
        )
        for i, base in enumerate(bases)
    ]
    sampled = sampled_leaf_list_digest(proofs)
    for p in proofs:
        p["sampled_leaf_list_digest"] = sampled
    c = dict(
        commitment_id=uid(3),
        listing_id=uid(4),
        seller_dataset_version="fixture-v1",
        previous_commitment_id=None,
        canonicalization_profile="aim-dataset-merkle-v1",
        hash_algorithm="sha-256",
        schema_digest=enc(schema.digest),
        dataset_merkle_root=root,
        leaf_count=2,
        seller_attestation_digest=enc(bytes([5]) * 32),
        aim_data_signer_reference=ref,
        signature_algorithm="ed25519",
        seller_signature=dummy,
        signed_at=STAMP,
        proofs=proofs,
    )
    from app.services.preview_lifecycle import capture_rights
    from app.services.preview_signing_service import seller_attestation_digest

    rights = capture_rights("Synthetic fixture rights", "owner", True)
    attestation = dict(
        listing_id=c["listing_id"],
        seller_dataset_version=c["seller_dataset_version"],
        schema_digest=c["schema_digest"],
        dataset_merkle_root=c["dataset_merkle_root"],
        leaf_count=c["leaf_count"],
        sample_hash=sample_hash(proofs),
        rights_basis_digest=rights["rights_basis_digest"],
        public_preview_permission=True,
        metadata_accuracy_confirmed=True,
        signed_at=STAMP,
    )
    c["seller_attestation_digest"] = seller_attestation_digest(attestation)
    for p in proofs:
        p["signature"] = enc(key.sign(proof_bytes(c, p)))
    c["seller_signature"] = enc(key.sign(commitment_bytes(c)))
    b = dict(
        profile="aim-preview-disclosure-v1",
        decision="approve",
        summary_id=uid(5),
        disclosure_version=uid(6),
        seller_id=uid(2),
        listing_id=uid(4),
        listing_version_id=None,
        content_revision=uid(8),
        source_revision="e" * 64,
        summary_approval_id=uid(9),
        summary_hash="a" * 64,
        render_hash="b" * 64,
        selected_fields=["amount", "count"],
        preview_type="table",
        content_type="tabular",
        sample_decision="approved",
        sample_hash=sample_hash(proofs),
        aggregate_hash="c" * 64,
        commitment_id=uid(3),
        schema_digest=enc(schema.digest),
        seller_dataset_version="fixture-v1",
        schema_descriptors=desc,
        proof_ids=[p["proof_id"] for p in proofs],
        sampled_leaf_list_digest=sampled,
        scan_attestation_digest=scan_attestation_digest(proofs),
        rights_basis_digest=rights["rights_basis_digest"],
        rights_basis_code="owner",
        public_preview_permission=True,
        approved_by=uid(2),
        approved_at=STAMP,
        last_attested_by_seller_at=STAMP,
        update_cadence_days=14,
        approval_expires_at=None,
        supersedes=None,
        request_id=uid(7),
        expected_current_disclosure_id=None,
        signer_reference=ref,
        signature_algorithm="ed25519",
        signature_profile="aim-preview-disclosure-signature-v1",
    )
    return key, c, b


def request_fixture():
    key, c, b = material()
    return dict(
        profile=b["profile"],
        summary_id=b["summary_id"],
        binding=b,
        seller_signature=enc(key.sign(disclosure_bytes(b))),
        commitment=c,
        proofs=c["proofs"],
    )


def p1(binding):
    return {
        k: binding[k]
        for k in (
            "summary_id",
            "summary_approval_id",
            "summary_hash",
            "render_hash",
            "aggregate_hash",
            "content_revision",
            "source_revision",
            "listing_id",
            "listing_version_id",
        )
    }


def all_requests():
    from app.services.preview_lifecycle import (
        withdrawal_candidate,
        refresh_candidate,
        supersession_candidate,
        GRANT_NULLS,
    )
    import copy

    r = request_fixture()
    key = test_key()
    result = {"approve": r}
    n = copy.deepcopy(r)
    b = n["binding"]
    b.update({k: None for k in GRANT_NULLS})
    b.update(
        selected_fields=[],
        schema_descriptors=[],
        proof_ids=[],
        sample_decision="none",
        disclosure_version=uid(20),
        request_id=uid(21),
    )
    n.update(
        commitment=None, proofs=[], seller_signature=enc(key.sign(disclosure_bytes(b)))
    )
    result["none"] = n
    w = withdrawal_candidate(
        r["binding"], disclosure_version=uid(30), request_id=uid(31), approved_at=STAMP
    ).binding()
    result["withdraw"] = dict(
        profile=w["profile"],
        summary_id=w["summary_id"],
        binding=w,
        seller_signature=enc(key.sign(disclosure_bytes(w))),
        commitment=None,
        proofs=[],
    )
    refreshed = refresh_candidate(
        r["binding"],
        disclosure_version=uid(32),
        request_id=uid(33),
        attested_at="2026-09-18T00:00:00.000000Z",
        cadence_days=7,
    ).binding()
    result["refresh"] = dict(
        r,
        binding=refreshed,
        seller_signature=enc(key.sign(disclosure_bytes(refreshed))),
    )
    replacement = dict(
        r["binding"],
        disclosure_version=uid(34),
        request_id=uid(35),
        selected_fields=["count"],
    )
    superseded = supersession_candidate(r["binding"], replacement).binding()
    result["supersede"] = dict(
        r,
        binding=superseded,
        seller_signature=enc(key.sign(disclosure_bytes(superseded))),
    )
    return result


def platform_material(request=None):
    from app.services.preview_signing_service import platform_envelope_bytes
    from app.services.dataset_merkle_service import (
        canonical_log_entry_bytes,
        compute_log_leaf_hash,
        checkpoint_signing_bytes,
    )

    r = request or request_fixture()
    key = test_key(32)
    seller = test_key().public_key()
    envelope = dict(
        profile="aim-preview-platform-envelope-v1",
        key_id="synthetic-platform-1",
        signature_algorithm="ed25519",
        binding=r["binding"],
        seller_signature=r["seller_signature"],
        signer_keys=[
            dict(
                key_id=uid(1),
                algorithm="ed25519",
                public_key=enc(public_bytes(seller)),
                status="active",
                valid_from="2026-09-16T00:00:00.000000Z",
                fingerprint=fingerprint(public_bytes(seller)),
            )
        ],
        signature=enc(bytes(64)),
    )
    envelope["signature"] = enc(key.sign(platform_envelope_bytes(envelope)))
    c = r["commitment"]
    entry = {k: v for k, v in c.items() if k != "proofs"}
    entry.update(appended_at=STAMP, transparency_sequence=1)
    root = enc(compute_log_leaf_hash(canonical_log_entry_bytes(entry)))
    cp = dict(
        log_id="synthetic-log-1",
        tree_size=1,
        root_hash=root,
        checkpoint_at=STAMP,
        key_id="synthetic-platform-1",
        public_key_algorithm="ed25519",
        signature=enc(bytes(64)),
    )
    cp["signature"] = enc(
        key.sign(
            checkpoint_signing_bytes(
                cp["log_id"], cp["tree_size"], cp["root_hash"], cp["checkpoint_at"]
            )
        )
    )
    log = dict(
        entry=entry,
        inclusion_path=[],
        consistency_path=[],
        previous_tree_size=None,
        previous_root=None,
    )
    return key, envelope, cp, log


def signing_corpus():
    from app.services.preview_signing_service import platform_envelope_bytes
    from app.services.dataset_merkle_service import checkpoint_signing_bytes
    import hashlib

    key, c, b = material()
    platform, env, cp, log = platform_material()
    rows = []

    def add(name, message, signature, signer):
        rows.append(
            dict(
                name=name,
                signed_bytes_hex=message.hex(),
                signed_bytes_sha256=hashlib.sha256(message).hexdigest(),
                signature=signature,
                public_key=enc(public_bytes(signer.public_key())),
            )
        )

    for i, p in enumerate(c["proofs"]):
        add("proof-" + str(i), proof_bytes(c, p), p["signature"], key)
    add("commitment", commitment_bytes(c), c["seller_signature"], key)
    for name, request in all_requests().items():
        add(
            name, disclosure_bytes(request["binding"]), request["seller_signature"], key
        )
    add("platform-envelope", platform_envelope_bytes(env), env["signature"], platform)
    add(
        "checkpoint",
        checkpoint_signing_bytes(
            cp["log_id"], cp["tree_size"], cp["root_hash"], cp["checkpoint_at"]
        ),
        cp["signature"],
        platform,
    )
    return dict(
        fixture_notice="SYNTHETIC TEST KEYS ONLY. Producer-local candidates, not platform allocations.",
        signatures=rows,
        platform_envelope=env,
        checkpoint=cp,
        log_evidence=log,
    )
