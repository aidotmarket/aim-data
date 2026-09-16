"""Outcome-only pytest plugin; preserve exact nodeids without tracebacks or inputs.

Load with -p s1716_r2_outcomes and set R2_OUTCOMES to the output JSON path.
"""

import json
import os
from pathlib import Path

_outcomes = {}
_collection_errors = []


def pytest_runtest_logreport(report):
    status = None
    if report.failed:
        status = "failure" if report.when == "call" else "error"
    elif report.skipped:
        status = "skipped"
    elif report.when == "call":
        status = "passed"
    rank = {None: -1, "passed": 0, "skipped": 1, "failure": 2, "error": 3}
    if rank[status] > rank[_outcomes.get(report.nodeid)]:
        _outcomes[report.nodeid] = status


def pytest_collectreport(report):
    if report.failed:
        _collection_errors.append(report.nodeid)


def pytest_sessionfinish(session, exitstatus):
    Path(os.environ["R2_OUTCOMES"]).write_text(
        json.dumps(
            {
                "exit_status": int(exitstatus),
                "collection_errors": sorted(_collection_errors),
                "outcomes": dict(sorted(_outcomes.items())),
            },
            indent=2,
        )
        + "\n"
    )
