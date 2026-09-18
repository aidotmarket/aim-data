"""Seller-attested preview policy and cryptographic attestation helpers.

Preview publication deliberately makes no automated judgement about cell content.
Only structural safety limits and the seller's explicit confirmations are checked.
"""

import hashlib
import re

from app.services.dataset_merkle_service import (
    canonical_json_bytes,
    canonical_rfc3339_utc,
    compute_leaf_hash,
    encode_base64url,
)

POLICY = "aim-preview-policy-v2"
VERSION = "2.0.0"
ACCEPTED_POLICY_IDENTITIES = {
    ("aim-preview-policy-v1", "1.0.0"),
    (POLICY, VERSION),
}


def detector_identity():
    """Compatibility hook: detector packages are not part of preview admission."""
    return {}


class PolicyError(ValueError):
    """Fixed codes only; never attach detector exceptions or input values."""


class NumericText(str):
    """Descriptor-proven numeric logical value, retaining exact decimal digits."""


def walk_selection(rows):
    """Validate bounded JSON-compatible rows without judging their content."""

    nodes = 0

    def walk(value, depth):
        nonlocal nodes
        nodes += 1
        if nodes > 10000:
            raise PolicyError("nodes_limit")
        if depth > 16:
            raise PolicyError("depth_limit")
        if isinstance(value, dict):
            for key, child in value.items():
                if not isinstance(key, str):
                    raise PolicyError("invalid_row")
                yield key, False
                yield from walk(child, depth + 1)
        elif isinstance(value, list):
            for child in value:
                yield from walk(child, depth + 1)
        elif isinstance(value, str):
            yield str(value), isinstance(value, NumericText)
        elif type(value) in (int, float):
            if type(value) is float and not __import__("math").isfinite(value):
                raise PolicyError("invalid_row")
            yield str(value), True
        elif value is not None and type(value) is not bool:
            raise PolicyError("unsupported_value")

    if not isinstance(rows, (list, tuple)) or not 1 <= len(rows) <= 100:
        raise PolicyError("invalid_selection")
    for row in rows:
        if not isinstance(row, dict):
            raise PolicyError("invalid_row")
        yield from walk(row, 0)


def check_text(text, numeric=False):
    """Compatibility no-op: cells are rendered as inert text by the viewer."""
    if not isinstance(text, str):
        raise PolicyError("invalid_row")


def sampled_leaf_list_digest(proofs):
    leaves = [
        encode_base64url(
            compute_leaf_hash(p["base_row_digest"], p["duplicate_ordinal"])
        )
        for p in proofs
    ]
    return encode_base64url(
        hashlib.sha256(
            b"aim-preview-sampled-leaves-v1\0" + canonical_json_bytes(leaves)
        ).digest()
    )


def scan_attestation_digest(signed_proof_records):
    """Called AFTER Chunk 2c signs closed proof records; never signs here."""
    # No generic row-bearing carrier may enter this digest seam.
    from app.services.dataset_merkle_service import decode_base64url

    allowed = {
        "proof_id",
        "base_row_digest",
        "duplicate_ordinal",
        "leaf_index",
        "tree_size",
        "siblings",
        "preview_package_url",
        "package_media_type",
        "package_profile",
        "package_byte_ceiling",
        "scan_policy",
        "scan_policy_version",
        "scan_verdict",
        "scanned_at",
        "sampled_leaf_list_digest",
        "signer_reference",
        "signature_algorithm",
        "signature",
    }
    shared_fields = (
        "preview_package_url",
        "package_media_type",
        "package_profile",
        "package_byte_ceiling",
        "scan_policy",
        "scan_policy_version",
        "scan_verdict",
        "scanned_at",
        "sampled_leaf_list_digest",
        "signer_reference",
        "signature_algorithm",
    )
    if not signed_proof_records:
        raise PolicyError("unsigned_proofs")
    try:
        for proof in signed_proof_records:
            if set(proof) != allowed or len(decode_base64url(proof["signature"])) != 64:
                raise PolicyError("unsigned_proofs")
            from app.models.dataset_commitment_schemas import CommitmentProof
            from app.services.preview_package_service import (
                canonical_uuid,
                MEDIA_TYPE,
                PROFILE,
            )
            from app.services.preview_origin_service import validate_url

            canonical_uuid(proof["proof_id"])
            CommitmentProof.model_validate(
                {
                    k: proof[k]
                    for k in (
                        "base_row_digest",
                        "duplicate_ordinal",
                        "leaf_index",
                        "tree_size",
                        "siblings",
                    )
                }
            )
            validate_url(proof["preview_package_url"])
            if (
                proof["package_media_type"] != MEDIA_TYPE
                or proof["package_profile"] != PROFILE
                or type(proof["package_byte_ceiling"]) is not int
                or not 0 < proof["package_byte_ceiling"] <= 1048576
                or (
                    proof["scan_policy"],
                    proof["scan_policy_version"],
                )
                not in ACCEPTED_POLICY_IDENTITIES
                or proof["scan_verdict"] != "passed"
                or proof["signature_algorithm"] != "ed25519"
                or canonical_rfc3339_utc(proof["scanned_at"]) != proof["scanned_at"]
                or not re.fullmatch(
                    r"[0-9a-f-]{36}:[0-9a-f]{64}", proof["signer_reference"]
                )
            ):
                raise PolicyError("unsigned_proofs")
            canonical_uuid(proof["signer_reference"][:36])
        expected = tuple(signed_proof_records[0][key] for key in shared_fields)
        if any(
            tuple(proof[key] for key in shared_fields) != expected
            for proof in signed_proof_records[1:]
        ):
            raise PolicyError("unsigned_proofs")
        digest = sampled_leaf_list_digest(signed_proof_records)
        if any(p["sampled_leaf_list_digest"] != digest for p in signed_proof_records):
            raise PolicyError("unsigned_proofs")
        return hashlib.sha256(
            b"aim-preview-scan-attestation-v1\0"
            + canonical_json_bytes(signed_proof_records)
        ).hexdigest()
    except (ValueError, TypeError, KeyError):
        raise PolicyError("unsigned_proofs") from None


def scan_selection(
    rows,
    proofs,
    *,
    detector=None,
    scanned_at,
    rights_confirmed,
    public_preview_permission=False,
    restricted_content_confirmed=False,
    language="en",
):
    if (
        rights_confirmed is not True
        or public_preview_permission is not True
        or restricted_content_confirmed is not True
        or len(rows) != len(proofs)
    ):
        raise PolicyError("approval_required")
    # Consume the complete selection solely to enforce technical shape limits.
    list(walk_selection(rows))
    return {
        "scan_policy": POLICY,
        "scan_policy_version": VERSION,
        "scan_verdict": "passed",
        "scanned_at": canonical_rfc3339_utc(scanned_at),
        "sampled_leaf_list_digest": sampled_leaf_list_digest(proofs),
    }
