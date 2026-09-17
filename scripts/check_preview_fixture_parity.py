#!/usr/bin/env python3
"""Check committed preview bytes, without rewriting or blessing any fixture."""

import argparse
import hashlib
import json
from pathlib import Path
import re
import sys

ROOT = Path(__file__).resolve().parents[1]
REFERENCE = "tests/fixtures/aim_dataset_merkle_v1.json"
BACKEND_SHA256 = "f3e358d1e7ce7c836ce8604810675e0952201a7799b92d30858cd489906499af"


def check(root: Path, backend_fixture: Path | None = None) -> int:
    manifest = json.loads((root / "tests/fixtures/preview-fixture-manifest.json").read_bytes())
    if not isinstance(manifest, list) or not manifest:
        raise ValueError("invalid_manifest")
    paths = set()
    for entry in manifest:
        if set(entry) != {"path", "sha256", "profile", "purpose"}:
            raise ValueError("invalid_manifest_entry")
        name = entry["path"]
        if (
            not isinstance(name, str)
            or not name.startswith("tests/fixtures/")
            or Path(name).as_posix() != name
            or ".." in Path(name).parts
            or name in paths
            or not re.fullmatch(r"[0-9a-f]{64}", entry["sha256"])
            or not all(isinstance(entry[k], str) and entry[k] for k in ("profile", "purpose"))
        ):
            raise ValueError("invalid_manifest_entry")
        paths.add(name)
        path = root / name
        if path.is_symlink() or not path.resolve().is_relative_to(root.resolve()):
            raise ValueError("unsafe_fixture_path")
        if hashlib.sha256(path.read_bytes()).hexdigest() != entry["sha256"]:
            raise ValueError("fixture_drift:" + name)
    # New preview/commitment fixtures must not escape the guard by omission.
    required = {
        p.relative_to(root).as_posix()
        for pattern in ("aim_dataset_*", "aim_preview_*")
        for p in (root / "tests/fixtures").glob(pattern)
        if p.is_file()
    }
    if not required <= paths or REFERENCE not in paths:
        raise ValueError("manifest_missing_fixture")
    reference = (root / REFERENCE).read_bytes()
    if hashlib.sha256(reference).hexdigest() != BACKEND_SHA256:
        raise ValueError("backend_reference_pin_drift")
    if backend_fixture is not None and reference != backend_fixture.read_bytes():
        raise ValueError("backend_reference_bytes_differ")
    return len(paths)


def main() -> int:
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument("--backend-fixture", type=Path, help="Exact backend Git object exported to a file")
    args = parser.parse_args()
    try:
        count = check(ROOT, args.backend_fixture)
    except (ValueError, OSError, KeyError, TypeError):
        print("Preview fixture parity FAILED; inspect manifest and pinned objects.", file=sys.stderr)
        return 1
    print(f"Preview fixture parity passed: {count} pinned files; backend copy "
          + ("byte-equal." if args.backend_fixture else "not supplied (SHA pin checked)."))
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
