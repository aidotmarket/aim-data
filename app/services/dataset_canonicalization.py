"""Explicit, lossless commitment parsing, separate from discovery/preview readers.

The schema is declared, never inferred from a sample. Errors expose codes only.
Root descriptor/value depth is zero; one node per declared type or value.
"""

from __future__ import annotations

import base64
import csv
import hashlib
import json
import os
import re
import stat
import unicodedata
from dataclasses import dataclass
from datetime import date, datetime, timedelta, timezone
from decimal import Decimal
from pathlib import Path
from typing import Any, Iterator

from app.services.dataset_merkle_service import (
    CommitmentValidationError as Error,
    canonical_json_bytes,
    compute_schema_digest,
)

MAX_RECORD_BYTES = 8 * 1024 * 1024
TAGS = {
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
INT_TYPES = set(
    "TINYINT INT1 SMALLINT INT2 SHORT INTEGER INT INT4 SIGNED BIGINT INT8 LONG HUGEINT UTINYINT USMALLINT UINTEGER UBIGINT".split()
)
STRING_TYPES = set("VARCHAR CHAR BPCHAR TEXT STRING".split())
TIMESTAMPS = {
    "TIMESTAMP": 6,
    "DATETIME": 6,
    "TIMESTAMP_S": 0,
    "TIMESTAMP_MS": 3,
    "TIMESTAMP_NS": 9,
    "TIMESTAMPTZ": 6,
    "TIMESTAMP WITH TIME ZONE": 6,
}


def nfc(value: str) -> str:
    if not isinstance(value, str):
        raise Error("invalid_unicode")
    try:
        value.encode("utf-8")
    except UnicodeError:
        raise Error("invalid_unicode") from None
    return unicodedata.normalize("NFC", value)


def dispatch_type(physical: str, *, element=None, members=None, source_timezone=None):
    """Exhaustive pinned DuckDB 0.9.2 dispatch, including nested rejection."""
    if not isinstance(physical, str):
        raise Error("unsupported_logical_type")
    kind = " ".join(physical.upper().split())
    if kind in {"BOOLEAN", "BOOL", "LOGICAL"}:
        return "boolean", {}
    if kind in INT_TYPES:
        return "signed_integer", {}
    match = re.fullmatch(r"(?:DECIMAL|NUMERIC)\((\d+),\s*(\d+)\)", kind)
    if match:
        p, s = map(int, match.groups())
        if not 1 <= p <= 38 or not 0 <= s <= p:
            raise Error("invalid_decimal_parameters")
        return "decimal", {"precision": p, "scale": s}
    if kind in STRING_TYPES:
        return "string", {}
    if kind == "DATE":
        return "date", {}
    if kind in TIMESTAMPS:
        if kind not in {"TIMESTAMPTZ", "TIMESTAMP WITH TIME ZONE"} and not valid_offset(
            source_timezone
        ):
            raise Error("timestamp_timezone_required")
        return "timestamp", {"timestamp_precision": TIMESTAMPS[kind]}
    if kind in {"BLOB", "BYTEA", "BINARY", "VARBINARY"}:
        return "binary", {}
    if kind.endswith("[]"):
        tag, params = dispatch_type(
            kind[:-2], element=element, members=members, source_timezone=source_timezone
        )
        return "array", {"element_type": {"type": tag, "type_parameters": params}}
    if kind == "LIST" and isinstance(element, dict):
        tag, params = dispatch_type(**element)
        return "array", {"element_type": {"type": tag, "type_parameters": params}}
    if kind.startswith("STRUCT(") and isinstance(members, list):
        import duckdb

        try:
            children = duckdb.sqltype(physical).children
            if len(children) != len(members):
                raise Error("schema_mismatch")
            declared = {nfc(member["name"]): member for member in members}
            for name, child in children:
                member = declared.get(nfc(name))
                if member is None or duckdb.sqltype(member["physical"]) != child:
                    raise Error("schema_mismatch")
        except Error:
            raise
        except Exception:
            raise Error("unsupported_logical_type") from None
        kind = "STRUCT"
    if kind == "STRUCT" and isinstance(members, list):
        fields = []
        for member in members:
            if not isinstance(member, dict) or set(member) - {
                "name",
                "nullable",
                "physical",
                "element",
                "members",
                "source_timezone",
            }:
                raise Error("invalid_schema")
            tag, params = dispatch_type(
                **{k: v for k, v in member.items() if k not in {"name", "nullable"}}
            )
            fields.append(
                {
                    "name": member["name"],
                    "nullable": member["nullable"],
                    "type": tag,
                    "type_parameters": params,
                }
            )
        return "object", {"object_fields": fields}
    raise Error("unsupported_logical_type")


def valid_offset(value):
    return isinstance(value, str) and (
        value == "Z"
        or re.fullmatch(r"[+-](?:0\d|1\d|2[0-3]):[0-5]\d", value) is not None
    )


@dataclass(frozen=True)
class ParsingDeclaration:
    format: str
    encoding: str | None = None
    delimiter: str | None = None
    quote: str | None = None
    escape: str | None = None
    header: bool | None = None
    locale: str | None = None
    null_token: str | None = None
    source_timezone: str | None = None

    def validate(self):
        if self.format not in {"csv", "tsv", "json-array", "ndjson", "parquet"}:
            raise Error("unsupported_format")
        if self.source_timezone is not None and not valid_offset(self.source_timezone):
            raise Error("timestamp_timezone_required")
        if self.format in {"csv", "tsv"}:
            if (
                self.encoding != "utf-8"
                or self.locale != "C"
                or type(self.header) is not bool
                or not isinstance(self.null_token, str)
            ):
                raise Error("parsing_declaration_required")
            if (
                not isinstance(self.delimiter, str)
                or len(self.delimiter) != 1
                or not isinstance(self.quote, str)
                or len(self.quote) != 1
                or not isinstance(self.escape, str)
                or len(self.escape) > 1
            ):
                raise Error("parsing_declaration_required")
            if self.format == "tsv" and self.delimiter != "\t":
                raise Error("invalid_delimiter")
        elif self.format in {"json-array", "ndjson"} and self.encoding != "utf-8":
            raise Error("parsing_declaration_required")


class CanonicalSchema:
    def __init__(self, descriptors):
        self.nodes = 0
        self.descriptors = self._fields(descriptors, 0)
        self.source_names = [nfc(f[0]) for f in descriptors]
        self.bytes = canonical_json_bytes(self.descriptors)
        self.digest = compute_schema_digest(self.bytes)

    def _type(self, tag, params, depth):
        self.nodes += 1
        if self.nodes > 10000:
            raise Error("node_limit")
        if depth > 16:
            raise Error("depth_limit")
        if not isinstance(tag, str) or tag not in TAGS:
            raise Error("unsupported_logical_type")
        if not isinstance(params, dict):
            raise Error("invalid_schema")
        if tag == "decimal":
            if (
                set(params) != {"precision", "scale"}
                or any(type(v) is not int for v in params.values())
                or not 1 <= params["precision"] <= 38
                or not 0 <= params["scale"] <= params["precision"]
            ):
                raise Error("invalid_decimal_parameters")
        elif tag == "timestamp":
            if (
                set(params) != {"timestamp_precision"}
                or type(params["timestamp_precision"]) is not int
                or params["timestamp_precision"] not in {0, 3, 6, 9}
            ):
                raise Error("invalid_timestamp_parameters")
        elif tag == "array":
            if (
                set(params) != {"element_type"}
                or not isinstance(params["element_type"], dict)
                or set(params["element_type"]) != {"type", "type_parameters"}
            ):
                raise Error("invalid_schema")
            elem = params["element_type"]
            return {
                "element_type": {
                    "type": elem["type"],
                    "type_parameters": self._type(
                        elem["type"], elem["type_parameters"], depth + 1
                    ),
                }
            }
        elif tag == "object":
            if set(params) != {"object_fields"} or not isinstance(
                params["object_fields"], list
            ):
                raise Error("invalid_schema")
            raw = params["object_fields"]
            if any(
                not isinstance(f, dict)
                or set(f) != {"name", "type", "nullable", "type_parameters"}
                for f in raw
            ):
                raise Error("invalid_schema")
            fields = self._fields(
                [
                    [f["name"], f["type"], f["nullable"], f["type_parameters"]]
                    for f in raw
                ],
                depth + 1,
            )
            return {
                "object_fields": [
                    dict(zip(("name", "type", "nullable", "type_parameters"), f))
                    for f in fields
                ]
            }
        elif params:
            raise Error("invalid_schema")
        return dict(params)

    def _fields(self, fields, depth):
        if not isinstance(fields, (list, tuple)) or not fields:
            raise Error("invalid_schema")
        if len(fields) > 500:
            raise Error("field_limit")
        out, names = [], set()
        for field in fields:
            if not isinstance(field, (list, tuple)) or len(field) != 4:
                raise Error("invalid_schema")
            name, tag, nullable, params = field
            name = nfc(name)
            if name in names:
                raise Error("duplicate_field")
            if type(nullable) is not bool or not isinstance(tag, str):
                raise Error("invalid_schema")
            names.add(name)
            out.append([name, tag, nullable, self._type(tag, params, depth)])
        return sorted(out, key=lambda f: f[0].encode("utf-8"))

    def canonical_row(self, record, *, text=False, source_timezone=None):
        count = [0]
        result = self._record(record, self.descriptors, 0, count, text, source_timezone)
        encoded = canonical_json_bytes(result)
        if len(encoded) > MAX_RECORD_BYTES:
            raise Error("record_resource_limit")
        return encoded

    def _record(self, record, fields, depth, count, text, offset):
        if not isinstance(record, dict):
            raise Error("invalid_record")
        normalized = {}
        for key, value in record.items():
            key = nfc(key)
            if key in normalized:
                raise Error("duplicate_field")
            normalized[key] = value
        if set(normalized) - {f[0] for f in fields}:
            raise Error("unknown_field")
        out = []
        for name, tag, nullable, params in fields:
            if name not in normalized or normalized[name] is None:
                count[0] += 1
                if count[0] > 10000:
                    raise Error("node_limit")
                if depth > 16:
                    raise Error("depth_limit")
            if name not in normalized:
                out.append([name, "missing", None])
            elif normalized[name] is None:
                if not nullable:
                    raise Error("null_not_allowed")
                out.append([name, "null", None])
            else:
                out.append(
                    [
                        name,
                        tag,
                        self._value(
                            normalized[name], tag, params, depth, count, text, offset
                        ),
                    ]
                )
        return out

    def _value(self, value, tag, params, depth, count, text, offset):
        count[0] += 1
        if count[0] > 10000:
            raise Error("node_limit")
        if depth > 16:
            raise Error("depth_limit")
        if value is None:
            return None
        if tag == "string":
            return nfc(value)
        if tag == "boolean":
            if text and value in {"true", "false"}:
                return value == "true"
            if type(value) is not bool:
                raise Error("invalid_boolean")
            return value
        if tag == "signed_integer":
            if type(value) is int:
                return str(value)
            if isinstance(value, str) and re.fullmatch(r"-?(?:0|[1-9]\d*)", value):
                return "0" if value == "-0" else value
            raise Error("invalid_integer")
        if tag == "decimal":
            if not isinstance(value, (str, Decimal)) or (
                isinstance(value, str) and not re.fullmatch(r"-?\d+(?:\.\d+)?", value)
            ):
                raise Error("invalid_decimal")
            number = Decimal(value)
            if not number.is_finite():
                raise Error("invalid_decimal")
            _, digits, exponent = number.as_tuple()
            scale = max(0, -exponent)
            integral = max(0, len(digits) + exponent) if number else 0
            if (
                scale > params["scale"]
                or integral > params["precision"] - params["scale"]
            ):
                raise Error("decimal_out_of_range")
            if not number:
                return "0"
            result = format(number, "f")
            return result.rstrip("0").rstrip(".") if "." in result else result
        if tag == "date":
            if type(value) is date:
                return value.isoformat()
            if not isinstance(value, str) or not re.fullmatch(
                r"\d{4}-\d{2}-\d{2}", value
            ):
                raise Error("invalid_date")
            try:
                return date.fromisoformat(value).isoformat()
            except ValueError:
                raise Error("invalid_date") from None
        if tag == "timestamp":
            return normalize_timestamp(value, params["timestamp_precision"], offset)
        if tag == "binary":
            if not isinstance(value, bytes):
                raise Error("invalid_binary")
            return base64.urlsafe_b64encode(value).rstrip(b"=").decode("ascii")
        if tag == "array":
            if not isinstance(value, list):
                raise Error("invalid_array")
            elem = params["element_type"]
            return [
                self._value(
                    v,
                    elem["type"],
                    elem["type_parameters"],
                    depth + 1,
                    count,
                    text,
                    offset,
                )
                for v in value
            ]
        if tag == "object":
            fields = [
                [f["name"], f["type"], f["nullable"], f["type_parameters"]]
                for f in params["object_fields"]
            ]
            return self._record(value, fields, depth + 1, count, text, offset)
        raise Error("unsupported_logical_type")


def normalize_timestamp(value, precision, offset):
    if isinstance(value, datetime):
        value = value.isoformat()
    if not isinstance(value, str):
        raise Error("invalid_timestamp")
    match = re.fullmatch(
        r"(\d{4}-\d{2}-\d{2})[T ](\d{2}:\d{2}:\d{2})(?:\.(\d{1,9}))?(Z|[+-]\d{2}:\d{2})?",
        value,
    )
    if not match:
        raise Error("invalid_timestamp")
    day, clock, fraction, zone = match.groups()
    zone = zone or offset
    if not valid_offset(zone):
        raise Error("timestamp_timezone_required")
    fraction = fraction or ""
    if len(fraction.rstrip("0")) > precision:
        raise Error("timestamp_precision_loss")
    try:
        dt = datetime.fromisoformat(day + "T" + clock)
        if zone != "Z":
            delta = timedelta(hours=int(zone[1:3]), minutes=int(zone[4:6]))
            dt -= delta if zone[0] == "+" else -delta
        dt = dt.replace(tzinfo=timezone.utc)
    except (ValueError, OverflowError):
        raise Error("invalid_timestamp") from None
    suffix = "." + fraction[:precision].ljust(precision, "0") if precision else ""
    return dt.strftime("%Y-%m-%dT%H:%M:%S") + suffix + "Z"


def _pairs(pairs):
    out = {}
    for key, value in pairs:
        key = nfc(key)
        if key in out:
            raise Error("duplicate_key")
        out[key] = value
    return out


def _json(raw):
    try:
        return json.loads(
            raw,
            object_pairs_hook=_pairs,
            parse_float=Decimal,
            parse_constant=lambda _: (_ for _ in ()).throw(Error("invalid_number")),
        )
    except (ValueError, RecursionError, UnicodeError) as exc:
        if isinstance(exc, Error):
            raise
        raise Error("invalid_json") from None


def _json_array(stream):
    """Incremental framed array; bounded one record, strict trailing syntax."""
    started = False
    ended = False
    buffer = bytearray()
    depth = 0
    quoted = escaped = False
    after_comma = False
    for chunk in iter(lambda: stream.read(65536), b""):
        for byte in chunk:
            if not started:
                if byte in b" \r\n\t":
                    continue
                if byte != 91:
                    raise Error("invalid_json")
                started = True
                continue
            if ended:
                if byte not in b" \r\n\t":
                    raise Error("invalid_json")
                continue
            if not quoted and depth == 0 and byte in (44, 93):
                if buffer.strip():
                    yield _json(bytes(buffer))
                elif byte == 44 or after_comma:
                    raise Error("invalid_json")
                buffer.clear()
                after_comma = byte == 44
                ended = byte == 93
                continue
            buffer.append(byte)
            if len(buffer) > MAX_RECORD_BYTES:
                raise Error("record_resource_limit")
            if quoted:
                if escaped:
                    escaped = False
                elif byte == 92:
                    escaped = True
                elif byte == 34:
                    quoted = False
            elif byte == 34:
                quoted = True
            elif byte in (91, 123):
                depth += 1
                if depth > 18:
                    raise Error("depth_limit")
            elif byte in (93, 125):
                depth -= 1
                if depth < 0:
                    raise Error("invalid_json")
    if not ended:
        raise Error("invalid_json")


def iter_records(
    path: Path, declaration: ParsingDeclaration, schema: CanonicalSchema
) -> Iterator[dict[str, Any]]:
    """Read the declared original file completely; reject replacement/mutation."""
    declaration.validate()
    try:
        fd = os.open(path, os.O_RDONLY | os.O_NOFOLLOW)
        with os.fdopen(fd, "rb") as source:
            before = os.fstat(source.fileno())
            if not stat.S_ISREG(before.st_mode):
                raise Error("invalid_source")
            fmt = declaration.format
            if fmt == "parquet":
                yield from _parquet(source, schema, declaration)
            elif fmt == "json-array":
                yield from _json_array(source)
            elif fmt == "ndjson":
                while raw := source.readline(MAX_RECORD_BYTES + 1):
                    if len(raw) > MAX_RECORD_BYTES:
                        raise Error("record_resource_limit")
                    if raw.strip():
                        yield _json(raw)
            else:
                # csv.reader supports quoted multiline fields, with a fixed field bound.
                old_limit = csv.field_size_limit(MAX_RECORD_BYTES)
                try:
                    consumed = [0]

                    def lines():
                        while raw := source.readline(MAX_RECORD_BYTES + 1):
                            consumed[0] += len(raw)
                            if consumed[0] > MAX_RECORD_BYTES:
                                raise Error("record_resource_limit")
                            yield raw.decode("utf-8")

                    reader = csv.reader(
                        lines(),
                        delimiter=declaration.delimiter,
                        quotechar=declaration.quote,
                        escapechar=declaration.escape or None,
                        strict=True,
                    )
                    names = schema.source_names
                    if declaration.header:
                        names = [nfc(k) for k in next(reader)]
                        if len(names) != len(set(names)):
                            raise Error("duplicate_field")
                        if set(names) != {f[0] for f in schema.descriptors}:
                            raise Error("invalid_header")
                    consumed[0] = 0
                    for row in reader:
                        consumed[0] = 0
                        if len(row) != len(names):
                            raise Error("invalid_record")
                        if sum(len(x.encode("utf-8")) for x in row) > MAX_RECORD_BYTES:
                            raise Error("record_resource_limit")
                        yield {
                            key: None if val == declaration.null_token else val
                            for key, val in zip(names, row)
                        }
                finally:
                    csv.field_size_limit(old_limit)
            # Compare the original descriptor identity with the final path identity.
            after = os.stat(path, follow_symlinks=False)
            if (
                before.st_dev,
                before.st_ino,
                before.st_size,
                before.st_mtime_ns,
                before.st_ctime_ns,
            ) != (
                after.st_dev,
                after.st_ino,
                after.st_size,
                after.st_mtime_ns,
                after.st_ctime_ns,
            ):
                raise Error("source_changed")
    except Error:
        raise
    except (OSError, ValueError, csv.Error, UnicodeError, StopIteration):
        raise Error("invalid_source") from None


def _parquet(source, schema, declaration):
    import pyarrow as pa
    import pyarrow.parquet as pq

    parquet = pq.ParquetFile(source)

    # Validate physical schema before reading; never cast unsupported values.
    def physical(typ):
        if pa.types.is_boolean(typ):
            return "boolean", {}
        if pa.types.is_integer(typ):
            return "signed_integer", {}
        if pa.types.is_decimal(typ):
            return dispatch_type(f"DECIMAL({typ.precision},{typ.scale})")
        if pa.types.is_string(typ) or pa.types.is_large_string(typ):
            return "string", {}
        if pa.types.is_binary(typ) or pa.types.is_large_binary(typ):
            return "binary", {}
        if pa.types.is_date32(typ):
            return "date", {}
        if pa.types.is_timestamp(typ):
            if typ.tz is None and declaration.source_timezone is None:
                raise Error("timestamp_timezone_required")
            return "timestamp", {
                "timestamp_precision": {"s": 0, "ms": 3, "us": 6, "ns": 9}[typ.unit]
            }
        if pa.types.is_list(typ) or pa.types.is_large_list(typ):
            tag, params = physical(typ.value_type)
            return "array", {"element_type": {"type": tag, "type_parameters": params}}
        if pa.types.is_struct(typ):
            fields = []
            for f in typ:
                tag, params = physical(f.type)
                fields.append(
                    {
                        "name": f.name,
                        "type": tag,
                        "nullable": f.nullable,
                        "type_parameters": params,
                    }
                )
            return "object", {"object_fields": fields}
        raise Error("unsupported_logical_type")

    descriptors = []
    for field in parquet.schema_arrow:
        tag, params = physical(field.type)
        descriptors.append([field.name, tag, field.nullable, params])
    if CanonicalSchema(descriptors).bytes != schema.bytes:
        raise Error("schema_mismatch")
    # Single rows prevent an arbitrarily wide batch; the worker RSS guard also
    # covers Arrow/native page decoding, which cannot be bounded by row count.
    for batch in parquet.iter_batches(batch_size=1, use_threads=False):
        if batch.nbytes > 16 * 1024 * 1024:
            raise Error("record_resource_limit")

        def exact_value(scalar):
            if not scalar.is_valid:
                return None
            typ = scalar.type
            if pa.types.is_timestamp(typ):
                precision = {"s": 0, "ms": 3, "us": 6, "ns": 9}[typ.unit]
                seconds, fraction = divmod(scalar.value, 10**precision)
                try:
                    instant = datetime(1970, 1, 1) + timedelta(seconds=seconds)
                except (OverflowError, ValueError):
                    raise Error("invalid_timestamp") from None
                suffix = f".{fraction:0{precision}d}" if precision else ""
                return (
                    instant.isoformat(timespec="seconds")
                    + suffix
                    + ("Z" if typ.tz else "")
                )
            if pa.types.is_list(typ) or pa.types.is_large_list(typ):
                return [exact_value(child) for child in scalar.values]
            if pa.types.is_struct(typ):
                return {field.name: exact_value(scalar[field.name]) for field in typ}
            return scalar.as_py()

        yield {
            name: exact_value(batch.column(i)[0])
            for i, name in enumerate(batch.schema.names)
        }


def parsing_options_digest(declaration):
    declaration.validate()
    return hashlib.sha256(canonical_json_bytes(vars(declaration))).digest()
