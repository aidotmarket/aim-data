"""Local ``aim-dataset-merkle-v1`` parsing and canonicalization.

This module is intentionally row-aware: it runs only inside the customer AIM
Data installation.  Callers must never serialize ``CanonicalRecord`` objects
into an ai.market request.
"""
from __future__ import annotations

import base64
import csv
import hashlib
import json
import re
import unicodedata
from dataclasses import dataclass, field
from datetime import date, datetime, timezone
from decimal import Decimal, InvalidOperation
from pathlib import Path
from typing import Any, Iterator, Literal, Mapping, Sequence

import pyarrow.parquet as pq
import pyarrow as pa


DATASET_PROFILE = "aim-dataset-merkle-v1"
LOGICAL_TYPES = frozenset(
    {
        "missing",
        "null",
        "boolean",
        "signed_integer",
        "decimal",
        "string",
        "date",
        "timestamp",
        "binary",
        "array",
        "object",
    }
)


class DatasetCanonicalizationError(ValueError):
    """A stable, non-content canonicalization failure."""

    def __init__(self, code: str) -> None:
        super().__init__(code)
        self.code = code


def encode_base64url(value: bytes) -> str:
    return base64.urlsafe_b64encode(value).rstrip(b"=").decode("ascii")


def decode_base64url(value: str, *, expected_size: int | None = None) -> bytes:
    if not isinstance(value, str) or not value or "=" in value:
        raise DatasetCanonicalizationError("invalid_base64url")
    try:
        raw = value.encode("ascii")
        decoded = base64.b64decode(raw + b"=" * ((4 - len(raw) % 4) % 4), altchars=b"-_", validate=True)
    except (UnicodeEncodeError, ValueError) as exc:
        raise DatasetCanonicalizationError("invalid_base64url") from exc
    if encode_base64url(decoded) != value or (expected_size is not None and len(decoded) != expected_size):
        raise DatasetCanonicalizationError("invalid_base64url")
    return decoded


def _validate_unicode(value: str) -> str:
    try:
        value.encode("utf-8")
    except UnicodeEncodeError as exc:
        raise DatasetCanonicalizationError("invalid_unicode") from exc
    return unicodedata.normalize("NFC", value)


def _utf16_sort_key(value: str) -> bytes:
    """JCS object-key ordering: lexicographic UTF-16 code units (M3)."""
    return value.encode("utf-16-be")


def jcs_canonical_bytes(value: Any) -> bytes:
    """RFC-8785 bytes for the integer-only JSON subset used by this profile.

    Python's ``sort_keys=True`` orders Unicode code points, which differs from
    JCS for some supplementary-plane keys.  Object keys are therefore sorted
    explicitly by UTF-16 code units.  Floats are rejected to avoid cross-runtime
    number-rendering ambiguity.
    """

    def render(item: Any) -> str:
        if item is None:
            return "null"
        if item is True:
            return "true"
        if item is False:
            return "false"
        if isinstance(item, int) and not isinstance(item, bool):
            return str(item)
        if isinstance(item, float):
            raise DatasetCanonicalizationError("floating_point_forbidden")
        if isinstance(item, str):
            normalized = _validate_unicode(item)
            return json.dumps(normalized, ensure_ascii=False, separators=(",", ":"))
        if isinstance(item, (list, tuple)):
            return "[" + ",".join(render(child) for child in item) + "]"
        if isinstance(item, Mapping):
            normalized_items: list[tuple[str, Any]] = []
            seen: set[str] = set()
            for key, child in item.items():
                if not isinstance(key, str):
                    raise DatasetCanonicalizationError("non_string_object_key")
                normalized = _validate_unicode(key)
                if normalized in seen:
                    raise DatasetCanonicalizationError("duplicate_object_key")
                seen.add(normalized)
                normalized_items.append((normalized, child))
            normalized_items.sort(key=lambda pair: _utf16_sort_key(pair[0]))
            return "{" + ",".join(f"{render(key)}:{render(child)}" for key, child in normalized_items) + "}"
        raise DatasetCanonicalizationError("unsupported_json_type")

    return render(value).encode("utf-8")


