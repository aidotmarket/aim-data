import hashlib
import json
from datetime import datetime, timezone
from decimal import Decimal

import pyarrow as pa
import pyarrow.parquet as pq
import pytest

from app.services.dataset_canonicalization import (
    CsvParseOptions,
    DatasetCanonicalizationError,
    DatasetCanonicalizationService,
    canonicalize_row,
    compute_schema_digest,
    encode_base64url,
    jcs_canonical_bytes,
)


FIXTURE = "tests/fixtures/aim_dataset_merkle_v1.json"


def test_reviewed_fixture_digest_and_schema_vector():
    raw = open(FIXTURE, "rb").read()
    vector = json.loads(raw)
    assert hashlib.sha256(raw).hexdigest() == "f3e358d1e7ce7c836ce8604810675e0952201a7799b92d30858cd489906499af"
    schema = [
        {"name": item[0], "type": item[1], "nullable": item[2], "type_parameters": item[3]}
        for item in vector["canonical_schema"]
    ]
    assert encode_base64url(compute_schema_digest(schema)) == vector["schema_digest"]


def test_jcs_uses_utf16_key_order_not_python_codepoint_order():
    # U+1F600 sorts before U+E000 by UTF-16 code units, but after it by code point.
    assert jcs_canonical_bytes({"\ue000": 1, "😀": 2}) == '{"😀":2,"\ue000":1}'.encode()


def test_unicode_decimal_timestamp_missing_and_null_are_unambiguous():
    schema = [
        {"name": "amount", "type": "decimal", "nullable": False, "type_parameters": {"precision": 12, "scale": 2}},
        {"name": "at", "type": "timestamp", "nullable": False, "type_parameters": {"timestamp_precision": 3}},
        {"name": "label", "type": "string", "nullable": True, "type_parameters": {}},
    ]
    digest = compute_schema_digest(schema)
    missing = canonicalize_row(
        schema,
        {"amount": Decimal("12.30"), "at": datetime(2026, 7, 21, 12, 34, 56, 123000, tzinfo=timezone.utc)},
        schema_digest=digest,
    )
    explicit_null = canonicalize_row(
        schema,
        {"amount": "12.3", "at": "2026-07-21T12:34:56.123Z", "label": None},
        schema_digest=digest,
    )
    assert missing.canonical_row[0][2] == "12.3"
    assert missing.canonical_row[1][2] == "2026-07-21T12:34:56.123Z"
    assert missing.canonical_row[2][1] == "missing"
    assert explicit_null.canonical_row[2][1] == "null"
    assert missing.base_row_digest != explicit_null.base_row_digest

    nanos_schema = [{"name": "at", "type": "timestamp", "nullable": False, "type_parameters": {"timestamp_precision": 9}}]
    nanos = canonicalize_row(nanos_schema, {"at": "2026-07-21T14:34:56.123456789+02:00"})
    assert nanos.canonical_row[0][2] == "2026-07-21T12:34:56.123456789Z"


def test_csv_json_ndjson_and_parquet_parse_to_equal_records(tmp_path):
    schema = [
        {"name": "id", "type": "signed_integer", "nullable": False, "type_parameters": {}},
        {"name": "name", "type": "string", "nullable": True, "type_parameters": {}},
    ]
    csv_path = tmp_path / "rows.csv"
    csv_path.write_text("id,name\n1,Café\n2,NULL\n", encoding="utf-8")
    json_path = tmp_path / "rows.json"
    json_path.write_text('[{"id":1,"name":"Cafe\\u0301"},{"id":2,"name":null}]', encoding="utf-8")
    ndjson_path = tmp_path / "rows.ndjson"
    ndjson_path.write_text('{"id":1,"name":"Café"}\n{"id":2,"name":null}\n', encoding="utf-8")
    parquet_path = tmp_path / "rows.parquet"
    pq.write_table(pa.table({"id": [1, 2], "name": ["Café", None]}), parquet_path)
    service = DatasetCanonicalizationService()
    csv_records = list(service.canonicalize_dataset(
        csv_path,
        "csv",
        schema,
        csv_options=CsvParseOptions("utf-8", ",", '"', True, "C", "NULL"),
    ))
    json_records = list(service.canonicalize_dataset(json_path, "json-array", schema))
    ndjson_records = list(service.canonicalize_dataset(ndjson_path, "ndjson", schema))
    parquet_records = list(service.canonicalize_dataset(parquet_path, "parquet", schema))
    expected = [record.base_row_digest for record in json_records]
    assert [record.base_row_digest for record in csv_records] == expected
    assert [record.base_row_digest for record in ndjson_records] == expected
    assert [record.base_row_digest for record in parquet_records] == expected


def test_parquet_physical_type_mismatch_stops_instead_of_coercing(tmp_path):
    path = tmp_path / "wrong.parquet"
    pq.write_table(pa.table({"id": ["1"]}), path)
    schema = [{"name": "id", "type": "signed_integer", "nullable": False, "type_parameters": {}}]
    with pytest.raises(DatasetCanonicalizationError) as exc:
        list(DatasetCanonicalizationService().canonicalize_dataset(path, "parquet", schema))
    assert exc.value.code == "parquet_physical_type_mismatch"


@pytest.mark.parametrize(
    "row,code",
    [
        ({"id": float("nan")}, "invalid_signed_integer"),
        ({"id": 1, "extra": "x"}, "unknown_field"),
    ],
)
def test_ambiguous_or_unsupported_values_stop(row, code):
    schema = [{"name": "id", "type": "signed_integer", "nullable": False, "type_parameters": {}}]
    with pytest.raises(DatasetCanonicalizationError) as exc:
        canonicalize_row(schema, row)
    assert exc.value.code == code
