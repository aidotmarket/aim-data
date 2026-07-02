from __future__ import annotations

import re


_VERSION_LABEL_RE = re.compile(r"^[A-Za-z0-9._-]{1,64}$")
_CONTROL_OR_WHITESPACE_RE = re.compile(r"[\x00-\x20\x7f\s]")


def build_version_prefix(parent_prefix: str, version_label: str) -> str:
    """Canonical version prefix join. Pure function shared by publish/delivery."""
    if not isinstance(parent_prefix, str) or _CONTROL_OR_WHITESPACE_RE.search(parent_prefix):
        raise ValueError("parent prefix contains whitespace or control characters")
    canonical_parent = re.sub(r"/+", "/", parent_prefix).strip("/")
    if not canonical_parent:
        raise ValueError("parent prefix must not be empty or root")
    if not isinstance(version_label, str) or not _VERSION_LABEL_RE.fullmatch(version_label):
        raise ValueError("version_label must match ^[A-Za-z0-9._-]{1,64}$")
    if version_label in {".", ".."}:
        raise ValueError("version_label must not be . or ..")

    prefix = f"{canonical_parent}/{version_label}/"
    if prefix == f"{canonical_parent}/" or not prefix.startswith(f"{canonical_parent}/"):
        raise ValueError("version prefix is not a strict child of parent prefix")
    return prefix
