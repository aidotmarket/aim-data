"""Synthetic private-file worker acceptance. Does not read seller datasets."""

import hashlib
import json
import sys
import tempfile
import threading
import time
from pathlib import Path

from app.services.dataset_canonicalization import ParsingDeclaration
from app.services.dataset_merkle_service import (
    CommitmentValidationError,
    WorkerBudget,
    run_commitment_job,
)


def main():
    size = int(sys.argv[1]) if len(sys.argv) > 1 else 1_000_000
    source_hashes = {
        str(p): hashlib.sha256(p.read_bytes()).hexdigest()
        for p in [
            Path("app/services/dataset_canonicalization.py"),
            Path("app/services/dataset_merkle_service.py"),
        ]
    }
    with tempfile.TemporaryDirectory(prefix="s1716-c2a-bench-") as tmp:
        directory = Path(tmp).resolve()
        source = directory / "generated.ndjson"
        with source.open("w") as stream:
            for i in range(size):
                stream.write(json.dumps({"id": i % 777777, "name": "synthetic"}) + "\n")
        declaration = ParsingDeclaration("ndjson", encoding="utf-8")
        descriptors = [
            ["id", "signed_integer", False, {}],
            ["name", "string", False, {}],
        ]
        phases = []
        started = time.monotonic()
        result = run_commitment_job(
            [source],
            declaration,
            descriptors,
            directory / "jobs",
            indices=[0, size - 1],
            progress=lambda p: phases.append(p),
        )
        result["source_sha256"] = source_hashes
        result["elapsed_seconds"] = time.monotonic() - started
        result["generated_records"] = size
        result["phases"] = sorted({p["phase"] for p in phases})
        result["cleanup_success"] = not list((directory / "jobs").glob("job-*"))
        cancel = threading.Event()

        def stop(p):
            if p["phase"] == "sorting":
                cancel.set()

        try:
            run_commitment_job(
                [source],
                declaration,
                descriptors,
                directory / "jobs",
                budget=WorkerBudget(run_bytes=1024 * 1024),
                cancel=cancel,
                progress=stop,
            )
        except CommitmentValidationError as exc:
            result["cancel_reason"] = exc.code
        else:
            raise AssertionError("cancellation was ignored")
        result["cleanup_cancel"] = not list((directory / "jobs").glob("job-*"))
        assert result["cleanup_success"] and result["cleanup_cancel"]
        assert result["cancel_reason"] == "cancelled"
        assert result["incremental_rss_bytes"] <= 512 * 1024 * 1024
        print(json.dumps(result, indent=2))


if __name__ == "__main__":
    main()
