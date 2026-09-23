#!/usr/bin/env python3
"""Build seller-local, NON-RUNTIME producer evidence; never submit to ai.market.

The output contains a public row package. Keep the entire directory on the seller
machine; share only reviewed metadata/checksums. Existing keys and fresh registry
readback are required. Host/retirement checks are separate resumable operations.
"""

import argparse
from datetime import datetime, timedelta, timezone
import hashlib
import json
import os
from pathlib import Path
import sys
from uuid import UUID, uuid5
from typing import Any

ROOT = Path(__file__).resolve().parents[1]
sys.path.insert(0, str(ROOT))
# Keep module import light: spawned parser workers re-import the CLI entrypoint.
# Load scanner/signing services only in the parent operations below.


NOTICE = "PRODUCER-LOCAL NON-RUNTIME FIXTURE. No platform allocation, acceptance or verification."
NAMESPACE = UUID("0684eb10-2a2b-4c2e-8181-171600000001")


class FixtureReplay:
    """Synthetic contract oracle kept outside the AIM Data runtime."""

    def __init__(self):
        self.results = {}
        self.heads = {}

    def apply(self, request):
        from app.services.preview_lifecycle import LifecycleError
        from app.services.preview_signing_service import request_digest

        digest = request_digest(request)
        binding = request["binding"]
        key = (
            binding["seller_id"],
            binding["listing_id"],
            binding["request_id"],
        )
        if key in self.results:
            saved_digest, result = self.results[key]
            if saved_digest != digest:
                raise LifecycleError("409_request_id_conflict")
            return result
        owner_key = key[:2]
        if binding["expected_current_disclosure_id"] != self.heads.get(owner_key):
            raise LifecycleError("409_stale_expected_head")
        result = {
            "decision_id": binding["request_id"],
            "disclosure_version": binding["disclosure_version"],
            "decision": binding["decision"],
        }
        self.results[key] = (digest, result)
        self.heads[owner_key] = binding["disclosure_version"]
        return result

    def current(self, binding):
        return (
            self.heads.get((binding["seller_id"], binding["listing_id"]))
            == binding["disclosure_version"]
            and binding["decision"] == "approve"
            and binding["sample_decision"] == "approved"
        )


def write_json(path, value):
    from app.services.dataset_merkle_service import canonical_json_bytes

    path.write_bytes(canonical_json_bytes(value) + b"\n")
    path.chmod(0o600)


def file_sha(path):
    digest = hashlib.sha256()
    with path.open("rb") as stream:
        for block in iter(lambda: stream.read(1024 * 1024), b""):
            digest.update(block)
    return digest.hexdigest()


def manifest(output, *, state):
    files = []
    for path in sorted(output.rglob("*")):
        if (
            path.is_file()
            and path.name != "manifest.json"
            and ".private" not in path.relative_to(output).parts
        ):
            files.append(
                {
                    "path": path.relative_to(output).as_posix(),
                    "sha256": file_sha(path),
                    "purpose": "seller-origin rows; NEVER send to platform"
                    if "public" in path.relative_to(output).parts
                    else "local evidence; review before sharing",
                }
            )
    value = {
        "notice": NOTICE,
        "state": state,
        "files": files,
        "unverified": [
            "real owner-authorized registry provenance",
            "Sergey machine and real dataset consent",
            "RC/stable customer image",
            "live T allocation/submission/storage",
            "buyer/browser/platform zero-ingress",
        ],
    }
    if state != "retirement_verified":
        value["unverified"].append("live retirement receipts")
    if state == "awaiting_host_verification":
        value["unverified"].append("live HTTPS GET/OPTIONS receipts")
    write_json(output / "manifest.json", value)
    return value


