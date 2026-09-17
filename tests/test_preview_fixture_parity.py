import importlib.util
import json
from pathlib import Path
import shutil

import pytest

ROOT = Path(__file__).resolve().parents[1]
spec = importlib.util.spec_from_file_location("fixture_parity", ROOT / "scripts/check_preview_fixture_parity.py")
assert spec and spec.loader
parity = importlib.util.module_from_spec(spec)
spec.loader.exec_module(parity)


def test_committed_manifest_and_backend_copy():
    assert parity.check(ROOT, ROOT / parity.REFERENCE) >= 14


@pytest.mark.parametrize("mutation", ["bytes", "missing", "omitted", "new", "backend", "duplicate", "escape"])
def test_drift_fails(tmp_path, mutation):
    dest = tmp_path / "tests/fixtures"
    shutil.copytree(ROOT / "tests/fixtures", dest)
    manifest_path = dest / "preview-fixture-manifest.json"
    manifest = json.loads(manifest_path.read_text())
    path = tmp_path / manifest[0]["path"]
    backend = tmp_path / "backend.json"
    backend.write_bytes((ROOT / parity.REFERENCE).read_bytes())
    if mutation == "bytes":
        path.write_bytes(path.read_bytes() + b" ")
    elif mutation == "missing":
        path.unlink()
    elif mutation == "omitted":
        manifest.pop(0)
    elif mutation == "new":
        (dest / "aim_preview_unpinned.json").write_text("{}")
    elif mutation == "backend":
        backend.write_bytes(backend.read_bytes() + b"\n")
    elif mutation == "duplicate":
        manifest.append(manifest[0])
    elif mutation == "escape":
        manifest[0]["path"] = "tests/fixtures/../../outside"
    manifest_path.write_text(json.dumps(manifest))
    with pytest.raises((ValueError, OSError)):
        parity.check(tmp_path, backend)
