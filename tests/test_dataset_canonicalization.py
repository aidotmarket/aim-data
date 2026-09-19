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
from app.services.dataset_merkle_service import (
    build_merkle_root,
    canonical_json_bytes,
    compute_base_row_digest,
    compute_leaf_hash,
)

FIXTURES = Path(__file__).parent / "fixtures"


def test_extended_ruling():
    fixture = json.loads((FIXTURES / "aim_dataset_merkle_v1_extended.json").read_text())
    for vector in fixture["must_reject"]:
        with pytest.raises(Error, match="^" + vector["reason"] + "$"):
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


@pytest.mark.parametrize(
    "fixture_name",
    ["aim_dataset_parser_v1.json", "aim_dataset_integral_decimal_v1.json"],
)
def test_shared_real_file_corpus(tmp_path, fixture_name):
    import base64
    from app.services import dataset_merkle_service as m

    fixture = json.loads((FIXTURES / fixture_name).read_text())
    schema = CanonicalSchema(fixture["schema"])
    assert m.encode_base64url(schema.digest) == fixture["schema_digest"]
    for vector in fixture["vectors"]:
        path = tmp_path / vector["format"]
        path.write_bytes(base64.b64decode(vector["file_bytes_base64"]))
        declaration = ParsingDeclaration(**vector["declaration"])
        rows = [
            schema.canonical_row(r, text=vector["format"] in {"csv", "tsv"})
            for r in iter_records(path, declaration, schema)
        ]
        assert [r.decode() for r in rows] == fixture["canonical_rows"]
        leaves = [
            m.compute_leaf_hash(b, 0)
            for b, r in sorted(
                (m.compute_base_row_digest(schema.digest, r), r) for r in rows
            )
        ]
        assert m.encode_base64url(m.build_merkle_root(leaves)) == fixture["root"]


def test_independent_ecmascript_serializer():
    import subprocess

    value = {
        "\U00010000": "supplementary",
        "a": [-(2**53 - 1), 2**53 - 1, True, None, "Café\n"],
    }
    javascript = 'const v=JSON.parse(process.argv[1]); function j(v){if(Array.isArray(v))return "["+v.map(j).join(",")+"]";if(v&&typeof v==="object")return "{"+Object.keys(v).sort().map(k=>JSON.stringify(k)+":"+j(v[k])).join(",")+"}";return JSON.stringify(v)}process.stdout.write(j(v));'
    actual = subprocess.check_output(
        # Execute the verifier directly (no shell); CI need not install a CLI
        # output-filtering proxy to compare Node's exact bytes.
        ["node", "-e", javascript, json.dumps(value)]
    )
    assert actual == canonical_json_bytes(value)


@pytest.mark.parametrize(
    "tag,value,code",
    [
        ("boolean", 1, "invalid_boolean"),
        ("string", b"bad", "invalid_unicode"),
        ("date", "infinity", "invalid_date"),
        ("binary", "abcd", "invalid_binary"),
        ("signed_integer", True, "invalid_integer"),
    ],
)
def test_nonreflecting_value_errors(tag, value, code):
    with pytest.raises(Error, match="^" + code + "$"):
        CanonicalSchema([["x", tag, False, {}]]).canonical_row({"x": value})


@pytest.mark.parametrize(
    "content",
    [
        '[{"x":1},]',
        '[{"x":1}] trailing',
        '[{"x":1}',
        '[{"x":1} {"x":2}]',
        '[{"x":NaN}]',
    ],
)
def test_malformed_array(tmp_path, content):
    path = tmp_path / "source"
    path.write_text(content)
    with pytest.raises(Error):
        list(
            iter_records(
                path,
                ParsingDeclaration("json-array", encoding="utf-8"),
                CanonicalSchema([["x", "signed_integer", False, {}]]),
            )
        )


