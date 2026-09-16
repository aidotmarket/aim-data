"""Observe failures for every added test using isolated, explicit source mutations.

Run with the repository test interpreter; output argument is an evidence JSON path.
"""

import concurrent.futures
import json
import os
import shutil
import subprocess
import tempfile
import sys
from pathlib import Path

ROOT = Path(__file__).resolve().parents[2]
PY = sys.executable
CASES = [
    (
        "strict_detector_bypass",
        "app/services/pii_service.py",
        "            detector_identity()",
        "            return",
    ),
    (
        "content_rules_bypass",
        "app/services/preview_content_policy.py",
        "    if len(text) > 500",
        "    return\n    if len(text) > 500",
    ),
    (
        "walk_bypass",
        "app/services/preview_content_policy.py",
        "    def walk(value, depth):",
        "    return\n    def walk(value, depth):",
    ),
    (
        "unknown_detector_accepted",
        "app/services/preview_content_policy.py",
        "if actual != DETECTOR_IDENTITY:",
        "if actual == DETECTOR_IDENTITY:",
    ),
    (
        "scan_digest_domain",
        "app/services/preview_content_policy.py",
        'b"aim-preview-scan-attestation-v1\\0"',
        'b"aim-preview-scan-attestation-v1\\1"',
    ),
    (
        "consent_bypass",
        "app/services/preview_content_policy.py",
        "rights_confirmed is not True",
        "False",
    ),
    (
        "cap_direction_flipped",
        "app/services/preview_package_service.py",
        "amount > CAPS[kind]",
        "amount < CAPS[kind]",
    ),
    (
        "package_validation_bypass",
        "app/services/preview_package_service.py",
        "    try:\n        if (\n            type(envelope)",
        '    return b"{}"\n    try:\n        if (\n            type(envelope)',
    ),
    (
        "value_budget_bypass",
        "app/services/preview_package_service.py",
        "    nodes = 0",
        "    return\n    nodes = 0",
    ),
    (
        "depth_limit_reduced",
        "app/services/preview_package_service.py",
        '"depth": 16',
        '"depth": 14',
    ),
    (
        "receipt_validation_bypass",
        "app/services/preview_origin_service.py",
        '    if method not in {"GET", "OPTIONS"}',
        '    return {}\n    if method not in {"GET", "OPTIONS"}',
    ),
    (
        "receipt_limit_removed",
        "app/services/preview_origin_service.py",
        "if len(encoded) > 8192:",
        "if False:",
    ),
    (
        "url_admission_bypass",
        "app/services/preview_origin_service.py",
        "        parsed = urlsplit(url)",
        "        return urlsplit(url)\n        parsed = urlsplit(url)",
    ),
    (
        "dns_admission_bypass",
        "app/services/preview_origin_service.py",
        "        addresses = list(dict.fromkeys(answer[4][0] for answer in answers))",
        '        return ["8.8.8.8"]\n        addresses = list(dict.fromkeys(answer[4][0] for answer in answers))',
    ),
    (
        "origin_body_removed",
        "app/services/preview_origin_service.py",
        "            if payload:",
        "            if False:",
    ),
    (
        "pin_verification_bypass",
        "app/services/preview_origin_service.py",
        '                raise OriginError("address_changed")',
        "                pass",
    ),
    (
        "source_grants_preview",
        "app/services/s3_publish_source_resolver.py",
        "        return False",
        "        return True",
    ),
    (
        "retirement_disabled",
        "app/services/preview_package_service.py",
        '            record["state"] = "retired"',
        '            record["state"] = "exported"',
    ),
    (
        "package_hash_mismatch_accepted",
        "app/services/preview_origin_service.py",
        '                        raise OriginError("package_mismatch")',
        "                        pass",
    ),
    (
        "public_permission_bypass",
        "app/services/preview_content_policy.py",
        "public_preview_permission is not True",
        "False",
    ),
    (
        "restricted_confirmation_bypass",
        "app/services/preview_content_policy.py",
        "restricted_content_confirmed is not True",
        "False",
    ),
    (
        "sampled_leaf_domain",
        "app/services/preview_content_policy.py",
        'b"aim-preview-sampled-leaves-v1\\0"',
        'b"aim-preview-sampled-leaves-v1\\1"',
    ),
    (
        "url_length_doubled",
        "app/services/preview_origin_service.py",
        "len(url) > 2048",
        "len(url) > 4096",
    ),
    (
        "url_control_allowed",
        "app/services/preview_origin_service.py",
        "ord(c) <= 32 or ord(c) == 127",
        "False",
    ),
    (
        "selection_order_bypass",
        "app/services/preview_package_service.py",
        "sorted(set(indices))",
        "list(indices)",
    ),
    (
        "journal_inside_public_allowed",
        "app/services/preview_package_service.py",
        "self.journal_root.resolve().is_relative_to(self.public_root.resolve())",
        "False",
    ),
    (
        "unscanned_prepared_allowed",
        "app/services/preview_package_service.py",
        '    def __init__(self, *args, **kwargs):\n        raise PackageError("approval_required")',
        "    def __init__(self, *args, **kwargs):\n        pass",
    ),
]


def run(case):
    name, file, before, after = case
    with tempfile.TemporaryDirectory(prefix="c2b-audit-") as temp:
        dest = Path(temp)
        shutil.copytree(
            ROOT / "app", dest / "app", ignore=shutil.ignore_patterns("__pycache__")
        )
        (dest / "tests/fixtures").mkdir(parents=True)
        for p in (ROOT / "tests").glob("test_preview_*py"):
            shutil.copy2(p, dest / "tests" / p.name)
        shutil.copy2(ROOT / "tests/conftest.py", dest / "tests/conftest.py")
        for p in (ROOT / "tests/fixtures").glob("aim_*.json"):
            shutil.copy2(p, dest / "tests/fixtures" / p.name)
        shutil.copy2(
            ROOT / "docs/reports/s1716_r2_outcomes.py", dest / "s1716_r2_outcomes.py"
        )
        target = dest / file
        s = target.read_text()
        if s.count(before) != (2 if name == "selection_order_bypass" else 1):
            return {
                "name": name,
                "not_run": "replacement cardinality",
                "count": s.count(before),
            }
        target.write_text(s.replace(before, after))
        env = dict(os.environ, PYTHONPATH=str(dest), R2_OUTCOMES=str(dest / "out.json"))
        try:
            result = subprocess.run(
                [
                    "rtk",
                    "proxy",
                    PY,
                    "-m",
                    "pytest",
                    "tests",
                    "-q",
                    "--tb=no",
                    "-p",
                    "s1716_r2_outcomes",
                ],
                cwd=dest,
                env=env,
                capture_output=True,
                timeout=50,
            )
        except subprocess.TimeoutExpired:
            return {"name": name, "not_run": "timeout"}
        if not (dest / "out.json").exists():
            return {"name": name, "not_run": "no outcomes", "exit": result.returncode}
        return {
            "name": name,
            "file": file,
            "before": before,
            "after": after,
            **json.loads((dest / "out.json").read_text()),
        }


with concurrent.futures.ThreadPoolExecutor(max_workers=3) as pool:
    results = list(pool.map(run, CASES))
Path(sys.argv[1]).write_text(json.dumps(results, indent=2) + "\n")
if any(
    r.get("not_run") or r.get("collection_errors") or r.get("exit_status") != 1
    for r in results
):
    raise SystemExit(1)