def lifecycle_evidence(output, request, signer, p1):
    from app.services.preview_lifecycle import LifecycleError
    from app.services.dataset_merkle_service import canonical_rfc3339_utc
    from app.services.preview_signing_service import construct_request
    from app.services.preview_lifecycle import freshness
    from app.services.preview_lifecycle import refresh_candidate
    from app.services.preview_signing_service import request_bytes
    from app.services.preview_lifecycle import stale_at
    from app.services.preview_lifecycle import supersession_candidate
    from app.services.preview_lifecycle import withdrawal_candidate

    b = request["binding"]

    def uid(label):
        return str(uuid5(NAMESPACE, b["disclosure_version"] + label))

    now = datetime.fromisoformat(b["approved_at"].replace("Z", "+00:00"))
    revised = canonical_rfc3339_utc(now + timedelta(seconds=1))
    candidates = {
        "withdraw": withdrawal_candidate(
            b,
            disclosure_version=uid("withdraw"),
            request_id=uid("withdraw-request"),
            approved_at=revised,
        ),
        "refresh": refresh_candidate(
            b,
            disclosure_version=uid("refresh"),
            request_id=uid("refresh-request"),
            attested_at=revised,
            cadence_days=7,
        ),
        "supersede": supersession_candidate(
            b,
            dict(
                b,
                disclosure_version=uid("supersede"),
                request_id=uid("supersede-request"),
                approved_at=revised,
            ),
        ),
    }
    requests = {}
    for name, candidate in candidates.items():
        withdrawing = name == "withdraw"
        result = construct_request(
            candidate,
            None if withdrawing else request["commitment"],
            [] if withdrawing else request["proofs"],
            signer=signer,
            approved_p1=p1,
        )
        write_json(
            output / (name + "-candidate.json"),
            {"notice": NOTICE, "kind": candidate.kind, "request": result},
        )
        requests[name] = result
    outcomes = {}
    for name, revision in requests.items():
        replay = FixtureReplay()
        original = replay.apply(request)
        assert replay.apply(request) == original
        changed = json.loads(request_bytes(request))
        changed["binding"]["update_cadence_days"] = 99
        try:
            replay.apply(changed)
        except LifecycleError as error:
            conflict = str(error)
        else:
            raise ValueError("idempotency_guard_failed")
        replay.apply(revision)
        assert not replay.current(b)
        assert replay.apply(request) == original
        assert not replay.current(b)
        try:
            replay.apply(
                dict(
                    requests["refresh"],
                    binding=dict(
                        requests["refresh"]["binding"], request_id=uid(name + "-stale")
                    ),
                )
            )
        except LifecycleError as error:
            stale = str(error)
        else:
            raise ValueError("head_guard_failed")
        outcomes[name] = {
            "identical_retry": original,
            "changed_request": conflict,
            "stale_head": stale,
            "saved_package_restores_head": False,
        }
    thresholds = []
    for cadence in (None, 1, 4, 14, 45, 46):
        threshold = stale_at(b["approved_at"], cadence)
        for offset in (-1, 0, 1):
            at = threshold + timedelta(microseconds=offset)
            thresholds.append(
                {
                    "attested_at": b["approved_at"],
                    "cadence_days": cadence,
                    "now": canonical_rfc3339_utc(at),
                    "result": freshness(
                        attested_at=b["approved_at"], cadence_days=cadence, now=at
                    ),
                }
            )
        thresholds.append(
            {
                "fixture_policy_only": True,
                "policy_expires_at": canonical_rfc3339_utc(threshold),
                "result": freshness(
                    attested_at=b["approved_at"],
                    cadence_days=cadence,
                    now=threshold,
                    policy_expires_at=threshold,
                ),
            }
        )
    write_json(
        output / "lifecycle-results.json",
        {"notice": NOTICE, "transitions": outcomes, "freshness": thresholds},
    )