def test_nested_values():
    schema = CanonicalSchema(
        [
            [
                "o",
                "object",
                True,
                {
                    "object_fields": [
                        {
                            "name": "x",
                            "type": "string",
                            "nullable": True,
                            "type_parameters": {},
                        }
                    ]
                },
            ],
            [
                "a",
                "array",
                False,
                {"element_type": {"type": "signed_integer", "type_parameters": {}}},
            ],
        ]
    )
    assert json.loads(schema.canonical_row({"o": {}, "a": [1, None, 2**70]})) == [
        ["a", "array", ["1", None, str(2**70)]],
        ["o", "object", [["x", "missing", None]]],
    ]
    with pytest.raises(Error, match="unknown_field"):
        schema.canonical_row({"o": {"other": "PRIVATE"}, "a": []})


def test_csv_no_header_declared_order_and_multiline(tmp_path):
    schema = CanonicalSchema([["z", "string", False, {}], ["a", "string", False, {}]])
    path = tmp_path / "data"
    path.write_text('"line1\nline2",last\n')
    declaration = ParsingDeclaration(
        "csv",
        encoding="utf-8",
        delimiter=",",
        quote='"',
        escape="",
        header=False,
        locale="C",
        null_token="NULL",
    )
    assert list(iter_records(path, declaration, schema)) == [
        {"z": "line1\nline2", "a": "last"}
    ]


def _csv_commitment(path, schema, escape):
    declaration = ParsingDeclaration(
        "csv",
        encoding="utf-8",
        delimiter=",",
        quote='"',
        escape=escape,
        header=True,
        locale="C",
        null_token="",
    )
    rows = [
        schema.canonical_row(record, text=True)
        for record in iter_records(path, declaration, schema)
    ]
    leaves = [
        compute_leaf_hash(base_digest, 0)
        for base_digest, _ in sorted(
            (compute_base_row_digest(schema.digest, row), row) for row in rows
        )
    ]
    return rows, schema.digest, build_merkle_root(leaves)


def test_csv_equal_quote_escape_is_rfc4180_and_hash_equivalent(tmp_path):
    path = tmp_path / "ordinary.csv"
    path.write_text('id,text\n1,"hello, world"\n2,"say ""hello"""\n')
    schema = CanonicalSchema(
        [["id", "signed_integer", False, {}], ["text", "string", False, {}]]
    )

    no_escape = _csv_commitment(path, schema, "")
    quote_escape = _csv_commitment(path, schema, '"')

    assert quote_escape == no_escape
    assert [row.decode() for row in quote_escape[0]] == [
        '[["id","signed_integer","1"],["text","string","hello, world"]]',
        '[["id","signed_integer","2"],["text","string","say \\"hello\\""]]',
    ]


def test_csv_equal_quote_escape_preserves_quotes_in_unquoted_field(tmp_path):
    path = tmp_path / "unquoted-quote.csv"
    path.write_text('value\nx""y\n')
    schema = CanonicalSchema([["value", "string", False, {}]])

    rows, schema_digest, merkle_root = _csv_commitment(path, schema, '"')

    assert [row.decode() for row in rows] == [
        '[["value","string","x\\"\\"y"]]'
    ]
    assert schema_digest.hex() == (
        "825399d361e38fb1b2d104f84f32255426953977f5a7e20f3a5c8597310d0e9f"
    )
    assert merkle_root.hex() == (
        "d4a04191a9ec02aafd107ca120deabb3f8966b3cb548eff69ba09c5ac7236649"
    )


def test_csv_distinct_escape_character_is_honoured(tmp_path):
    path = tmp_path / "escaped.csv"
    path.write_text('id,text\n1,"say \\"hello\\""\n')
    schema = CanonicalSchema(
        [["id", "signed_integer", False, {}], ["text", "string", False, {}]]
    )

    rows, _, _ = _csv_commitment(path, schema, "\\")

    assert json.loads(rows[0]) == [
        ["id", "signed_integer", "1"],
        ["text", "string", 'say "hello"'],
    ]


