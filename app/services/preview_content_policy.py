"""Fail-closed local content policy for seller-selected preview rows."""
from __future__ import annotations

import math
import re
from dataclasses import dataclass
from datetime import datetime, timezone
from typing import Any, Iterable, Mapping


POLICY_PROFILE = "aim-preview-policy-v1"
POLICY_VERSION = "1.0.0"


class PreviewPolicyError(ValueError):
    """A stable policy failure that never includes row content."""

    def __init__(self, code: str, *, row_index: int | None = None) -> None:
        super().__init__(code)
        self.code = code
        self.row_index = row_index


@dataclass(frozen=True)
class PreviewRightsBasis:
    basis: str
    public_preview_permitted: bool
    copyright_status: str
    license_conflict_resolved: bool = False


@dataclass(frozen=True)
class PreviewPolicyResult:
    policy: str
    policy_version: str
    verdict: str
    scanned_at: datetime
    row_count: int


_EMAIL = re.compile(r"(?i)\b[A-Z0-9._%+-]+@[A-Z0-9.-]+\.[A-Z]{2,}\b")
_SSN = re.compile(r"\b\d{3}-\d{2}-\d{4}\b")
_CREDIT_CARD = re.compile(r"\b(?:\d[ -]*?){13,19}\b")
_PHONE = re.compile(r"(?<!\w)(?:\+?\d[\d .()\-]{7,}\d)(?!\w)")
_PRIVATE_KEY = re.compile(r"-----BEGIN (?:RSA |EC |OPENSSH |DSA )?PRIVATE KEY-----")
_AWS_KEY = re.compile(r"\b(?:AKIA|ASIA)[A-Z0-9]{16}\b")
_JWT = re.compile(r"\beyJ[A-Za-z0-9_-]{8,}\.[A-Za-z0-9_-]{8,}\.[A-Za-z0-9_-]{8,}\b")
_TOKEN = re.compile(r"(?i)\b(?:api[_-]?key|secret|token|password|authorization)\s*[:=]\s*['\"]?[A-Za-z0-9_./+\-=]{8,}")
_CONNECTION = re.compile(r"(?i)\b(?:postgres(?:ql)?|mysql|mongodb(?:\+srv)?|redis|amqp|mssql)://[^\s]+")
_HTML = re.compile(r"(?is)<\s*(?:script|iframe|object|embed|svg|math|style|link|meta|form)\b|on\w+\s*=")
_ACTIVE_URL = re.compile(r"(?i)(?:https?|ftp|file|javascript|data):(?:/{0,2})[^\s]+|\bwww\.[^\s]+")
_MACRO = re.compile(r"(?is)\b(?:auto_open|workbook_open|document_open|ddeauto)\b|\bsub\s+\w+\s*\(.*?end\s+sub\b")
_PROHIBITED = re.compile(r"(?i)\b(?:child sexual abuse material|csam)\b")


def _walk_scalars(value: Any) -> Iterable[str]:
    if isinstance(value, str):
        yield value
    elif isinstance(value, Mapping):
        for key, child in value.items():
            yield str(key)
            yield from _walk_scalars(child)
    elif isinstance(value, (list, tuple)):
        for child in value:
            yield from _walk_scalars(child)
    elif isinstance(value, float) and not math.isfinite(value):
        raise PreviewPolicyError("non_finite_value")


def _looks_high_entropy_secret(value: str) -> bool:
    compact = value.strip()
    if len(compact) < 32 or len(compact) > 512 or not re.fullmatch(r"[A-Za-z0-9_./+\-=]+", compact):
        return False
    categories = sum(
        bool(pattern.search(compact))
        for pattern in (re.compile(r"[a-z]"), re.compile(r"[A-Z]"), re.compile(r"\d"), re.compile(r"[_./+\-=]"))
    )
    if categories < 3:
        return False
    frequencies = {char: compact.count(char) / len(compact) for char in set(compact)}
    entropy = -sum(frequency * math.log2(frequency) for frequency in frequencies.values())
    return entropy >= 4.0


