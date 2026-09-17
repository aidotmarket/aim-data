"""Synthetic CI only: deterministic test identity, real parser/scanner/HTTP origin.

No production key, owner registration, public DNS or TLS is claimed by this test.
Only checksum references are committed. No scanner monkeypatch is permitted.
"""

import argparse
from datetime import timedelta
import hashlib
import http.client
import json
from pathlib import Path
import sys
import tempfile
import threading
from unittest.mock import patch
from urllib.parse import urlsplit

ROOT = Path(__file__).resolve().parents[1]
sys.path.insert(0, str(ROOT))

MARKER = "unique synthetic cell marker aabbccdd"


def run(output):
    from scripts import build_preview_producer_evidence as evidence  # noqa: E402
    from app.config import settings  # noqa: E402
    from app.core.crypto import DeviceCrypto  # noqa: E402
    from cryptography.hazmat.primitives.asymmetric.x25519 import X25519PrivateKey  # noqa: E402
    from app.services.preview_origin_service import make_origin, capture_receipt  # noqa: E402
    from app.services.preview_package_service import PublicationStore  # noqa: E402
    from app.services.preview_signing_service import (  # noqa: E402
        PreviewSigningService,
        fingerprint,
        public_bytes,
    )  # noqa: E402
    from app.services.registration_service import read_preview_registration_evidence  # noqa: E402
    from tests.preview_fixture_factory import test_key, uid, NOW, STAMP  # noqa: E402

    with tempfile.TemporaryDirectory(prefix="producer-synthetic-") as temporary:
        private = Path(temporary).resolve()
        private.chmod(0o700)
        settings.data_directory = str(private / "data")
        dataset = private / "synthetic.ndjson"
        dataset.write_text(
            json.dumps({"crop": MARKER, "count": 1})
            + "\n"
            + json.dumps({"crop": "oats", "count": 2})
            + "\n"
        )
        declaration = {
            "parsing": {"format": "ndjson", "encoding": "utf-8", "locale": "C"},
            "schema_descriptors": [
                ["count", "signed_integer", False, {}],
                ["crop", "string", False, {}],
            ],
        }
        crypto = DeviceCrypto(
            str(private / "keystore.json"), "synthetic-only-test-passphrase"
        )
        key = test_key()
        x = X25519PrivateKey.from_private_bytes(bytes(range(32)))
        crypto._save_keys(key, key.public_key(), x, x.public_key())
        registration = private / "registration.json"
        evidence.write_json(
            registration,
            dict(
                install_id=uid(1),
                seller_id=uid(2),
                fingerprint=fingerprint(public_bytes(key.public_key())),
                status="active",
                observed_at=STAMP,
            ),
        )
        signer = PreviewSigningService(
            crypto,
            install_id=uid(1),
            seller_id=uid(2),
            evidence_reader=lambda: read_preview_registration_evidence(registration),
            evidence_max_age=timedelta(hours=1),
            clock=lambda: NOW,
        )
        evidence.build(
            dataset=dataset,
            declaration=declaration,
            origin="https://seller.example",
            output=output,
            signer=signer,
            registration_path=registration,
            rights_text="Synthetic producer evidence rights",
            rights_code="owner",
            indices=[0, 1],
            fixture_time=STAMP,
            confirmation=True,
        )
        store = PublicationStore(output / "public", output / ".private" / "publication")
        server = make_origin(store, origin="https://ai.market")
        thread = threading.Thread(target=server.serve_forever, daemon=True)
        thread.start()

        def local_receipts(
            url, *, origin, expected_sha256, expected_bytes, retired=False
        ):
            receipts = []
            for method in ("GET", "OPTIONS"):
                conn = http.client.HTTPConnection("127.0.0.1", server.server_port)
                try:
                    headers = {"Origin": origin}
                    if method == "OPTIONS":
                        headers["Access-Control-Request-Method"] = "GET"
                    conn.request(method, urlsplit(url).path, headers=headers)
                    response = conn.getresponse()
                    body = response.read()
                    if method == "GET" and not retired:
                        assert len(body) == expected_bytes
                        assert hashlib.sha256(body).hexdigest() == expected_sha256
                    if retired:
                        assert not body
                    receipts.append(
                        capture_receipt(
                            url,
                            method,
                            response.status,
                            response.headers,
                            captured_at=STAMP,
                            origin=origin,
                            retired=retired,
                        )
                    )
                finally:
                    conn.close()
            return receipts

        try:
            with patch(
                "app.services.preview_origin_service.verify_hosted_package",
                local_receipts,
            ):
                evidence.check_host(output)
                pub = json.loads((output / "publication.json").read_bytes())
                package_path = (
                    output
                    / "public"
                    / store.path(pub["disclosure_version"], pub["sample_hash"])
                )
                package_sha = evidence.file_sha(package_path)
                assert MARKER in package_path.read_text()
                evidence.check_host(output, retire=True)
                assert store.read(pub["disclosure_version"], pub["sample_hash"]) == (
                    410,
                    b"",
                )
        finally:
            server.shutdown()
            server.server_close()
            thread.join()
        evidence.write_json(
            output / "synthetic-provenance.json",
            {
                "synthetic": True,
                "notice": "CI ONLY: deterministic test key and registry readback; loopback HTTP receipts labelled with fixture HTTPS URL; no public DNS/TLS or real owner proof.",
                "retired_package_sha256": package_sha,
                "real_policy_engine": True,
            },
        )
        # Metadata outputs must never leak the unique row marker or local source path.
        for path in output.glob("*.json"):
            assert MARKER not in path.read_text()
            assert str(dataset) not in path.read_text()
            assert "Synthetic producer evidence rights" not in path.read_text()
        result = evidence.manifest(output, state="synthetic_retirement_verified")
        result["unverified"] += [
            "real HTTPS hosting and retirement",
            "real owner-bound registration",
        ]
        evidence.write_json(output / "manifest.json", result)
        return {
            "notice": "SYNTHETIC CI ONLY; not Gate-4 receipts",
            "retired_package_sha256": package_sha,
            "files": [
                {"path": f["path"], "sha256": f["sha256"]} for f in result["files"]
            ],
        }


def main():
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument("--output", required=True, type=Path)
    parser.add_argument("--reference", type=Path)
    parser.add_argument("--write-reference", type=Path)
    args = parser.parse_args()
    result = run(args.output)
    if args.reference:
        assert result == json.loads(args.reference.read_bytes()), (
            "synthetic bundle checksum drift"
        )
    if args.write_reference:
        args.write_reference.write_text(json.dumps(result, indent=2) + "\n")
    print(
        "Synthetic producer bundle and row-egress checks passed; no live owner/HTTPS proof."
    )


if __name__ == "__main__":
    main()
