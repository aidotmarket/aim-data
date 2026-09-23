"""AIM Data's isolated process for backend-owned preview contract vectors."""

import argparse
import base64
import hashlib
import importlib
import json
import os
from pathlib import Path
import re
import sys
from typing import get_args


def canonical(value):
    return (
        json.dumps(value, sort_keys=True, separators=(",", ":"), ensure_ascii=False)
        + "\n"
    ).encode()


def model_type(annotation):
    from pydantic import BaseModel

    if isinstance(annotation, type) and issubclass(annotation, BaseModel):
        return annotation
    for arg in get_args(annotation):
        found = model_type(arg)
        if found is not None:
            return found
    return None


def nested_values(annotation, value, path):
    model = model_type(annotation)
    if model is None:
        return []
    if isinstance(value, dict):
        return [(model, value, path)]
    if isinstance(value, list):
        return [
            (model, item, path + "/" + str(index))
            for index, item in enumerate(value)
            if isinstance(item, dict)
        ]
    return []


def observe(model, value, ident, path="", direct=True):
    if not isinstance(value, dict):
        return []
    token = {
        "DisclosureBinding": "shared.DisclosureBinding",
        "DatasetPreviewProofContract": "shared.PreviewProof",
        "DatasetCommitmentContract": "shared.PreviewCommitment",
        "PreviewDisclosureRequest": "shared.PreviewDisclosureRequest",
        "PlatformEnvelope": "shared.PlatformEnvelope",
    }.get(model.__name__, f"{model.__module__}.{model.__name__}")
    if model.__name__ in {"WithdrawalRequest", "RefreshRequest", "SupersessionRequest"}:
        token = "shared.PreviewDisclosureRequest"
    fields = []
    for name, field in sorted(model.model_fields.items()):
        entry = {"name": name, "present": name in value}
        if name in value:
            entry["value"] = value[name]
        fields.append(entry)
    output = [
        {
            "id": ident,
            "model": token,
            "input_path": path,
            "direct": direct,
            "fields": fields,
        }
    ]
    for name, field in model.model_fields.items():
        if name in value:
            for child, obj, child_path in nested_values(
                field.annotation, value[name], path + "/" + name
            ):
                output.extend(observe(child, obj, ident, child_path, False))
    return output


def error_rows(exc):
    from pydantic import ValidationError

    if not isinstance(exc, ValidationError):
        raise exc
    output = []
    for item in exc.errors():
        row = {"loc": list(item["loc"]), "type": item["type"]}
        if item["type"] == "value_error":
            error = item.get("ctx", {}).get("error")
            code = str(error) if error is not None else ""
            if not re.fullmatch(r"[a-z][a-z0-9_]*", code):
                raise ValueError("unrecognized_custom_code")
            row["code"] = code
        output.append(row)
    return output


def setup(root):
    root = Path(root).resolve(strict=True)
    os.chdir(root)
    sys.path[:] = [str(root)] + [path for path in sys.path if path != str(root)]
    app = importlib.import_module("app")
    if not Path(app.__file__).resolve().is_relative_to(root):
        raise ValueError("foreign_app_import")
    return root