def build(
    *,
    dataset,
    declaration,
    origin,
    output,
    signer,
    registration_path,
    rights_text,
    rights_code,
    indices,
    fixture_time,
    confirmation,
    contract_manifest_sha256=None,
):
    from app.services.dataset_canonicalization import CanonicalSchema
    from app.services.preview_package_service import CommitmentPreviewBuilder
    from app.services.preview_signing_service import LocalCandidate
    from app.services.preview_package_service import MEDIA_TYPE
    from app.services.dataset_canonicalization import ParsingDeclaration
    from app.services.preview_package_service import PublicationStore
    from app.services.dataset_merkle_service import canonical_json_bytes
    from app.services.dataset_merkle_service import canonical_rfc3339_utc
    from app.services.preview_lifecycle import capture_rights
    from app.services.preview_signing_service import construct_request
    from app.services.preview_package_service import directory_fd
    from app.services.dataset_merkle_service import encode_base64url
    from app.services.preview_signing_service import manifest_budget
    from app.services.preview_signing_service import public_bytes
    from app.services.registration_service import read_preview_registration_evidence
    from app.services.dataset_merkle_service import run_commitment_job
    from app.services.preview_package_service import sample_hash
    from app.services.preview_content_policy import scan_attestation_digest
    from app.services.preview_signing_service import seller_attestation_digest
    from app.services.preview_origin_service import validate_url
    from app.services.preview_signing_service import verify_request

    if not confirmation:
        raise ValueError("explicit_consent_required")
    from scripts.preview_contract_gate import lock

    pinned_digest = lock("aim-data", ROOT / "preview-contract-backend.lock.json")[
        "manifest_sha256"
    ]
    if contract_manifest_sha256 != pinned_digest:
        raise ValueError("verified_contract_manifest_required")
    parsed = validate_url(origin)
    if parsed.path not in ("", "/"):
        raise ValueError("origin_must_be_root")
    if output.exists():
        raise ValueError("output_must_be_new")
    if set(declaration) != {"parsing", "schema_descriptors"}:
        raise ValueError("invalid_declaration")
    schema = CanonicalSchema(declaration["schema_descriptors"])
    parsing = ParsingDeclaration(**declaration["parsing"])
    parsing.validate()
    source_sha = file_sha(dataset)
    time = canonical_rfc3339_utc(fixture_time)
    seed = source_sha + time

    def uid(label):
        return str(uuid5(NAMESPACE, seed + label))

    rights = capture_rights(rights_text, rights_code, True)
    reference = (
        signer.signer_reference
    )  # Fail before any row export when authority is absent.
    with directory_fd(output, private=True):
        pass
    with directory_fd(output / ".private", private=True):
        pass
    store = PublicationStore(output / "public", output / ".private" / "publication")
    proof_ids = [uid("proof-" + str(index)) for index in indices]
    collected: dict[str, Any] = {}

    def review(tree, result):
        builder = CommitmentPreviewBuilder(tree, schema.descriptors)
        proofs = [
            dict(proof_id=pid, **tree.proof(index))
            for pid, index in zip(proof_ids, indices, strict=True)
        ]
        digest = sample_hash(proofs)
        url = origin.rstrip("/") + "/" + store.path(uid("disclosure"), digest)
        validate_url(url)
        package = builder.prepare(
            indices,
            proof_ids=proof_ids,
            commitment_id=uid("commitment"),
            disclosure_version=uid("disclosure"),
            scanned_at=time,
            rights_confirmed=True,
            public_preview_permission=True,
            restricted_content_confirmed=True,
            package_url=url,
            manifest_bytes=0,
        )
        if source_sha != file_sha(dataset):
            raise ValueError("source_changed")
        dummy = encode_base64url(bytes(64))
        c = dict(
            {k: v for k, v in result["commitment"].items() if k != "profile"},
            commitment_id=uid("commitment"),
            listing_id=uid("listing"),
            seller_dataset_version=source_sha,
            aim_data_signer_reference=reference,
            seller_signature=dummy,
            signed_at=time,
        )
        attestation = {
            k: c[k]
            for k in (
                "listing_id",
                "seller_dataset_version",
                "schema_digest",
                "dataset_merkle_root",
                "leaf_count",
                "signed_at",
            )
        }
        attestation.update(
            sample_hash=digest,
            rights_basis_digest=rights["rights_basis_digest"],
            public_preview_permission=True,
            metadata_accuracy_confirmed=True,
        )
        c["seller_attestation_digest"] = seller_attestation_digest(attestation)
        proofs = [
            dict(
                p,
                preview_package_url=url,
                package_media_type=MEDIA_TYPE,
                package_profile="aim-preview-package-v2",
                package_byte_ceiling=1048576,
                **package.scan,
                signer_reference=reference,
                signature_algorithm="ed25519",
                signature=dummy,
            )
            for p in proofs
        ]
        c["proofs"] = proofs
        proofs = [signer.sign_proof(c, p) for p in proofs]
        c["proofs"] = proofs
        c = signer.sign_commitment(c)
        metadata_digest = hashlib.sha256(
            canonical_json_bytes([NOTICE, source_sha])
        ).hexdigest()
        p1 = dict(
            summary_id=uid("summary"),
            summary_approval_id=uid("approval"),
            summary_hash=metadata_digest,
            render_hash=metadata_digest,
            aggregate_hash=metadata_digest,
            content_revision=uid("content"),
            source_revision=source_sha,
            listing_id=c["listing_id"],
            listing_version_id=None,
        )
        b = dict(
            profile="aim-preview-disclosure-v1",
            decision="approve",
            **p1,
            disclosure_version=uid("disclosure"),
            seller_id=signer.seller_id,
            selected_fields=[d[0] for d in schema.descriptors],
            preview_type="table",
            content_type="tabular",
            sample_decision="approved",
            sample_hash=digest,
            commitment_id=c["commitment_id"],
            schema_digest=c["schema_digest"],
            seller_dataset_version=source_sha,
            schema_descriptors=schema.descriptors,
            proof_ids=proof_ids,
            sampled_leaf_list_digest=package.scan["sampled_leaf_list_digest"],
            scan_attestation_digest=scan_attestation_digest(proofs),
            **rights,
            approved_by=signer.seller_id,
            approved_at=time,
            last_attested_by_seller_at=time,
            update_cadence_days=14,
            approval_expires_at=None,
            supersedes=None,
            request_id=uid("request"),
            expected_current_disclosure_id=None,
            signer_reference=reference,
            signature_algorithm="ed25519",
            signature_profile="aim-preview-disclosure-signature-v1",
        )
        candidate = LocalCandidate.validate(b)
        request = construct_request(candidate, c, proofs, signer=signer, approved_p1=p1)
        evidence = read_preview_registration_evidence(registration_path).model_dump(
            mode="json"
        )
        verify_request(
            request,
            evidence=evidence,
            raw_key=public_bytes(signer._keys()[1]),
            now=signer.clock(),
            max_age=signer.max_age,
        )
        publication = store.export(package)
        collected.update(
            request=request,
            p1=p1,
            url=url,
            publication=publication,
            attestation=attestation,
            registration=evidence,
            canonical_bytes=sum(
                len(schema.canonical_row(e["row"]))
                for e in json.loads(package.payload)["entries"]
            ),
        )

    run_commitment_job(
        [dataset],
        parsing,
        schema.descriptors,
        output / ".private" / "worker",
        local_review=review,
    )
    request = collected["request"]
    write_json(
        output / "approval-candidate.json",
        {"notice": NOTICE, "kind": "fixture_candidate", "request": request},
    )
    write_json(output / "schema-descriptors.json", schema.descriptors)
    write_json(output / "commitment.json", request["commitment"])
    write_json(output / "proofs.json", request["proofs"])
    write_json(output / "seller-attestation.json", collected["attestation"])
    write_json(
        output / "registration-readback.json",
        {
            "notice": "Locally supplied registry readback, not independently retrieved by this tool",
            "evidence": collected["registration"],
            "key_match_checked": True,
        },
    )
    write_json(
        output / "publication.json",
        dict(collected["publication"], url=collected["url"], notice=NOTICE),
    )
    write_json(
        output / "build-receipt.json",
        {
            "notice": NOTICE,
            "source_sha256": source_sha,
            "parser_options_sha256": hashlib.sha256(
                canonical_json_bytes(declaration["parsing"])
            ).hexdigest(),
            "rows": request["commitment"]["leaf_count"],
            "selected_rows": len(indices),
            "fields": len(schema.descriptors),
            "canonical_selected_bytes": collected["canonical_bytes"],
            "manifest_budget_bytes": manifest_budget(request),
            "contract_manifest_sha256": contract_manifest_sha256,
        },
    )
    expected_platform_fixture(output, request, signer)
    lifecycle_evidence(output, request, signer, collected["p1"])
    return manifest(output, state="awaiting_host_verification")


