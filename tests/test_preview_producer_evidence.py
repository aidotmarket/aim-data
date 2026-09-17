"""Producer evidence safety boundaries, with no marketplace or provider writes."""

import json
import os
from pathlib import Path
import subprocess
import sys

import pytest

from scripts import build_preview_producer_evidence as evidence


def test_spawn_entrypoints_do_not_load_parent_scanner():
    # spawn re-executes the entrypoint before starting the bounded parser worker.
    # Loading spaCy/PII here consumed the worker budget on the Linux CI runner.
    program = """
import runpy, sys
for path in ('scripts/build_preview_producer_evidence.py', 'tests/run_preview_producer_synthetic.py'):
    runpy.run_path(path, run_name='__mp_main__')
assert 'app.services.pii_service' not in sys.modules
assert 'spacy' not in sys.modules
"""
    subprocess.run([sys.executable, "-c", program], check=True, capture_output=True)


def test_consent_required_before_any_read_or_write(tmp_path):
    with pytest.raises(ValueError, match="explicit_consent_required"):
        evidence.build(
            dataset=None,
            declaration=None,
            origin=None,
            output=tmp_path / "out",
            signer=None,
            registration_path=None,
            rights_text=None,
            rights_code=None,
            indices=None,
            fixture_time=None,
            confirmation=False,
        )
    assert not (tmp_path / "out").exists()


def test_missing_authority_does_not_export(tmp_path):
    source = tmp_path / "source.ndjson"
    source.write_text('{"crop":"oats"}\n')

    class UnavailableSigner:
        @property
        def signer_reference(self):
            raise ValueError("signing_authority_unavailable")

    with pytest.raises(ValueError, match="signing_authority_unavailable"):
        evidence.build(
            dataset=source,
            declaration={
                "parsing": {"format": "ndjson", "encoding": "utf-8"},
                "schema_descriptors": [["crop", "string", False, {}]],
            },
            origin="https://seller.example",
            output=tmp_path / "out",
            signer=UnavailableSigner(),
            registration_path=None,
            rights_text="Private synthetic rights",
            rights_code="owner",
            indices=[0],
            fixture_time="2026-09-17T00:00:00.000000Z",
            confirmation=True,
        )
    assert not (tmp_path / "out").exists()


def test_cli_failure_does_not_reflect_private_inputs(tmp_path):
    marker = "synthetic_private_marker"
    source = tmp_path / marker
    source.write_text(marker)
    result = subprocess.run(
        [
            sys.executable,
            "scripts/build_preview_producer_evidence.py",
            "build",
            "--output",
            str(tmp_path / "out"),
            "--dataset",
            str(source),
            "--declaration",
            str(source),
            "--origin",
            "https://seller.example",
            "--registration-evidence",
            str(source),
            "--keystore",
            str(source),
            "--rights-file",
            str(source),
            "--rights-code",
            "owner",
            "--leaf-indices",
            "0",
            "--confirm-public-preview",
        ],
        capture_output=True,
        text=True,
        env=dict(os.environ, PYTHONPATH="."),
    )
    assert result.returncode == 1
    assert marker not in result.stdout + result.stderr
    assert not (tmp_path / "out").exists()


def test_manifest_excludes_private_state_and_hashes_package(tmp_path):
    (tmp_path / ".private").mkdir()
    (tmp_path / ".private" / "journal.sqlite").write_bytes(b"private")
    (tmp_path / "public").mkdir()
    (tmp_path / "public" / "package.json").write_bytes(b"row-package")
    result = evidence.manifest(tmp_path, state="awaiting_host_verification")
    assert [f["path"] for f in result["files"]] == ["public/package.json"]
    assert result["files"][0]["sha256"] == evidence.file_sha(
        tmp_path / "public/package.json"
    )
    assert "live HTTPS GET/OPTIONS receipts" in result["unverified"]


def test_failed_host_check_never_creates_receipt(tmp_path, monkeypatch):
    evidence.write_json(
        tmp_path / "publication.json",
        dict(url="https://seller.example/p", package_sha256="a" * 64, byte_count=1),
    )

    def unavailable(*args, **kwargs):
        raise ValueError("transport_failed")

    monkeypatch.setattr(
        "app.services.preview_origin_service.verify_hosted_package", unavailable
    )
    with pytest.raises(ValueError, match="transport_failed"):
        evidence.check_host(tmp_path)
    assert not (tmp_path / "hosting-receipts.json").exists()


def test_synthetic_reference_is_only_hashes():
    reference = json.loads(
        Path("tests/fixtures/preview-producer-synthetic-shas.json").read_bytes()
    )
    assert "SYNTHETIC" in reference["notice"]
    required = {
        "approval-candidate.json",
        "commitment.json",
        "proofs.json",
        "schema-descriptors.json",
        "registration-readback.json",
        "hosting-receipts.json",
        "retirement-receipts.json",
        "withdraw-candidate.json",
        "refresh-candidate.json",
        "supersede-candidate.json",
        "lifecycle-results.json",
        "expected-platform-records.json",
    }
    assert required <= {f["path"] for f in reference["files"]}
    assert all(
        set(f) == {"path", "sha256"} and len(f["sha256"]) == 64
        for f in reference["files"]
    )
