import json
from decimal import Decimal
from pathlib import Path

import pytest

from app.services.dataset_canonicalization import (
    CanonicalSchema,
    Error,
    ParsingDeclaration,
    dispatch_type,
    iter_records,
)
from app.services.dataset_merkle_service import canonical_json_bytes

FIXTURES = Path(__file__).parent / "fixtures"


def test_extended_ruling():
    fixture = json.loads((FIXTURES / "aim_dataset_merkle_v1_extended.json").read_text())
    for vector in fixture["must_reject"]:
        with pytest.raises(Error, match="^unsafe_integer$"):
            canonical_json_bytes(vector["metadata"])
    for vector in fixture["must_accept"]:
        assert canonical_json_bytes(vector["metadata"]).decode() == vector["canonical"]
    assert (
        canonical_json_bytes(fixture["exact_row_control"]["canonical_row"])
        == b'[["id","signed_integer","9007199254740993"]]'
    )


@pytest.mark.parametrize(
    "physical,tag",
    [(k, "boolean") for k in ["BOOLEAN", "BOOL", "LOGICAL"]]
    + [
        (k, "signed_integer")
        for k in "TINYINT INT1 SMALLINT INT2 SHORT INTEGER INT INT4 SIGNED BIGINT INT8 LONG HUGEINT UTINYINT USMALLINT UINTEGER UBIGINT".split()
    ]
    + [(k, "string") for k in "VARCHAR CHAR BPCHAR TEXT STRING".split()]
    + [(k, "binary") for k in "BLOB BYTEA BINARY VARBINARY".split()]
    + [("DATE", "date"), ("DECIMAL(12,2)", "decimal"), ("NUMERIC(38,9)", "decimal")],
)
def test_dispatch_supported(physical, tag):
    assert dispatch_type(physical)[0] == tag


@pytest.mark.parametrize(
    "physical",
    [
        "FLOAT",
        "REAL",
        "FLOAT4",
        "DOUBLE",
        "DOUBLE PRECISION",
        "FLOAT8",
        "UHUGEINT",
        "ARRAY",
        "INTEGER[3]",
        "MAP",
        "UNION",
        "ENUM",
        "UUID",
        "JSON",
        "TIME",
        "TIME WITH TIME ZONE",
        "TIMETZ",
        "INTERVAL",
        "BIT",
        "BITSTRING",
        "BIGNUM",
        "VARINT",
        "GEOMETRY",
        "VARIANT",
        "CUSTOM",
        "SQLNULL",
        "NULL",
        "UNKNOWN",
        "ANY",
        "UNLISTED",
        "FLOAT[]",
    ],
)
def test_dispatch_unsupported(physical):
    with pytest.raises(Error, match="^unsupported_logical_type$"):
        dispatch_type(physical)


@pytest.mark.parametrize(
    "physical,precision",
    [
        ("TIMESTAMP", 6),
        ("DATETIME", 6),
        ("TIMESTAMP_S", 0),
        ("TIMESTAMP_MS", 3),
        ("TIMESTAMP_NS", 9),
        ("TIMESTAMPTZ", 6),
        ("TIMESTAMP WITH TIME ZONE", 6),
    ],
)
def test_timestamp_dispatch(physical, precision):
    assert dispatch_type(physical, source_timezone="Z") == (
        "timestamp",
        {"timestamp_precision": precision},
    )
    if physical not in {"TIMESTAMPTZ", "TIMESTAMP WITH TIME ZONE"}:
        with pytest.raises(Error, match="timestamp_timezone_required"):
            dispatch_type(physical)


def test_list_struct_dispatch():
    assert dispatch_type("INTEGER[]") == dispatch_type(
        "LIST", element={"physical": "INTEGER"}
    )
    tag, params = dispatch_type(
        "STRUCT", members=[{"name": "x", "nullable": True, "physical": "VARCHAR"}]
    )
    assert CanonicalSchema([["obj", tag, True, params]]).nodes == 2
    with pytest.raises(Error, match="unsupported_logical_type"):
        dispatch_type(
            "STRUCT", members=[{"name": "x", "nullable": True, "physical": "UUID"}]
        )


@pytest.mark.parametrize("size", [24, 25, 26, 499, 500, 501])
def test_dictionary_boundaries(size):
    fields = [[f"f{i}", "string", True, {}] for i in range(size)]
    if size > 500:
        with pytest.raises(Error, match="field_limit"):
            CanonicalSchema(fields)
    else:
        assert len(CanonicalSchema(fields).descriptors) == size


def nested(depth):
    tag, params = "string", {}
    for _ in range(depth):
        tag, params = (
            "array",
            {"element_type": {"type": tag, "type_parameters": params}},
        )
    return [["a", tag, True, params]]


@pytest.mark.parametrize("depth", [15, 16, 17])
def test_depth_boundary(depth):
    if depth == 17:
        with pytest.raises(Error, match="depth_limit"):
            CanonicalSchema(nested(depth))
    else:
        assert CanonicalSchema(nested(depth)).nodes == depth + 1


@pytest.mark.parametrize("nodes", [9999, 10000, 10001])
def test_node_boundary(nodes):
    fields = []
    remaining = nodes
    while remaining:
        n = min(500, remaining - 1)
        if n <= 0:
            fields.append([f"x{len(fields)}", "string", True, {}])
            remaining -= 1
        else:
            fields.append(
                [
                    f"x{len(fields)}",
                    "object",
                    True,
                    {
                        "object_fields": [
                            {
                                "name": f"v{i}",
                                "type": "string",
                                "nullable": True,
                                "type_parameters": {},
                            }
                            for i in range(n)
                        ]
                    },
                ]
            )
            remaining -= n + 1
    if nodes > 10000:
        with pytest.raises(Error, match="node_limit"):
            CanonicalSchema(fields)
    else:
        assert CanonicalSchema(fields).nodes == nodes