@dataclass(frozen=True)
class LogicalField:
    name: str
    type: str
    nullable: bool
    type_parameters: Mapping[str, Any] = field(default_factory=dict)


@dataclass(frozen=True)
class CsvParseOptions:
    encoding: str
    delimiter: str
    quotechar: str
    header: bool
    locale: str
    null_token: str

    def validate(self) -> None:
        if not self.encoding or not self.locale:
            raise DatasetCanonicalizationError("csv_options_required")
        if len(self.delimiter) != 1 or len(self.quotechar) != 1 or self.delimiter == self.quotechar:
            raise DatasetCanonicalizationError("invalid_csv_dialect")


@dataclass(frozen=True)
class CanonicalRecord:
    canonical_row: list[list[Any]]
    canonical_row_bytes: bytes
    base_row_digest: bytes
    row: Mapping[str, Any]
    source_index: int | None = None


def _field_from_value(value: LogicalField | Mapping[str, Any]) -> LogicalField:
    if isinstance(value, LogicalField):
        field_value = value
    elif isinstance(value, Mapping):
        try:
            field_value = LogicalField(
                name=value["name"],
                type=value["type"],
                nullable=value["nullable"],
                type_parameters=value.get("type_parameters") or {},
            )
        except (KeyError, TypeError) as exc:
            raise DatasetCanonicalizationError("invalid_schema_descriptor") from exc
    else:
        raise DatasetCanonicalizationError("invalid_schema_descriptor")
    if not isinstance(field_value.name, str) or not field_value.name:
        raise DatasetCanonicalizationError("invalid_field_name")
    name = _validate_unicode(field_value.name)
    if name != field_value.name:
        field_value = LogicalField(name, field_value.type, field_value.nullable, field_value.type_parameters)
    if field_value.type not in LOGICAL_TYPES or field_value.type in {"missing", "null"}:
        raise DatasetCanonicalizationError("unsupported_logical_type")
    if not isinstance(field_value.nullable, bool) or not isinstance(field_value.type_parameters, Mapping):
        raise DatasetCanonicalizationError("invalid_schema_descriptor")
    _validate_type_parameters(field_value.type, field_value.type_parameters)
    return field_value


def normalize_schema(schema: Sequence[LogicalField | Mapping[str, Any]]) -> list[LogicalField]:
    if not schema:
        raise DatasetCanonicalizationError("empty_schema")
    fields = [_field_from_value(value) for value in schema]
    names = [item.name for item in fields]
    if len(set(names)) != len(names):
        raise DatasetCanonicalizationError("duplicate_field_name")
    return sorted(fields, key=lambda item: item.name.encode("utf-8"))


def _nested_descriptor(value: Any) -> tuple[str, Mapping[str, Any]]:
    if not isinstance(value, Mapping) or value.get("type") not in LOGICAL_TYPES:
        raise DatasetCanonicalizationError("invalid_nested_type")
    return str(value["type"]), value.get("type_parameters") or {}


def _validate_type_parameters(logical_type: str, params: Mapping[str, Any]) -> None:
    allowed: set[str]
    if logical_type == "decimal":
        allowed = {"precision", "scale"}
        precision, scale = params.get("precision"), params.get("scale")
        if not isinstance(precision, int) or isinstance(precision, bool) or precision < 1:
            raise DatasetCanonicalizationError("decimal_parameters_required")
        if not isinstance(scale, int) or isinstance(scale, bool) or scale < 0 or scale > precision:
            raise DatasetCanonicalizationError("decimal_parameters_required")
    elif logical_type == "timestamp":
        allowed = {"timestamp_precision"}
        precision = params.get("timestamp_precision")
        if not isinstance(precision, int) or isinstance(precision, bool) or not 0 <= precision <= 9:
            raise DatasetCanonicalizationError("timestamp_precision_required")
    elif logical_type == "array":
        allowed = {"element_type"}
        nested_type, nested_params = _nested_descriptor(params.get("element_type"))
        _validate_type_parameters(nested_type, nested_params)
    elif logical_type == "object":
        allowed = {"object_fields"}
        fields = params.get("object_fields")
        if not isinstance(fields, list):
            raise DatasetCanonicalizationError("object_fields_required")
        normalize_schema(fields)
    else:
        allowed = set()
    if set(params) != allowed:
        raise DatasetCanonicalizationError("unexpected_type_parameter")


