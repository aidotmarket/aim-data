"""Recompute parity from exact-nodeid, outcome-only pytest receipts."""

import collections
import json
import sys
from pathlib import Path


def main():
    baseline, candidate = [json.loads(Path(p).read_text()) for p in sys.argv[1:3]]
    b, c = baseline["outcomes"], candidate["outcomes"]
    table = [
        {
            "nodeid": key,
            "baseline": b.get(key, "NEW"),
            "candidate": c.get(key, "MISSING"),
        }
        for key in sorted(b.keys() | c.keys())
    ]
    summary = {
        "baseline": dict(collections.Counter(b.values())),
        "candidate": dict(collections.Counter(c.values())),
        "added": dict(collections.Counter(c[k] for k in c.keys() - b.keys())),
        "changed_existing": [
            r
            for r in table
            if r["baseline"] != "NEW" and r["baseline"] != r["candidate"]
        ],
        "branch_only_failures": [
            r["nodeid"]
            for r in table
            if r["candidate"] in {"error", "failure"}
            and r["baseline"] not in {"error", "failure"}
        ],
        "collection_errors": {
            "baseline": baseline["collection_errors"],
            "candidate": candidate["collection_errors"],
        },
    }
    Path(sys.argv[3]).write_text(
        json.dumps({"summary": summary, "tests": table}, indent=2) + "\n"
    )
    print(json.dumps(summary, indent=2))
    if (
        summary["changed_existing"]
        or summary["branch_only_failures"]
        or any(summary["collection_errors"].values())
    ):
        raise SystemExit(1)


if __name__ == "__main__":
    main()
