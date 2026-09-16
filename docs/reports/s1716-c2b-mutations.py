"""Reproduce two killed source mutations in isolated copies; never edit checkout."""

import hashlib
import json
import os
from pathlib import Path
import shutil
import subprocess
import sys
import tempfile


def main():
    root = Path(__file__).resolve().parents[2]
    source = root / "app/services/preview_package_service.py"
    original = source.read_text()
    cases = [
        (
            "sample_hash_domain_byte",
            'b"aim-approved-sample-v1\\0"',
            'b"aim-approved-sample-v1\\1"',
            "tests/test_preview_package_service.py::test_sample_hash_vectors",
        ),
        (
            "cap_comparison_flipped",
            "amount > CAPS[kind]",
            "amount >= CAPS[kind]",
            "tests/test_preview_package_service.py::test_all_cap_comparisons",
        ),
    ]
    results = []
    with tempfile.TemporaryDirectory(prefix="s1716-c2b-mutants-") as directory:
        checkout = Path(directory)
        shutil.copytree(
            root / "app", checkout / "app", ignore=shutil.ignore_patterns("__pycache__")
        )
        (checkout / "tests/fixtures").mkdir(parents=True)
        for name in ["conftest.py", "test_preview_package_service.py"]:
            shutil.copy2(root / "tests" / name, checkout / "tests" / name)
        for name in [
            "aim_dataset_merkle_v1.json",
            "aim_preview_package_v2.json",
            "aim_preview_package_vectors_v1.json",
        ]:
            shutil.copy2(
                root / "tests/fixtures" / name, checkout / "tests/fixtures" / name
            )
        shutil.copy2(
            root / "docs/reports/s1716_r2_outcomes.py",
            checkout / "s1716_r2_outcomes.py",
        )
        target = checkout / "app/services/preview_package_service.py"
        for name, before, after, node in cases:
            assert original.count(before) == 1
            for mutant in [False, True]:
                target.write_text(
                    original.replace(before, after) if mutant else original
                )
                # Do not let timestamp-based .pyc caching mask a same-length mutation.
                shutil.rmtree(target.parent / "__pycache__", ignore_errors=True)
                output = checkout / "outcomes.json"
                env = dict(
                    os.environ, PYTHONPATH=str(checkout), R2_OUTCOMES=str(output)
                )
                run = subprocess.run(
                    [
                        "rtk",
                        "proxy",
                        sys.executable,
                        "-m",
                        "pytest",
                        node,
                        "-q",
                        "--tb=no",
                        "-p",
                        "s1716_r2_outcomes",
                    ],
                    cwd=checkout,
                    env=env,
                    capture_output=True,
                )
                outcomes = json.loads(output.read_text())
                failures = [
                    n for n, state in outcomes["outcomes"].items() if state == "failure"
                ]
                assert run.returncode == (1 if mutant else 0)
                assert bool(failures) == mutant
                results.append(
                    {
                        "mutation": name,
                        "mutated": mutant,
                        "source_sha256": hashlib.sha256(
                            target.read_bytes()
                        ).hexdigest(),
                        "exit_status": run.returncode,
                        "outcomes": outcomes["outcomes"],
                        "log_sha256": hashlib.sha256(
                            run.stdout + run.stderr
                        ).hexdigest(),
                    }
                )
    Path(sys.argv[1]).write_text(json.dumps(results, indent=2) + "\n")


if __name__ == "__main__":
    main()
