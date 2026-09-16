"""Produce a per-test comparison without reflecting test input or traceback data."""

import collections
import json
import sys
import xml.etree.ElementTree as ET
from pathlib import Path


def outcomes(path):
    result = {}
    for case in ET.parse(path).iter("testcase"):
        name = case.get("classname", "") + "::" + case.get("name", "")
        result[name] = next(
            (
                kind
                for kind in ("failure", "error", "skipped")
                if case.find(kind) is not None
            ),
            "passed",
        )
    return result


def main():
    baseline, candidate = map(outcomes, sys.argv[1:3])
    added = {key: val for key, val in candidate.items() if key not in baseline}
    changes = {
        key: (val, candidate.get(key, "MISSING"))
        for key, val in baseline.items()
        if candidate.get(key) != val
    }
    report = {
        "baseline": dict(collections.Counter(baseline.values())),
        "candidate": dict(collections.Counter(candidate.values())),
        "added": dict(collections.Counter(added.values())),
        "changed_existing": changes,
        "branch_only_failures": [
            key
            for key, val in candidate.items()
            if val in ("failure", "error")
            and baseline.get(key) not in ("failure", "error")
        ],
    }
    print(json.dumps(report, indent=2))
    output = Path(sys.argv[3])
    lines = [
        "# Complete backend-independent pytest parity",
        "",
        "| Test | origin/main | Candidate |",
        "|---|---|---|",
    ]
    lines += [
        "| `"
        + key.replace("|", "\\|")
        + "` | "
        + baseline.get(key, "NEW")
        + " | "
        + candidate.get(key, "MISSING")
        + " |"
        for key in sorted(baseline.keys() | candidate.keys())
    ]
    output.write_text("\n".join(lines) + "\n")
    if changes or any(v != "passed" for v in added.values()):
        raise SystemExit(1)


if __name__ == "__main__":
    main()
