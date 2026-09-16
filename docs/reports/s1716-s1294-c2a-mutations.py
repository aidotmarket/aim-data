"""Execute the golden assertion against two deliberately broken primitives."""

import json
import subprocess
import sys

mutations = {
    "leaf_domain_byte": ('b"\\x00aim-leaf-v1\\0"', 'b"\\x01aim-leaf-v1\\0"'),
    "ordinal_encoding": (
        'duplicate_ordinal.to_bytes(8, "big")',
        'duplicate_ordinal.to_bytes(8, "little")',
    ),
}
results = {}
for name, (before, after) in mutations.items():
    program = f"""
import pathlib, pytest
from app.services import dataset_merkle_service as m
source=pathlib.Path(m.__file__).read_text()
assert {before!r} in source
exec(compile(source.replace({before!r},{after!r},1),m.__file__,'exec'),m.__dict__)
raise SystemExit(pytest.main(['tests/test_dataset_merkle_service.py::test_golden_byte_exact','-q','--tb=short']))
"""
    run = subprocess.run(
        ["rtk", "proxy", sys.executable, "-c", program], capture_output=True, text=True
    )
    assert run.returncode == 1 and "1 failed" in run.stdout, (name, run.returncode)
    results[name] = {
        "exit_code": run.returncode,
        "golden_test_failed": True,
        "output": run.stdout,
    }
print(json.dumps(results, indent=2))