def canonical_schema(schema: Sequence[LogicalField | Mapping[str, Any]]) -> list[list[Any]]:
    return [[item.name, item.type, item.nullable, dict(item.type_parameters)] for item in normalize_schema(schema)]


def canonical_schema_bytes(schema: Sequence[LogicalField | Mapping[str, Any]]) -> bytes:
    return jcs_canonical_bytes(canonical_schema(schema))


def compute_schema_digest(schema: Sequence[LogicalField | Mapping[str, Any]]) -> bytes:
    return hashlib.sha256(b"aim-schema-v1\0" + canonical_schema_bytes(schema)).digest()


def _canonical_decimal(value: Any, params: Mapping[str, Any]) -> str:
    if isinstance(value, float) or isinstance(value, bool):
        raise DatasetCanonicalizationError("lossy_decimal_coercion")
    try:
        decimal_value = value if isinstance(value, Decimal) else Decimal(str(value))
    except (InvalidOperation, ValueError) as exc:
        raise DatasetCanonicalizationError("invalid_decimal") from exc
    if not decimal_value.is_finite():
        raise DatasetCanonicalizationError("non_finite_number")
    precision, scale = int(params["precision"]), int(params["scale"])
    normalized_decimal = decimal_value.normalize() if decimal_value else Decimal(0)
    exponent = normalized_decimal.as_tuple().exponent
    fractional_digits = max(0, -exponent)
    if fractional_digits > scale:
        raise DatasetCanonicalizationError("decimal_scale_exceeded")
    integer_digits = max(0, normalized_decimal.adjusted() + 1) if normalized_decimal else 1
    if integer_digits > precision - scale or integer_digits + fractional_digits > precision:
        raise DatasetCanonicalizationError("decimal_precision_exceeded")
    rendered = format(decimal_value, "f")
    if "." in rendered:
        rendered = rendered.rstrip("0").rstrip(".")
    if rendered in {"-0", ""}:
        rendered = "0"
    return rendered


_TIMESTAMP_PATTERN = re.compile(
    r"\A(\d{4}-\d{2}-\d{2}T\d{2}:\d{2}:\d{2})(?:\.(\d{1,9}))?(Z|[+-]\d{2}:\d{2})\Z"
)


def _canonical_timestamp(value: Any, precision: int) -> str:
    if isinstance(value, str):
        match = _TIMESTAMP_PATTERN.fullmatch(value)
        if match is None:
            raise DatasetCanonicalizationError("ambiguous_local_timestamp")
        base, fraction, offset = match.groups()
        fraction = fraction or ""
        nanoseconds = fraction.ljust(9, "0")
        if any(char != "0" for char in nanoseconds[precision:]):
            raise DatasetCanonicalizationError("timestamp_precision_loss")
        parse_value = f"{base}.{nanoseconds[:6]}{'+00:00' if offset == 'Z' else offset}"
        try:
            parsed = datetime.fromisoformat(parse_value)
        except ValueError as exc:
            raise DatasetCanonicalizationError("invalid_timestamp") from exc
    elif isinstance(value, datetime):
        parsed = value
        nanoseconds = f"{value.microsecond:06d}{int(getattr(value, 'nanosecond', 0)):03d}"
    else:
        raise DatasetCanonicalizationError("invalid_timestamp")
    if parsed.tzinfo is None or parsed.utcoffset() is None:
        raise DatasetCanonicalizationError("ambiguous_local_timestamp")
    parsed = parsed.astimezone(timezone.utc)
    if not isinstance(value, str):
        if any(char != "0" for char in nanoseconds[precision:]):
            raise DatasetCanonicalizationError("timestamp_precision_loss")
    base = parsed.strftime("%Y-%m-%dT%H:%M:%S")
    return base + (f".{nanoseconds[:precision]}" if precision else "") + "Z"


