"""Frozen seller-local aim-preview-policy-v1 / 1.0.0 predicates.

No network, diagnostic logging, matched values or permissive detector fallback.
Signing belongs to Chunk 2c; the attestation digest consumes its signed records.
"""

import hashlib
import math
import re
import unicodedata
from collections import Counter

from app.services.dataset_merkle_service import (
    canonical_json_bytes,
    canonical_rfc3339_utc,
    compute_leaf_hash,
    encode_base64url,
)

POLICY = "aim-preview-policy-v1"
VERSION = "1.0.0"
DETECTOR_IDENTITY = {
    "presidio-analyzer": "2.2.362",
    "spacy": "3.7.2",
    "en-core-web-sm": "3.7.1",
}


def detector_identity():
    """Local evidence only. Unknown detector/model versions cannot attest v1."""
    from importlib.metadata import version

    try:
        actual = {name: version(name) for name in DETECTOR_IDENTITY}
        if actual != DETECTOR_IDENTITY:
            raise ValueError
        return actual
    except Exception:
        raise PolicyError("detector_unavailable") from None


# Predicates are versioned protocol choices, not an assurance of legal clearance.
RULES = {
    "secret": r"(?i)(-----BEGIN [A-Z ]*PRIVATE KEY-----|\b(?:AKIA|ASIA)[A-Z0-9]{16}\b|\b(?:gh[pousr]_|github_pat_|sk_live_|sk_test_|sk-(?:proj-)?|xox[baprs]-)|\b(?:password|passwd|secret|api[_-]?key|access[_-]?token|authorization)\s*[:=]|\bBearer\s+\S+|\beyJ[A-Za-z0-9_-]+\.[A-Za-z0-9_-]+\.[A-Za-z0-9_-]+)",
    "personal_data": r"(?i)([\w.+-]+@[\w.-]+\.[a-z]{2,}|\b\d{3}[- ]?\d{2}[- ]?\d{4}\b|\b(?:\d[ -]?){10,19}\b|\b[A-Z]{2}\d{2}[A-Z0-9]{11,30}\b|\b(?:\d{1,3}\.){3}\d{1,3}\b)",
    "executable": r"(?i)(<\s*/?\s*[a-z][^>]*>|\bon[a-z]+\s*=|\b(?:javascript|vbscript|data)\s*:|\b(?:Sub\s+Auto_Open|AutoOpen|Workbook_Open|CreateObject|Shell\s*\())",
    "url": r"(?i)(\b[a-z][a-z0-9+.-]*:|\bwww\.|\b(?:mailto|tel|file|javascript|data):|\]\s*\(|(?:^|\s)//[a-z0-9])",
    "restricted_content": r"(?i)(\bcopyright\b|©|all rights reserved|licensed under|not for redistribution|reproduced (?:from|with)|excerpt (?:from|of)|attribution required)",
}
COMPILED = {code: re.compile(pattern) for code, pattern in RULES.items()}
TOKEN = re.compile(r"[A-Za-z0-9_-]{24,}")


class PolicyError(ValueError):
    """Fixed codes only; never attach detector exceptions or input values."""


class NumericText(str):
    """Descriptor-proven numeric logical value, retaining exact decimal digits."""


def walk_selection(rows):
    """Yield all keys and scalars with numeric identity retained for formula rules."""

    def walk(value, depth):
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
            if not math.isfinite(value):
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
    if len(text) > 500 or len(text.split()) > 80:
        raise PolicyError("long_prose")
    if any(unicodedata.category(c) in {"Cc", "Cf", "Cs"} for c in text):
        raise PolicyError("control_character")
    if not numeric and text.lstrip().startswith(("=", "+", "-", "@")):
        raise PolicyError("formula")
    for code, pattern in COMPILED.items():
        if pattern.search(text):
            raise PolicyError(code)
    for candidate in TOKEN.findall(text):
        entropy = -sum(
            (n / len(candidate)) * math.log2(n / len(candidate))
            for n in Counter(candidate).values()
        )
        if entropy >= 4.0:
            raise PolicyError("high_entropy")


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
                or proof["scan_policy"] != POLICY
                or proof["scan_policy_version"] != VERSION
                or proof["scan_verdict"] != "passed"
                or proof["signature_algorithm"] != "ed25519"
                or canonical_rfc3339_utc(proof["scanned_at"]) != proof["scanned_at"]
                or not re.fullmatch(
                    r"[0-9a-f-]{36}:[0-9a-f]{64}", proof["signer_reference"]
                )
            ):
                raise PolicyError("unsigned_proofs")
            canonical_uuid(proof["signer_reference"][:36])
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
    detector,
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
    try:
        from app.services.pii_service import PIIService

        if not isinstance(detector, PIIService) or type(detector) is not PIIService:
            raise PolicyError("detector_unavailable")
        # Complete detector pass precedes deterministic predicates, even for long text.
        detector.scan_complete_selection(rows, language=language)
    except PolicyError:
        raise
    except Exception:
        raise PolicyError("detector_unavailable") from None
    for text, numeric in walk_selection(rows):
        check_text(text, numeric)
    return {
        "scan_policy": POLICY,
        "scan_policy_version": VERSION,
        "scan_verdict": "passed",
        "scanned_at": canonical_rfc3339_utc(scanned_at),
        "sampled_leaf_list_digest": sampled_leaf_list_digest(proofs),
    }