def test_csv_parse_failure_is_actionable(tmp_path):
    schema = CanonicalSchema([["value", "string", False, {}]])
    declaration = ParsingDeclaration(
        "csv",
        encoding="utf-8",
        delimiter=",",
        quote='"',
        escape='"',
        header=False,
        locale="C",
        null_token="",
    )
    declaration.validate()  # Equal quote/escape is explicitly within the contract.

    malformed = tmp_path / "malformed.csv"
    malformed.write_text('"unterminated\n')
    declaration = ParsingDeclaration(
        "csv",
        encoding="utf-8",
        delimiter=",",
        quote='"',
        escape="",
        header=False,
        locale="C",
        null_token="",
    )
    with pytest.raises(Error, match="^csv_parse_error$") as failure:
        list(iter_records(malformed, declaration, schema))
    assert "line 1" in failure.value.safe_message
    assert "unterminated" not in failure.value.safe_message


def test_source_encoding_and_io_failures_are_distinct_and_safe(tmp_path):
    schema = CanonicalSchema([["value", "string", False, {}]])
    declaration = ParsingDeclaration(
        "csv",
        encoding="utf-8",
        delimiter=",",
        quote='"',
        escape="",
        header=False,
        locale="C",
        null_token="",
    )
    encoded = tmp_path / "encoded.csv"
    encoded.write_bytes(b"valid\nsecret-\xff\n")
    with pytest.raises(Error, match="^source_encoding_error$") as failure:
        list(iter_records(encoded, declaration, schema))
    assert "line 2" in failure.value.safe_message
    assert "secret" not in failure.value.safe_message

    missing = tmp_path / "missing.csv"
    with pytest.raises(Error, match="^invalid_source$") as failure:
        list(iter_records(missing, declaration, schema))
    assert str(missing) not in failure.value.safe_message


def test_parquet_exact_nanos_decimal_binary_nested(tmp_path):
    import pyarrow as pa
    import pyarrow.parquet as pq

    fields = [
        ["b", "binary", True, {}],
        ["d", "decimal", True, {"precision": 38, "scale": 9}],
        ["t", "timestamp", True, {"timestamp_precision": 9}],
        [
            "a",
            "array",
            True,
            {"element_type": {"type": "signed_integer", "type_parameters": {}}},
        ],
    ]
    arrow_schema = pa.schema(
        [
            pa.field("b", pa.binary()),
            pa.field("d", pa.decimal128(38, 9)),
            pa.field("t", pa.timestamp("ns", tz="UTC")),
            pa.field("a", pa.list_(pa.int64())),
        ]
    )
    table = pa.Table.from_arrays(
        [
            pa.array([b"\x00\xff"]),
            pa.array(
                [Decimal("12345678901234567890123456789.123456789")],
                type=pa.decimal128(38, 9),
            ),
            pa.array([1784637296123456789], type=pa.timestamp("ns", tz="UTC")),
            pa.array([[1, None, 2]], type=pa.list_(pa.int64())),
        ],
        schema=arrow_schema,
    )
    path = tmp_path / "data.parquet"
    pq.write_table(table, path)
    schema = CanonicalSchema(fields)
    record = list(iter_records(path, ParsingDeclaration("parquet"), schema))[0]
    assert isinstance(record["b"], bytes) and isinstance(record["d"], Decimal)
    values = {
        name: value for name, tag, value in json.loads(schema.canonical_row(record))
    }
    assert (
        values["b"] == "AP8"
        and values["d"] == "12345678901234567890123456789.123456789"
    )
    assert values["t"].endswith(".123456789Z") and values["a"] == ["1", None, "2"]


@pytest.mark.parametrize("kind", ["float", "map", "fixed_list"])
def test_parquet_rejected_types(tmp_path, kind):
    import pyarrow as pa
    import pyarrow.parquet as pq

    typ = {
        "float": pa.float64(),
        "map": pa.map_(pa.string(), pa.string()),
        "fixed_list": pa.list_(pa.int64(), 2),
    }[kind]
    path = tmp_path / "unsupported.parquet"
    pq.write_table(
        pa.Table.from_arrays([pa.array([None], type=typ)], names=["x"]), path
    )
    with pytest.raises(Error, match="unsupported_logical_type"):
        list(
            iter_records(
                path,
                ParsingDeclaration("parquet"),
                CanonicalSchema([["x", "string", True, {}]]),
            )
        )