def expected_platform_fixture(output, request, signer):
    """Public deterministic TEST key, isolated from real platform trust/keys."""
    from app.services.dataset_merkle_service import canonical_log_entry_bytes
    from app.services.dataset_merkle_service import compute_log_leaf_hash
    from app.services.dataset_merkle_service import encode_base64url
    from app.services.preview_signing_service import public_bytes

    from cryptography.hazmat.primitives.asymmetric.ed25519 import Ed25519PrivateKey
    from app.services.preview_signing_service import (
        platform_envelope_bytes,
        commitment_bytes,
        proof_bytes,
        disclosure_bytes,
        fingerprint,
        verify_authenticated_request,
        verify_checkpoint,
        verify_log_evidence,
    )
    from app.services.dataset_merkle_service import checkpoint_signing_bytes

    fixture_key = Ed25519PrivateKey.from_private_bytes(
        hashlib.sha256(b"S1716 NON-RUNTIME PUBLIC SYNTHETIC PLATFORM TEST KEY").digest()
    )
    b = request["binding"]
    time = b["approved_at"]
    key_id = "NON-RUNTIME-s1716-fixture-platform"
    raw = public_bytes(signer._keys()[1])
    envelope = dict(
        profile="aim-preview-platform-envelope-v1",
        key_id=key_id,
        signature_algorithm="ed25519",
        binding=b,
        seller_signature=request["seller_signature"],
        signer_keys=[
            dict(
                key_id=signer.install_id,
                algorithm="ed25519",
                public_key=encode_base64url(raw),
                status="active",
                valid_from=time,
                fingerprint=fingerprint(raw),
            )
        ],
        signature=encode_base64url(bytes(64)),
    )
    envelope["signature"] = encode_base64url(
        fixture_key.sign(platform_envelope_bytes(envelope))
    )
    c = request["commitment"]
    entry = {k: v for k, v in c.items() if k != "proofs"}
    entry.update(appended_at=time, transparency_sequence=1)
    root = encode_base64url(compute_log_leaf_hash(canonical_log_entry_bytes(entry)))
    checkpoint = dict(
        log_id="NON-RUNTIME-fixture-log",
        tree_size=1,
        root_hash=root,
        checkpoint_at=time,
        key_id=key_id,
        public_key_algorithm="ed25519",
        signature=encode_base64url(bytes(64)),
    )
    cp_bytes = checkpoint_signing_bytes(checkpoint["log_id"], 1, root, time)
    checkpoint["signature"] = encode_base64url(fixture_key.sign(cp_bytes))
    log: dict[str, Any] = dict(
        entry=entry,
        inclusion_path=[],
        consistency_path=[],
        previous_tree_size=None,
        previous_root=None,
    )
    fixture_trust = {key_id: public_bytes(fixture_key.public_key())}
    verify_authenticated_request(
        envelope,
        request,
        fixture_trust,
        now=datetime.fromisoformat(time.replace("Z", "+00:00")),
    )
    verify_checkpoint(checkpoint, fixture_trust)
    verify_log_evidence(log, checkpoint, c)
    write_json(
        output / "expected-platform-records.json",
        dict(
            notice=NOTICE + " SIGNED WITH PUBLIC TEST KEY; NEVER TRUST IN RUNTIME.",
            fixture_public_key=encode_base64url(fixture_trust[key_id]),
            platform_envelope=envelope,
            checkpoint=checkpoint,
            log_evidence=log,
        ),
    )
    preimages = [
        ("commitment", commitment_bytes(c)),
        ("disclosure", disclosure_bytes(b)),
        ("platform-envelope", platform_envelope_bytes(envelope)),
        ("checkpoint", cp_bytes),
    ]
    preimages += [
        ("proof-" + str(i), proof_bytes(c, p)) for i, p in enumerate(request["proofs"])
    ]
    write_json(
        output / "signing-preimages.json",
        dict(
            notice=NOTICE,
            preimages=[
                dict(
                    profile=name,
                    hex=data.hex(),
                    sha256=hashlib.sha256(data).hexdigest(),
                )
                for name, data in preimages
            ],
        ),
    )


