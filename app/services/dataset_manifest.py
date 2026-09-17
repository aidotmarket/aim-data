"""D0 manifest preimage: ordered seven-field tuples, never derived scalars.

The S1294 serializer has a restricted JCS vocabulary. Reuse the signed publish
serializer, which implements python-json-sort-compact-v1 without that restriction.
"""
from __future__ import annotations

import hashlib
import re
import unicodedata
from collections.abc import Mapping

from app.services.marketplace_action_signer import canonical_json_bytes

MEMBER_FIELDS = ("index", "relative_path", "size_bytes", "sha256", "detected_type", "role", "is_sample")
ROLES = ("data", "documentation", "other")
STATUSES = ("current", "removed", "missing", "unsupported")


def canonical_path(path: str) -> str:
    try:
        path.encode("utf-8", errors="strict")
    except UnicodeError:
        raise ValueError(f"Non-UTF-8 relative_path: {ascii(path)}") from None
    normalized = unicodedata.normalize("NFC", path)
    if (not normalized or "\\" in normalized or normalized.startswith("/")
            or any(p in ("", ".", "..") for p in normalized.split("/"))
            or any(unicodedata.category(c) == "Cc" for c in normalized)):
        raise ValueError(f"Invalid relative_path: {ascii(path)}")
    if len(normalized.encode("utf-8")) > 1024:
        raise ValueError(f"relative_path exceeds 1024 UTF-8 bytes: {ascii(path)}")
    return normalized


def member_tuple(member) -> dict:
    result = {key: member[key] if isinstance(member, Mapping) else getattr(member, key) for key in MEMBER_FIELDS}
    if canonical_path(result["relative_path"]) != result["relative_path"]:
        raise ValueError("relative_path must be NFC")
    if type(result["index"]) is not int or result["index"] < 0:
        raise ValueError("Invalid member index")
    if type(result["size_bytes"]) is not int or result["size_bytes"] < 0:
        raise ValueError("Invalid member size")
    if not isinstance(result["sha256"], str) or not re.fullmatch("[0-9a-f]{64}", result["sha256"]):
        raise ValueError("Missing or invalid member sha256")
    from app.services.processing_service import PROCESSABLE_TYPES
    if result["detected_type"] not in PROCESSABLE_TYPES | {"unsupported"}:
        raise ValueError("Invalid detected_type")
    if result["role"] not in ROLES or type(result["is_sample"]) is not bool:
        raise ValueError("Invalid role or is_sample")
    if result["is_sample"] and result["role"] != "data":
        raise ValueError("is_sample requires role=data")
    return result


def build_manifest(members) -> dict:
    """Caller supplies manifest order; never reorder or hash row bookkeeping."""
    rows = [member_tuple(member) for member in members]
    if len({r["index"] for r in rows}) != len(rows):
        raise ValueError("Duplicate member index")
    if len({r["relative_path"].casefold() for r in rows}) != len(rows):
        raise ValueError("Duplicate member path")
    return {
        "members": rows,
        "member_count": len(rows),
        "data_member_count": sum(r["role"] == "data" for r in rows),
        "sample_member_count": sum(r["is_sample"] for r in rows),
        "total_data_bytes": sum(r["size_bytes"] for r in rows if r["role"] == "data"),
        "manifest_hash": hashlib.sha256(canonical_json_bytes(rows)).hexdigest(),
    }