def test_duckdb_pinned_runtime_dispatch():
    import duckdb

    connection = duckdb.connect(":memory:")
    try:
        connection.execute("SET memory_limit='128MB'")
        for sql_type, tag in [
            ("BOOLEAN", "boolean"),
            ("TINYINT", "signed_integer"),
            ("UTINYINT", "signed_integer"),
            ("HUGEINT", "signed_integer"),
            ("DECIMAL(38,9)", "decimal"),
            ("VARCHAR", "string"),
            ("DATE", "date"),
            ("TIMESTAMP_NS", "timestamp"),
            ("TIMESTAMPTZ", "timestamp"),
            ("BLOB", "binary"),
            ("INTEGER[]", "array"),
        ]:
            resolved = connection.execute(
                "DESCRIBE SELECT CAST(NULL AS " + sql_type + ") AS v"
            ).fetchone()[1]
            assert dispatch_type(resolved, source_timezone="Z")[0] == tag
        for sql_type in ["FLOAT", "DOUBLE", "UUID", "JSON", "TIME", "INTERVAL", "BIT"]:
            resolved = connection.execute(
                "DESCRIBE SELECT CAST(NULL AS " + sql_type + ") AS v"
            ).fetchone()[1]
            with pytest.raises(Error, match="unsupported_logical_type"):
                dispatch_type(resolved)
    finally:
        connection.close()


@pytest.mark.parametrize("size", [9999, 10000, 10001])
def test_value_nodes_boundary(size):
    schema = CanonicalSchema(
        [
            [
                "a",
                "array",
                True,
                {"element_type": {"type": "string", "type_parameters": {}}},
            ]
        ]
    )
    record = {"a": [None] * (size - 1)}
    if size > 10000:
        with pytest.raises(Error, match="node_limit"):
            schema.canonical_row(record)
    else:
        assert len(json.loads(schema.canonical_row(record))[0][2]) == size - 1


@pytest.mark.parametrize("depth", [15, 16, 17])
def test_nested_dictionary_depth(depth):
    field = {"name": "x", "type": "string", "nullable": True, "type_parameters": {}}
    for _ in range(depth):
        field = {
            "name": "x",
            "type": "object",
            "nullable": True,
            "type_parameters": {"object_fields": [field]},
        }
    descriptors = [
        [field["name"], field["type"], field["nullable"], field["type_parameters"]]
    ]
    if depth == 17:
        with pytest.raises(Error, match="depth_limit"):
            CanonicalSchema(descriptors)
    else:
        assert CanonicalSchema(descriptors).nodes == depth + 1


@pytest.mark.parametrize("count", [499, 500, 501])
def test_nested_dictionary_fields(count):
    fields = [
        {"name": f"x{i}", "type": "string", "nullable": True, "type_parameters": {}}
        for i in range(count)
    ]
    if count == 501:
        with pytest.raises(Error, match="field_limit"):
            CanonicalSchema([["o", "object", True, {"object_fields": fields}]])
    else:
        assert (
            CanonicalSchema([["o", "object", True, {"object_fields": fields}]]).nodes
            == count + 1
        )


def test_large_integer_row_string():
    value = "9" * 5000
    assert (
        value
        in CanonicalSchema([["x", "signed_integer", False, {}]])
        .canonical_row({"x": value})
        .decode()
    )


def test_csv_record_limit_before_accumulation(tmp_path):
    from app.services.dataset_canonicalization import MAX_RECORD_BYTES

    path = tmp_path / "wide.csv"
    path.write_bytes(b'"' + b"x" * (MAX_RECORD_BYTES) + b'"\n')
    declaration = ParsingDeclaration(
        "csv",
        encoding="utf-8",
        delimiter=",",
        quote='"',
        escape="",
        header=False,
        locale="C",
        null_token="NULL",
    )
    with pytest.raises(Error, match="record_resource_limit"):
        list(
            iter_records(
                path, declaration, CanonicalSchema([["x", "string", False, {}]])
            )
        )


