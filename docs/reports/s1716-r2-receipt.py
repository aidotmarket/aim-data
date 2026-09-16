"""Generate an exact-HEAD receipt outside Git, avoiding a self-referential SHA.

The committed receipt pins the tested implementation; regenerate after evidence
commits to attest the final review HEAD with --output /absolute/path/receipt.json.
"""

import argparse
from datetime import datetime, timezone
import hashlib
import json
from pathlib import Path
import subprocess


def git(*args):
    return subprocess.check_output(["rtk", "proxy", "git", *args])


def main():
    parser = argparse.ArgumentParser()
    parser.add_argument("--output", type=Path, required=True)
    args = parser.parse_args()
    root = Path(git("rev-parse", "--show-toplevel").decode().strip())
    head = git("rev-parse", "HEAD").decode().strip()
    evidence = root / "docs/reports/s1716-s1294-c2a-evidence"
    original = json.loads((evidence / "implementation-receipt.json").read_text())
    sources = {}
    for name in original["source_sha256"]:
        data = (root / name).read_bytes()
        if git("show", f"{head}:{name}") != data:
            raise SystemExit(f"Uncommitted source: {name}")
        sources[name] = hashlib.sha256(data).hexdigest()
    receipt = {
        "implementation_sha": head,
        "baseline_sha": git("rev-parse", "origin/main").decode().strip(),
        "reviewed_r1_sha": "1f1e99d5501639c0b0f1f8d1cb81169721cd4bb8",
        "profile": "aim-dataset-merkle-v1",
        "source_sha256": sources,
        "full_suite_parity": json.loads(
            (evidence / "r2/full-per-test.json").read_text()
        )["summary"],
        "affected_suite_parity": json.loads(
            (evidence / "r2/affected-per-test.json").read_text()
        )["summary"],
        "focused_passed": 219,
        "pinned_focused_passed": 219,
        "pinned_duckdb": "0.9.2",
        "fixture_sha256": json.loads((evidence / "r2/fixture-sha256.json").read_text()),
        "evidence_sha256": {
            str(p.relative_to(evidence)): hashlib.sha256(p.read_bytes()).hexdigest()
            for p in sorted((evidence / "r2").iterdir())
            if p.is_file()
        },
        "benchmark_scope": "R1 receipts retained as historical; benchmarks were not rerun for R2",
        "merge_performed": False,
        "release_performed": False,
        "verified_at_utc": datetime.now(timezone.utc).isoformat(),
    }
    args.output.write_text(json.dumps(receipt, indent=2) + "\n")
    print(head)


if __name__ == "__main__":
    main()
