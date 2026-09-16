"""Actual DuckDBService adapter check, using generated local Parquet only."""

import json
import tempfile
from decimal import Decimal
from pathlib import Path
from types import GeneratorType

import pyarrow as pa
import pyarrow.parquet as pq

from app.services.dataset_canonicalization import CanonicalSchema, ParsingDeclaration
from app.services.duckdb_service import DuckDBService


def main():
    with tempfile.TemporaryDirectory(prefix="s1716-adapter-") as tmp:
        path = Path(tmp).resolve() / "generated.parquet"
        arrow_schema = pa.schema(
            [
                pa.field("i", pa.int64(), nullable=False),
                pa.field("d", pa.decimal128(30, 2), nullable=False),
                pa.field("b", pa.binary(), nullable=False),
            ]
        )
        records = [
            {
                "i": 9007199254740993 + i,
                "d": Decimal("1234567890123456789012345678.90"),
                "b": b"\0\xff",
            }
            for i in range(5001)
        ]
        pq.write_table(pa.Table.from_pylist(records, schema=arrow_schema), path)
        schema = CanonicalSchema(
            [
                ["i", "signed_integer", False, {}],
                ["d", "decimal", False, {"precision": 30, "scale": 2}],
                ["b", "binary", False, {}],
            ]
        )
        # This adapter intentionally requires no engine, cache or /data directory.
        service = DuckDBService.__new__(DuckDBService)
        iterator = service.iter_commitment_records(
            path, ParsingDeclaration("parquet"), schema
        )
        assert isinstance(iterator, GeneratorType)
        count = 0
        for count, record in enumerate(iterator, 1):
            assert type(record["i"]) is int and record["i"] == 9007199254740992 + count
            assert type(record["d"]) is Decimal and record["d"] == records[0]["d"]
            assert type(record["b"]) is bytes and record["b"] == b"\0\xff"
        assert count == 5001
        print(
            json.dumps(
                {
                    "records_read": count,
                    "streaming_generator": True,
                    "integer_exact": True,
                    "decimal_exact": True,
                    "binary_bytes": True,
                    "source": "generated_local_fixture",
                }
            )
        )


if __name__ == "__main__":
    main()
