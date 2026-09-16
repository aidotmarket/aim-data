"""Standalone pre-build contract gate; exit 1 reproduces the approved release stop.

Read only four pinned backend modules, without importing its application. This
is deliberately outside pytest collection: it exposes an unresolved contract
conflict, not a skipped/xfail producer test. All data is synthetic. No networking.
Run with a Python environment containing Pydantic v2 and cryptography, plus Node.
"""

from __future__ import annotations

import argparse
import hashlib
import json
from pathlib import Path
import subprocess
import sys
import types
import unittest


BACKEND_SHA = "9b6f8c1d590116ffe2792696937a63821336e00c"
FIXTURE_SHA = "f3e358d1e7ce7c836ce8604810675e0952201a7799b92d30858cd489906499af"
ROOT = Path(__file__).resolve().parents[2]
JCS_SUBSET = r"""
function canonical(v) {
  if (v === null || typeof v !== 'object') return JSON.stringify(v);
  if (Array.isArray(v)) return '[' + v.map(canonical).join(',') + ']';
  return '{' + Object.keys(v).sort().map(
    k => JSON.stringify(k) + ':' + canonical(v[k])
  ).join(',') + '}';
}
let input = '';
process.stdin.setEncoding('utf8');
process.stdin.on('data', c => input += c);
process.stdin.on('end', () => process.stdout.write(canonical(JSON.parse(input))));
"""


def pinned_module(repo: str, name: str) -> types.ModuleType:
    source_path = name.replace(".", "/") + ".py"
    source = subprocess.check_output(
        ["rtk", "proxy", "git", "-C", repo, "show", f"{BACKEND_SHA}:{source_path}"]
    )
    module = types.ModuleType(name)
    sys.modules[name] = module
    exec(compile(source, f"{BACKEND_SHA}:{source_path}", "exec"), module.__dict__)
    return module


def js_bytes(value: object) -> bytes:
    return subprocess.check_output(
        ["rtk", "proxy", "node", "-e", JCS_SUBSET],
        input=json.dumps(value, ensure_ascii=False).encode("utf-8"),
    )


def main() -> int:
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument("--backend", required=True)
    parser.add_argument("--write-fixture", action="store_true")
    args = parser.parse_args()
    # Fresh package namespaces: never import backend app startup/config/DB.
    for name in ("app", "app.schemas", "app.utils"):
        module = types.ModuleType(name)
        module.__path__ = []
        sys.modules[name] = module
    pinned_module(args.backend, "app.schemas.listing_enrichment")
    pinned_module(args.backend, "app.schemas.preview_package_profile")
    reference = pinned_module(args.backend, "app.utils.dataset_commitment")
    contracts = pinned_module(args.backend, "app.schemas.dataset_commitment")
    fixture_path = ROOT / "tests/fixtures/aim_dataset_serialization_conflict_s1716.json"

    commitment = {
        "commitment_id": "00000000-0000-0000-0000-000000000001",
        "listing_id": "10000000-0000-0000-0000-000000000001",
        "seller_dataset_version": "synthetic-conformance",
        "previous_commitment_id": None,
        "canonicalization_profile": "aim-dataset-merkle-v1",
        "hash_algorithm": "sha-256",
        "schema_digest": reference.encode_base64url(bytes(32)),
        "dataset_merkle_root": reference.encode_base64url(bytes(32)),
        "leaf_count": 2**53 + 1,
        "seller_attestation_digest": reference.encode_base64url(bytes(32)),
        "aim_data_signer_reference": "00000000-0000-0000-0000-000000000001:" + "0" * 64,
        "signature_algorithm": "ed25519",
        "seller_signature": reference.encode_base64url(bytes(64)),
        "signed_at": "2026-09-16T00:00:00.000000Z",
        "proofs": [],
    }
    # Structural admission only. Zero hashes/signature are deliberate synthetic
    # placeholders, not a signed dataset, valid proof, registered key or tree.
    admitted = contracts.DatasetCommitmentContract.model_validate(commitment)
    assert admitted.leaf_count == 2**53 + 1
    preimage = {key: value for key, value in commitment.items() if key != "seller_signature"}
    cases = {
        "integer_metadata_fragment": {"leaf_count": 2**53 + 1},
        "commitment_signature_object": preimage,
        "generic_supplementary_keys": {"\ue000": "bmp", "\U00010000": "supplementary"},
    }
    evidence = {
        "backend_sha": BACKEND_SHA,
        "backend_contract_structurally_accepts": commitment,
        "note": "Synthetic schema admission only; no valid seller signature or real dataset.",
        "cases": {},
    }
    for name, value in cases.items():
        py = reference.canonical_json_bytes(value)
        js = js_bytes(value)
        evidence["cases"][name] = {
            "input": value,
            "backend_bytes_hex": py.hex(),
            "ecmascript_bytes_hex": js.hex(),
            "backend_sha256": hashlib.sha256(py).hexdigest(),
            "ecmascript_sha256": hashlib.sha256(js).hexdigest(),
            "equal": py == js,
        }
    if args.write_fixture:
        fixture_path.write_text(json.dumps(evidence, ensure_ascii=False, indent=2) + "\n")
    else:
        assert json.loads(fixture_path.read_text()) == evidence, "evidence drift"

    class ConformanceGate(unittest.TestCase):
        def test_original_fixture_sha(self):
            data = (ROOT / "tests/fixtures/aim_dataset_merkle_v1.json").read_bytes()
            self.assertEqual(hashlib.sha256(data).hexdigest(), FIXTURE_SHA)

        def test_safe_integer_metadata_control(self):
            for number in (2**53 - 1, 2**53):
                with self.subTest(number=number):
                    value = {"leaf_count": number}
                    self.assertEqual(reference.canonical_json_bytes(value), js_bytes(value))

        def test_exact_integer_row_string_control(self):
            value = [["id", "signed_integer", str(2**53 + 1)]]
            self.assertEqual(reference.canonical_json_bytes(value), js_bytes(value))

        def test_utf8_descriptor_array_order_control(self):
            names = sorted(["\U00010000", "\ue000"], key=lambda s: s.encode("utf-8"))
            self.assertEqual(names, ["\ue000", "\U00010000"])
            value = [[name, "string", False, {}] for name in names]
            self.assertEqual(reference.canonical_json_bytes(value), js_bytes(value))

        def test_admitted_integer_metadata_must_agree(self):
            value = cases["integer_metadata_fragment"]
            self.assertEqual(reference.canonical_json_bytes(value), js_bytes(value))

        def test_complete_commitment_signature_object_must_agree(self):
            self.assertEqual(reference.canonical_json_bytes(preimage), js_bytes(preimage))

        def test_generic_supplementary_object_key_order_must_agree(self):
            # Generic helper mismatch, NOT a closed descriptor-object witness.
            value = cases["generic_supplementary_keys"]
            self.assertEqual(reference.canonical_json_bytes(value), js_bytes(value))

    result = unittest.TextTestRunner(verbosity=2).run(
        unittest.defaultTestLoader.loadTestsFromTestCase(ConformanceGate)
    )
    return 0 if result.wasSuccessful() else 1


if __name__ == "__main__":
    raise SystemExit(main())