def check_host(output, *, retire=False):
    from app.services.preview_package_service import PublicationStore
    from app.services.preview_lifecycle import validate_retirement_receipts
    from app.services.preview_origin_service import verify_hosted_package

    publication = json.loads((output / "publication.json").read_bytes())
    if retire:
        store = PublicationStore(output / "public", output / ".private" / "publication")
        store.retire(publication["disclosure_version"], publication["sample_hash"])
        manifest(output, state="retirement_pending")
    receipts = verify_hosted_package(
        publication["url"],
        origin="https://ai.market",
        expected_sha256=publication["package_sha256"],
        expected_bytes=publication["byte_count"],
        retired=retire,
    )
    if retire:
        validate_retirement_receipts(
            receipts, url=publication["url"], origin="https://ai.market"
        )
    write_json(
        output / ("retirement-receipts.json" if retire else "hosting-receipts.json"),
        receipts,
    )
    return manifest(
        output, state="retirement_verified" if retire else "hosting_verified"
    )


def main():
    from app.core.crypto import DeviceCrypto
    from app.services.preview_signing_service import PreviewSigningService
    from app.services.dataset_merkle_service import canonical_rfc3339_utc
    from app.services.registration_service import read_preview_registration_evidence

    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument("action", choices=("build", "check-host", "retire"))
    parser.add_argument("--output", required=True, type=Path)
    parser.add_argument("--contract-corpus", type=Path)
    parser.add_argument("--dataset", type=Path)
    parser.add_argument(
        "--declaration", type=Path, help="JSON: parsing and schema_descriptors"
    )
    parser.add_argument("--origin", help="Authorized HTTPS seller origin, without path")
    parser.add_argument("--registration-evidence", type=Path)
    parser.add_argument("--keystore", type=Path)
    parser.add_argument(
        "--rights-file",
        type=Path,
        help="Private local rights prose; never copied to evidence",
    )
    parser.add_argument(
        "--rights-code",
        choices=("owner", "licensed", "public_domain", "other_authorized"),
    )
    parser.add_argument(
        "--leaf-indices", help="Explicit reviewed sorted leaf indices, comma separated"
    )
    parser.add_argument(
        "--confirm-public-preview",
        action="store_true",
        help="Confirm rights, complete-row public permission, metadata accuracy and restricted-content review",
    )
    parser.add_argument(
        "--fixture-time", default=canonical_rfc3339_utc(datetime.now(timezone.utc))
    )
    args = parser.parse_args()
    try:
        if args.action == "build":
            from scripts.preview_contract_gate import lock, verify_manifest

            if args.contract_corpus is None:
                raise ValueError("verified_contract_corpus_required")
            expected = lock("aim-data", ROOT / "preview-contract-backend.lock.json")
            contract_digest, _ = verify_manifest(
                args.contract_corpus, expected["manifest_sha256"]
            )
            if any(
                getattr(args, name) is None
                for name in (
                    "dataset",
                    "declaration",
                    "origin",
                    "registration_evidence",
                    "keystore",
                    "rights_file",
                    "rights_code",
                    "leaf_indices",
                )
            ):
                parser.error(
                    "build requires dataset, declaration, origin, registration evidence, keystore, rights and selected leaf indices"
                )
            evidence = read_preview_registration_evidence(args.registration_evidence)
            passphrase = os.environ.get("PREVIEW_KEYSTORE_PASSPHRASE")
            if not passphrase:
                raise ValueError("keystore_passphrase_required")
            signer = PreviewSigningService(
                DeviceCrypto(args.keystore, passphrase),
                install_id=evidence.install_id,
                seller_id=evidence.seller_id,
                evidence_reader=lambda: read_preview_registration_evidence(
                    args.registration_evidence
                ),
                evidence_max_age=timedelta(hours=1),
            )
            result = build(
                dataset=args.dataset,
                declaration=json.loads(args.declaration.read_bytes()),
                origin=args.origin,
                output=args.output,
                signer=signer,
                registration_path=args.registration_evidence,
                rights_text=args.rights_file.read_text(),
                rights_code=args.rights_code,
                indices=[int(i) for i in args.leaf_indices.split(",")],
                fixture_time=args.fixture_time,
                confirmation=args.confirm_public_preview,
                contract_manifest_sha256=contract_digest,
            )
        else:
            result = check_host(args.output, retire=args.action == "retire")
    except Exception:
        # Parser, filesystem, crypto and detector exceptions may contain private input.
        print(
            "Producer evidence failed; no runtime approval. Check local inputs and prerequisites.",
            file=sys.stderr,
        )
        return 1
    print(result["state"] + "; " + NOTICE)
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