def test_golden_rows_presence_unicode_numeric():
    fixture = json.loads((FIXTURES / "aim_dataset_merkle_v1.json").read_text())
    schema = CanonicalSchema(fixture["canonical_schema"])
    records = [
        {"id": 3},
        {"id": 2, "name": None},
        {"id": 1, "name": "Cafe\u0301"},
        {"id": 1, "name": "Café"},
        {"id": 4, "name": "Z"},
    ]
    for record, vector in zip(records, fixture["rows"]):
        assert schema.canonical_row(record) == canonical_json_bytes(
            vector["canonical_row"]
        )
    assert schema.canonical_row({"id": 2**100}).decode().find(str(2**100)) >= 0
    with pytest.raises(Error, match="invalid_integer"):
        schema.canonical_row({"id": 1.0})
    with pytest.raises(Error, match="null_not_allowed"):
        schema.canonical_row({"id": None})


def test_unicode_order_collisions():
    schema = CanonicalSchema(
        [["\U00010000", "string", True, {}], ["\ue000", "string", True, {}]]
    )
    assert schema.descriptors[0][0] == "\ue000"
    with pytest.raises(Error, match="duplicate_field"):
        CanonicalSchema([["é", "string", True, {}], ["e\u0301", "string", True, {}]])
    with pytest.raises(Error, match="invalid_unicode"):
        CanonicalSchema([["\ud800", "string", True, {}]])


@pytest.mark.parametrize(
    "value,expected",
    [("12.30", "12.3"), ("-0.00", "0"), (Decimal("9999999999.99"), "9999999999.99")],
)
def test_decimal_exact(value, expected):
    schema = CanonicalSchema([["x", "decimal", False, {"precision": 12, "scale": 2}]])
    assert json.loads(schema.canonical_row({"x": value}))[0][2] == expected


@pytest.mark.parametrize("value", ["0.001", "10000000000.00", "NaN", "1e2", 1.2])
def test_decimal_reject(value):
    with pytest.raises(Error):
        CanonicalSchema(
            [["x", "decimal", False, {"precision": 12, "scale": 2}]]
        ).canonical_row({"x": value})


def test_timestamp_exact():
    schema = CanonicalSchema([["t", "timestamp", False, {"timestamp_precision": 9}]])
    assert (
        json.loads(schema.canonical_row({"t": "2026-07-21T14:34:56.123456789+02:00"}))[
            0
        ][2]
        == "2026-07-21T12:34:56.123456789Z"
    )
    with pytest.raises(Error, match="timestamp_timezone_required"):
        schema.canonical_row({"t": "2026-07-21T12:34:56"})


@pytest.mark.parametrize(
    "fmt,content",
    [("json-array", '[{"id":1,"id":2}]'), ("ndjson", '{"id":1,"id":2}\n')],
)
def test_duplicate_keys(tmp_path, fmt, content):
    path = tmp_path / "source"
    path.write_text(content)
    with pytest.raises(Error, match="duplicate_key"):
        list(
            iter_records(
                path,
                ParsingDeclaration(fmt, encoding="utf-8"),
                CanonicalSchema([["id", "signed_integer", False, {}]]),
            )
        )


def test_real_formats(tmp_path):
    import pyarrow as pa
    import pyarrow.parquet as pq

    fields = [["id", "signed_integer", False, {}], ["name", "string", True, {}]]
    schema = CanonicalSchema(fields)
    expected = [
        schema.canonical_row({"id": 1, "name": "Café"}),
        schema.canonical_row({"id": 2, "name": None}),
    ]
    formats = {
        "csv": "id,name\n1,Cafe\u0301\n2,NULL\n",
        "tsv": "id\tname\n1\tCafé\n2\tNULL\n",
        "json-array": '[{"id":1,"name":"Café"},{"id":2,"name":null}]',
        "ndjson": '{"id":1,"name":"Café"}\n{"id":2,"name":null}\n',
    }
    for fmt, content in formats.items():
        path = tmp_path / fmt
        path.write_text(content)
        declaration = ParsingDeclaration(
            fmt,
            encoding="utf-8",
            delimiter="\t" if fmt == "tsv" else ",",
            quote='"',
            escape="",
            header=True,
            locale="C",
            null_token="NULL",
        )
        actual = [
            schema.canonical_row(r, text=fmt in {"csv", "tsv"})
            for r in iter_records(path, declaration, schema)
        ]
        assert actual == expected
    path = tmp_path / "data.parquet"
    pq.write_table(
        pa.Table.from_pylist(
            [{"id": 1, "name": "Café"}, {"id": 2, "name": None}],
            schema=pa.schema(
                [
                    pa.field("id", pa.int64(), nullable=False),
                    pa.field("name", pa.string()),
                ]
            ),
        ),
        path,
    )
    assert [
        schema.canonical_row(r)
        for r in iter_records(path, ParsingDeclaration("parquet"), schema)
    ] == expected
    path = tmp_path / "missing.json"
    path.write_text('[{"id":2}]')
    assert [
        schema.canonical_row(r)
        for r in iter_records(
            path, ParsingDeclaration("json-array", encoding="utf-8"), schema
        )
    ] != [expected[1]]


def test_source_mutation(tmp_path):
    path = tmp_path / "data"
    path.write_text('{"x":"a"}\n')
    schema = CanonicalSchema([["x", "string", False, {}]])
    records = iter_records(path, ParsingDeclaration("ndjson", encoding="utf-8"), schema)
    next(records)
    path.write_text('{"x":"b"}\n')
    with pytest.raises(Error, match="source_changed"):
        list(records)