def _canonical_nested(logical_type: str, value: Any, params: Mapping[str, Any]) -> tuple[str, Any]:
    if value is None:
        return "null", None
    if logical_type == "boolean":
        if not isinstance(value, bool):
            if isinstance(value, str) and value.lower() in {"true", "false"}:
                return "boolean", value.lower() == "true"
            raise DatasetCanonicalizationError("invalid_boolean")
        return "boolean", value
    if logical_type == "signed_integer":
        if isinstance(value, bool):
            raise DatasetCanonicalizationError("invalid_signed_integer")
        if isinstance(value, int):
            return logical_type, str(value)
        if isinstance(value, str) and value and (value == "0" or value.lstrip("-").isdigit()):
            try:
                return logical_type, str(int(value, 10))
            except ValueError as exc:
                raise DatasetCanonicalizationError("invalid_signed_integer") from exc
        raise DatasetCanonicalizationError("invalid_signed_integer")
    if logical_type == "decimal":
        return logical_type, _canonical_decimal(value, params)
    if logical_type == "string":
        if not isinstance(value, str):
            raise DatasetCanonicalizationError("lossy_string_coercion")
        return logical_type, _validate_unicode(value)
    if logical_type == "date":
        if isinstance(value, datetime):
            raise DatasetCanonicalizationError("lossy_date_coercion")
        if isinstance(value, date):
            return logical_type, value.isoformat()
        if isinstance(value, str):
            try:
                return logical_type, date.fromisoformat(value).isoformat()
            except ValueError as exc:
                raise DatasetCanonicalizationError("invalid_date") from exc
        raise DatasetCanonicalizationError("invalid_date")
    if logical_type == "timestamp":
        return logical_type, _canonical_timestamp(value, int(params["timestamp_precision"]))
    if logical_type == "binary":
        if isinstance(value, (bytes, bytearray, memoryview)):
            return logical_type, encode_base64url(bytes(value))
        if isinstance(value, str):
            return logical_type, encode_base64url(decode_base64url(value))
        raise DatasetCanonicalizationError("invalid_binary")
    if logical_type == "array":
        if not isinstance(value, (list, tuple)):
            raise DatasetCanonicalizationError("invalid_array")
        nested_type, nested_params = _nested_descriptor(params["element_type"])
        return logical_type, [list(_canonical_nested(nested_type, child, nested_params)) for child in value]
    if logical_type == "object":
        if not isinstance(value, Mapping):
            raise DatasetCanonicalizationError("invalid_object")
        nested_fields = normalize_schema(params["object_fields"])
        normalized: dict[str, Any] = {}
        for key, child in value.items():
            if not isinstance(key, str):
                raise DatasetCanonicalizationError("non_string_object_key")
            normalized_key = _validate_unicode(key)
            if normalized_key in normalized:
                raise DatasetCanonicalizationError("duplicate_object_key")
            normalized[normalized_key] = child
        declared = {item.name for item in nested_fields}
        if set(normalized) - declared:
            raise DatasetCanonicalizationError("unknown_object_field")
        entries: list[list[Any]] = []
        for nested in nested_fields:
            if nested.name not in normalized:
                entries.append([nested.name, "missing", None])
            else:
                tag, child = _canonical_nested(nested.type, normalized[nested.name], nested.type_parameters)
                if tag == "null" and not nested.nullable:
                    raise DatasetCanonicalizationError("null_not_allowed")
                entries.append([nested.name, tag, child])
        return logical_type, entries
    raise DatasetCanonicalizationError("unsupported_logical_type")