class PreviewContentPolicy:
    def __init__(self, *, pii_service: Any | None = None, require_presidio: bool = True) -> None:
        self._pii_service = pii_service
        self.require_presidio = require_presidio

    def _pii_matches(self, value: str) -> bool:
        if _EMAIL.search(value) or _SSN.search(value) or _CREDIT_CARD.search(value) or _PHONE.search(value):
            return True
        service = self._pii_service
        if service is None and self.require_presidio:
            try:
                from app.services.pii_service import get_pii_service

                service = get_pii_service()
                self._pii_service = service
            except Exception as exc:
                raise PreviewPolicyError("pii_scanner_unavailable") from exc
        if service is None:
            return False
        try:
            return bool(service.scan_text(value))
        except PreviewPolicyError:
            raise
        except Exception as exc:
            raise PreviewPolicyError("pii_scanner_unavailable") from exc

    def scan_rows(
        self,
        rows: Iterable[Mapping[str, Any]],
        *,
        rights_basis: PreviewRightsBasis,
        now: datetime | None = None,
    ) -> PreviewPolicyResult:
        if not rights_basis.basis.strip():
            raise PreviewPolicyError("rights_basis_missing")
        if not rights_basis.public_preview_permitted:
            raise PreviewPolicyError("public_preview_permission_missing")
        if rights_basis.copyright_status not in {"seller_owned", "licensed", "public_domain"}:
            raise PreviewPolicyError("copyright_status_uncertain")
        row_count = 0
        for row_index, row in enumerate(rows):
            row_count += 1
            if not isinstance(row, Mapping):
                raise PreviewPolicyError("row_not_object", row_index=row_index)
            for value in _walk_scalars(row):
                if any(ord(char) < 0x20 and char not in "\t\n\r" for char in value) or "\x7f" in value:
                    raise PreviewPolicyError("control_character_detected", row_index=row_index)
                if _HTML.search(value):
                    raise PreviewPolicyError("executable_markup_detected", row_index=row_index)
                if _MACRO.search(value):
                    raise PreviewPolicyError("macro_detected", row_index=row_index)
                if _ACTIVE_URL.search(value):
                    raise PreviewPolicyError("active_url_detected", row_index=row_index)
                if _PRIVATE_KEY.search(value):
                    raise PreviewPolicyError("private_key_detected", row_index=row_index)
                if _AWS_KEY.search(value) or _JWT.search(value) or _TOKEN.search(value):
                    raise PreviewPolicyError("credential_detected", row_index=row_index)
                if _CONNECTION.search(value):
                    raise PreviewPolicyError("connection_string_detected", row_index=row_index)
                stripped = value.lstrip()
                if stripped.startswith(("=", "+", "-", "@")):
                    raise PreviewPolicyError("spreadsheet_formula_detected", row_index=row_index)
                if _looks_high_entropy_secret(value):
                    raise PreviewPolicyError("high_entropy_secret_detected", row_index=row_index)
                if _PROHIBITED.search(value):
                    raise PreviewPolicyError("prohibited_content_detected", row_index=row_index)
                if self._pii_matches(value):
                    raise PreviewPolicyError("personal_data_detected", row_index=row_index)
                if len(value.encode("utf-8")) > 1_000 and rights_basis.copyright_status != "public_domain":
                    raise PreviewPolicyError("copyright_excerpt_uncertain", row_index=row_index)
        if row_count == 0:
            raise PreviewPolicyError("empty_preview_selection")
        if rights_basis.copyright_status == "licensed" and not rights_basis.license_conflict_resolved:
            raise PreviewPolicyError("license_conflict_unresolved")
        scanned_at = (now or datetime.now(timezone.utc)).astimezone(timezone.utc)
        return PreviewPolicyResult(POLICY_PROFILE, POLICY_VERSION, "passed", scanned_at, row_count)