def dispatch(row, value):
    from app.models.dataset_commitment_schemas import (
        DatasetPreviewProofContract,
        DatasetCommitmentContract,
    )
    from app.models.preview_disclosure_schemas import (
        DisclosureBinding,
        PreviewDisclosureRequest,
        WithdrawalRequest,
        RefreshRequest,
        SupersessionRequest,
        PlatformEnvelope,
    )
    from app.services.dataset_merkle_service import (
        canonical_json_bytes,
        encode_base64url,
    )
    from app.services.preview_signing_service import (
        LocalCandidate,
        construct_request,
        disclosure_bytes,
        platform_envelope_bytes,
        request_bytes,
    )

    op = row["operation"]
    if op in {"binding-model", "disclosure-preimage"}:
        model = DisclosureBinding
    elif op == "proof-model":
        model = DatasetPreviewProofContract
    elif op == "commitment-model":
        model = DatasetCommitmentContract
    elif op in {"platform-envelope-model", "platform-envelope-preimage"}:
        model = PlatformEnvelope
    elif op == "local-candidate":
        model = DisclosureBinding
    elif op in {"request-model", "request-bytes"}:
        model = next(
            (
                klass
                for prefix, klass in (
                    ("request-withdraw-", WithdrawalRequest),
                    ("request-refresh-", RefreshRequest),
                    ("request-supersede-", SupersessionRequest),
                )
                if row["id"].startswith(prefix)
            ),
            PreviewDisclosureRequest,
        )
    else:
        raise ValueError("unknown_operation")
    if "aim_data.construct_request" in row["models"]:
        if set(value) != {"candidate", "commitment", "proofs", "approved_p1"}:
            raise ValueError("request_envelope_keys")
        candidate = value["candidate"]
        fields = {
            "summary_id",
            "summary_approval_id",
            "summary_hash",
            "render_hash",
            "aggregate_hash",
            "content_revision",
            "source_revision",
            "listing_id",
            "listing_version_id",
        }
        if (
            not isinstance(value["approved_p1"], dict)
            or set(value["approved_p1"]) != fields
            or any(value["approved_p1"][key] != candidate.get(key) for key in fields)
        ):
            raise ValueError("p1_reference_mismatch")
        from cryptography.hazmat.primitives.asymmetric.ed25519 import Ed25519PrivateKey
        from app.services.preview_signing_service import public_bytes, fingerprint

        key = Ed25519PrivateKey.from_private_bytes(
            hashlib.sha256(
                b"preview-contract-cross-repo-v1 synthetic signing seed"
            ).digest()
        )

        class Signer:
            def _keys(self):
                return key, key.public_key()

            def sign_disclosure(self, binding):
                return encode_base64url(key.sign(disclosure_bytes(binding)))

        raw = public_bytes(key.public_key())
        reference = candidate.get("signer_reference", "")
        if not reference.endswith(":" + fingerprint(raw)):
            raise ValueError("synthetic_signer_mismatch")
        request = construct_request(
            LocalCandidate.validate(candidate),
            value["commitment"],
            value["proofs"],
            signer=Signer(),
            approved_p1=value["approved_p1"],
        )
        return request_bytes(request), observe(
            DisclosureBinding, candidate, row["id"], "/candidate", False
        ) + observe(PreviewDisclosureRequest, request, row["id"], "", True)
    parsed = model.model_validate(value)
    observations = observe(model, value, row["id"])
    if op == "disclosure-preimage":
        return disclosure_bytes(value), observations
    if op == "platform-envelope-preimage":
        return platform_envelope_bytes(value), observations
    if op == "request-bytes":
        return request_bytes(value), observations
    if op == "local-candidate":
        return LocalCandidate.validate(value).binding_bytes, observations
    return canonical_json_bytes(parsed.model_dump(mode="json")), observations


def run(repo_root, corpus, output):
    corpus = Path(corpus).resolve(strict=True)
    output = Path(output).absolute()
    root = setup(repo_root)
    from scripts.preview_contract_gate import verify_manifest, git, digest, read_json

    manifest_digest, rows = verify_manifest(corpus)
    results, coverage = [], []
    for row in rows:
        value = read_json(Path(corpus) / row["input_path"])
        try:
            raw, observations = dispatch(row, value)
        except Exception as exc:
            results.append(
                {"id": row["id"], "status": "reject", "errors": error_rows(exc)}
            )
        else:
            results.append(
                {
                    "id": row["id"],
                    "status": "accept",
                    "bytes": base64.urlsafe_b64encode(raw).rstrip(b"=").decode(),
                    "byte_length": len(raw),
                    "sha256": digest(raw),
                }
            )
            coverage.extend(observations)
    payload = {
        "repo_sha": git("rev-parse", "HEAD", cwd=root),
        "manifest_sha256": manifest_digest,
        "results": results,
        "coverage_observations": sorted(
            coverage, key=lambda x: (x["id"], x["input_path"], x["model"])
        ),
    }
    Path(output).write_bytes(canonical(payload))
    return payload


if __name__ == "__main__":
    parser = argparse.ArgumentParser()
    parser.add_argument("--repo-root", required=True)
    parser.add_argument("--corpus", required=True)
    parser.add_argument("--output", required=True)
    args = parser.parse_args()
    run(args.repo_root, args.corpus, args.output)