def canonicalize_row(
    schema: Sequence[LogicalField | Mapping[str, Any]],
    row: Mapping[str, Any],
    *,
    schema_digest: bytes | None = None,
    source_index: int | None = None,
) -> CanonicalRecord:
    fields = normalize_schema(schema)
    if not isinstance(row, Mapping):
        raise DatasetCanonicalizationError("row_not_object")
    normalized_row: dict[str, Any] = {}
    for key, value in row.items():
        if not isinstance(key, str):
            raise DatasetCanonicalizationError("non_string_field_name")
        key_nfc = _validate_unicode(key)
        if key_nfc in normalized_row:
            raise DatasetCanonicalizationError("duplicate_field_name")
        normalized_row[key_nfc] = value
    declared = {item.name for item in fields}
    if set(normalized_row) - declared:
        raise DatasetCanonicalizationError("unknown_field")
    canonical: list[list[Any]] = []
    for item in fields:
        if item.name not in normalized_row:
            canonical.append([item.name, "missing", None])
            continue
        tag, normalized = _canonical_nested(item.type, normalized_row[item.name], item.type_parameters)
        if tag == "null" and not item.nullable:
            raise DatasetCanonicalizationError("null_not_allowed")
        canonical.append([item.name, tag, normalized])
    canonical_bytes = jcs_canonical_bytes(canonical)
    digest = schema_digest or compute_schema_digest(fields)
    if len(digest) != 32:
        raise DatasetCanonicalizationError("invalid_schema_digest")
    base_digest = hashlib.sha256(b"aim-row-v1\0" + digest + b"\0" + canonical_bytes).digest()
    if source_index is not None and (isinstance(source_index, bool) or source_index < 0):
        raise DatasetCanonicalizationError("invalid_source_index")
    return CanonicalRecord(canonical, canonical_bytes, base_digest, dict(row), source_index)


def _json_no_duplicates(text: str) -> Any:
    def pairs_hook(pairs: list[tuple[str, Any]]) -> dict[str, Any]:
        result: dict[str, Any] = {}
        for key, value in pairs:
            normalized = _validate_unicode(key)
            if normalized in result:
                raise DatasetCanonicalizationError("duplicate_field_name")
            result[normalized] = value
        return result

    try:
        return json.loads(text, object_pairs_hook=pairs_hook, parse_constant=lambda _value: (_ for _ in ()).throw(DatasetCanonicalizationError("non_finite_number")))
    except json.JSONDecodeError as exc:
        raise DatasetCanonicalizationError("invalid_json") from exc


def _validate_parquet_physical_type(field: LogicalField, arrow_type: pa.DataType) -> None:
    matches = False
    if field.type == "boolean":
        matches = pa.types.is_boolean(arrow_type)
    elif field.type == "signed_integer":
        matches = pa.types.is_signed_integer(arrow_type)
    elif field.type == "decimal":
        matches = (
            pa.types.is_decimal(arrow_type)
            and arrow_type.precision == field.type_parameters["precision"]
            and arrow_type.scale == field.type_parameters["scale"]
        )
    elif field.type == "string":
        matches = pa.types.is_string(arrow_type) or pa.types.is_large_string(arrow_type)
    elif field.type == "date":
        matches = pa.types.is_date(arrow_type)
    elif field.type == "timestamp":
        unit_precision = {"s": 0, "ms": 3, "us": 6, "ns": 9}
        matches = (
            pa.types.is_timestamp(arrow_type)
            and arrow_type.tz in {"UTC", "Etc/UTC", "+00:00"}
            and unit_precision.get(arrow_type.unit) == field.type_parameters["timestamp_precision"]
        )
    elif field.type == "binary":
        matches = pa.types.is_binary(arrow_type) or pa.types.is_large_binary(arrow_type)
    elif field.type == "array":
        matches = pa.types.is_list(arrow_type) or pa.types.is_large_list(arrow_type) or pa.types.is_fixed_size_list(arrow_type)
    elif field.type == "object":
        matches = pa.types.is_struct(arrow_type) or pa.types.is_map(arrow_type)
    if not matches:
        raise DatasetCanonicalizationError("parquet_physical_type_mismatch")