def test_runtime_struct_declared_nullability():
    tag, params = dispatch_type(
        "STRUCT(x INTEGER, y VARCHAR)",
        members=[
            {"name": "x", "physical": "INTEGER", "nullable": False},
            {"name": "y", "physical": "VARCHAR", "nullable": True},
        ],
    )
    assert tag == "object"
    assert params["object_fields"][0]["nullable"] is False
    with pytest.raises(Error, match="schema_mismatch"):
        dispatch_type(
            "STRUCT(x INTEGER)",
            members=[{"name": "x", "physical": "VARCHAR", "nullable": False}],
        )


@pytest.mark.parametrize("length", [0, 1, 255, 256])
def test_field_name_contract(length):
    if length in {0, 256}:
        with pytest.raises(Error, match="invalid_field_name"):
            CanonicalSchema([["x" * length, "string", True, {}]])
    else:
        assert CanonicalSchema([["x" * length, "string", True, {}]])


def test_declared_decimal_contract_precision():
    schema = CanonicalSchema(
        [["x", "decimal", False, {"precision": 1000, "scale": 500}]]
    )
    value = "9" * 500 + "." + "1" * 500
    assert value in schema.canonical_row({"x": value}).decode()
    with pytest.raises(Error, match="invalid_decimal_parameters"):
        dispatch_type("DECIMAL(1000,500)")


@pytest.mark.parametrize("value", [12, "12", Decimal("12")])
def test_integral_decimal_identical_bytes(value):
    schema = CanonicalSchema(
        [["amount", "decimal", False, {"precision": 4, "scale": 2}]]
    )
    assert schema.canonical_row({"amount": value}) == b'[["amount","decimal","12"]]'


@pytest.mark.parametrize(
    "value,code",
    [
        (True, "invalid_decimal"),
        (12.0, "invalid_decimal"),
        ("bad", "invalid_decimal"),
        (Decimal("NaN"), "invalid_decimal"),
        (100, "decimal_out_of_range"),
        ("100", "decimal_out_of_range"),
        (Decimal("100"), "decimal_out_of_range"),
        ("12.001", "decimal_out_of_range"),
    ],
)
def test_integral_decimal_stable_rejections(value, code):
    schema = CanonicalSchema(
        [["amount", "decimal", False, {"precision": 4, "scale": 2}]]
    )
    with pytest.raises(Error, match="^" + code + "$"):
        schema.canonical_row({"amount": value})


@pytest.mark.parametrize("nested", [False, True])
@pytest.mark.parametrize("has_null", [False, True])
def test_parquet_nullable_arrow_strict_declaration_streamed(tmp_path, nested, has_null):
    import pyarrow as pa
    import pyarrow.parquet as pq

    values = [12, None if has_null else 13]
    fields = [["x", "signed_integer", False, {}]]
    typ = pa.int64()
    if nested:
        typ = pa.list_(pa.struct([pa.field("x", pa.int64(), nullable=True)]))
        values = [[{"x": value}] for value in values]
        fields = [
            [
                "x",
                "array",
                False,
                {
                    "element_type": {
                        "type": "object",
                        "type_parameters": {
                            "object_fields": [
                                {
                                    "name": "x",
                                    "type": "signed_integer",
                                    "nullable": False,
                                    "type_parameters": {},
                                }
                            ]
                        },
                    }
                },
            ]
        ]
    path = tmp_path / "nullable.parquet"
    pq.write_table(pa.table({"x": pa.array(values, type=typ)}), path, row_group_size=1)
    records = iter_records(path, ParsingDeclaration("parquet"), CanonicalSchema(fields))
    assert next(records) == {"x": values[0]}
    if has_null:
        with pytest.raises(Error, match="^nullability_violation$"):
            next(records)
    else:
        assert list(records) == [{"x": values[1]}]