def iter_dataset_rows(
    path: Path,
    file_format: Literal["csv", "parquet", "json-array", "ndjson"],
    schema: Sequence[LogicalField | Mapping[str, Any]],
    *,
    csv_options: CsvParseOptions | None = None,
) -> Iterator[Mapping[str, Any]]:
    """Parse every row under explicit options; no format/schema guessing."""
    fields = normalize_schema(schema)
    if not path.is_file():
        raise DatasetCanonicalizationError("source_unavailable")
    if file_format == "csv":
        if csv_options is None:
            raise DatasetCanonicalizationError("csv_options_required")
        csv_options.validate()
        try:
            handle = path.open("r", encoding=csv_options.encoding, newline="")
        except (LookupError, UnicodeError, OSError) as exc:
            raise DatasetCanonicalizationError("invalid_csv_encoding") from exc
        with handle:
            reader = csv.reader(handle, delimiter=csv_options.delimiter, quotechar=csv_options.quotechar, strict=True)
            names = [item.name for item in fields]
            try:
                if csv_options.header:
                    header = next(reader)
                    header = [_validate_unicode(value) for value in header]
                    if len(set(header)) != len(header):
                        raise DatasetCanonicalizationError("duplicate_field_name")
                    if set(header) != set(names):
                        raise DatasetCanonicalizationError("csv_header_schema_mismatch")
                    names = header
                for values in reader:
                    if len(values) != len(names):
                        raise DatasetCanonicalizationError("csv_column_count_mismatch")
                    yield {name: None if value == csv_options.null_token else value for name, value in zip(names, values)}
            except (csv.Error, UnicodeError) as exc:
                raise DatasetCanonicalizationError("invalid_csv") from exc
        return
    if file_format == "parquet":
        try:
            parquet = pq.ParquetFile(path)
            parquet_names = [_validate_unicode(name) for name in parquet.schema_arrow.names]
            if len(set(parquet_names)) != len(parquet_names) or set(parquet_names) != {item.name for item in fields}:
                raise DatasetCanonicalizationError("parquet_schema_mismatch")
            arrow_fields = {item.name: item.type for item in parquet.schema_arrow}
            for field in fields:
                _validate_parquet_physical_type(field, arrow_fields[field.name])
            for batch in parquet.iter_batches():
                for row in batch.to_pylist():
                    yield row
        except DatasetCanonicalizationError:
            raise
        except Exception as exc:
            raise DatasetCanonicalizationError("invalid_parquet") from exc
        return
    if file_format == "json-array":
        try:
            value = _json_no_duplicates(path.read_text(encoding="utf-8"))
        except UnicodeError as exc:
            raise DatasetCanonicalizationError("invalid_unicode") from exc
        if not isinstance(value, list):
            raise DatasetCanonicalizationError("json_array_required")
        for row in value:
            if not isinstance(row, Mapping):
                raise DatasetCanonicalizationError("row_not_object")
            yield row
        return
    if file_format == "ndjson":
        try:
            with path.open("r", encoding="utf-8") as handle:
                for line in handle:
                    if not line.strip():
                        raise DatasetCanonicalizationError("blank_ndjson_line")
                    row = _json_no_duplicates(line)
                    if not isinstance(row, Mapping):
                        raise DatasetCanonicalizationError("row_not_object")
                    yield row
        except UnicodeError as exc:
            raise DatasetCanonicalizationError("invalid_unicode") from exc
        return
    raise DatasetCanonicalizationError("unsupported_dataset_format")


class DatasetCanonicalizationService:
    """Canonicalize the full local dataset as a streaming iterator."""

    def canonicalize_dataset(
        self,
        path: Path,
        file_format: Literal["csv", "parquet", "json-array", "ndjson"],
        schema: Sequence[LogicalField | Mapping[str, Any]],
        *,
        csv_options: CsvParseOptions | None = None,
    ) -> Iterator[CanonicalRecord]:
        normalized_schema = normalize_schema(schema)
        digest = compute_schema_digest(normalized_schema)
        count = 0
        for source_index, row in enumerate(
            iter_dataset_rows(path, file_format, normalized_schema, csv_options=csv_options)
        ):
            count += 1
            yield canonicalize_row(
                normalized_schema,
                row,
                schema_digest=digest,
                source_index=source_index,
            )
        if count == 0:
            raise DatasetCanonicalizationError("empty_dataset")
