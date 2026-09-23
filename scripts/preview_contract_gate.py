"""Fail-closed, standard-library integrity and transition gate for preview vectors."""

import argparse
import base64
import hashlib
import json
import os
from pathlib import Path
import re
import subprocess
import sys
from typing import get_args, get_origin, Literal
from enum import Enum

sys.path.insert(0, str(Path(__file__).resolve().parents[1]))

SHA40 = re.compile(r"[0-9a-f]{40}\Z")
SHA64 = re.compile(r"[0-9a-f]{64}\Z")
VECTOR_ID = re.compile(r"[a-z0-9]+(?:-[a-z0-9]+)*\Z")
ACCEPT = frozenset(
    """binding-legacy-approve-owner binding-legacy-withdraw
    binding-v1-approve-licensed binding-v1-withdraw
    binding-v2-approve-other-authorized binding-v2-approve-public-domain
    binding-v2-none binding-v2-withdraw
    commitment-no-proofs commitment-v1-previous-absent commitment-v2-previous-present
    envelope-model-legacy-open envelope-model-v1-all-statuses envelope-model-v2-bounded
    envelope-preimage-legacy envelope-preimage-v1 envelope-preimage-v2
    local-candidate-legacy local-candidate-v1 local-candidate-v2
    preimage-binding-legacy preimage-binding-v1 preimage-binding-v2
    proof-v1-policy-v1 proof-v2-policy-v2
    request-approve-v2 request-none
    request-refresh-legacy request-refresh-v1 request-refresh-v2
    request-supersede-legacy request-supersede-v1 request-supersede-v2
    request-withdraw-legacy request-withdraw-v1 request-withdraw-v2""".split()
)
REJECT = frozenset(
    """extra-binding extra-commitment extra-envelope extra-envelope-binding
    extra-proof extra-request local-candidate-extra
    literal-binding-aggregate-profile literal-binding-content-type
    literal-binding-decision literal-binding-preview-type literal-binding-profile
    literal-binding-rights-code literal-binding-sample-decision
    literal-binding-signature-algorithm literal-binding-signature-profile
    literal-commitment-canonicalization literal-commitment-hash
    literal-commitment-signature-algorithm literal-envelope-profile
    literal-envelope-signature-algorithm literal-proof-media-type
    literal-proof-package-profile literal-proof-scan-policy literal-proof-scan-verdict
    literal-proof-sibling-direction literal-proof-signature-algorithm
    literal-request-profile literal-signer-key-algorithm literal-signer-key-status
    package-mismatch-byte-ceiling package-mismatch-media-type
    package-mismatch-profile package-mismatch-scan-policy
    package-mismatch-scan-policy-version package-mismatch-scan-verdict
    package-mismatch-scanned-at package-mismatch-signer-reference
    package-mismatch-url proof-sampled-leaf-list-mismatch
    request-proof-v1-unsupported
    schema-binary-array schema-binary-object schema-binary-object-array
    schema-binary-top""".split()
)
TOKENS = {
    "binding-model": {"shared.DisclosureBinding"},
    "disclosure-preimage": {"shared.DisclosureBinding"},
    "proof-model": {"shared.PreviewProof"},
    "commitment-model": {"shared.PreviewCommitment"},
    "platform-envelope-model": {"shared.PlatformEnvelope"},
    "platform-envelope-preimage": {"shared.PlatformEnvelope"},
    "local-candidate": {"shared.LocalCandidate"},
}
PACKAGE_KEYS = (
    "preview_package_url",
    "package_media_type",
    "package_profile",
    "package_byte_ceiling",
    "scan_policy",
    "scan_policy_version",
    "scanned_at",
    "scan_verdict",
    "signer_reference",
)
DELETED = (
    "preview-contract-parity.yml",
    "check_preview_fixture_parity.py",
    "test_preview_fixture_parity.py",
    "preview-fixture-manifest.json",
    "preview_differential_corpus.py",
    "aim_preview_differential_v1.json",
    "aim_preview_differential_v1.sha256",
    "aim_preview_requests_v1.json",
    "aim_preview_signing_v1.json",
    "aim_preview_signing_requests_v1.sha256",
)


class GateError(ValueError):
    pass


def require(ok, code):
    if not ok:
        raise GateError(code)


def canonical(value):
    return (
        json.dumps(value, sort_keys=True, separators=(",", ":"), ensure_ascii=False)
        + "\n"
    ).encode()


def pairs_unique(pairs):
    result = {}
    for key, value in pairs:
        require(key not in result, "duplicate_key")
        result[key] = value
    return result


def read_json(path):
    raw = Path(path).read_bytes()
    value = json.loads(raw.decode("utf-8"), object_pairs_hook=pairs_unique)
    require(raw == canonical(value), "noncanonical_json")
    return value


def digest(raw):
    return hashlib.sha256(raw).hexdigest()


def valid_sha(value, length):
    pattern = SHA40 if length == 40 else SHA64
    return (
        isinstance(value, str)
        and bool(pattern.fullmatch(value))
        and value != "0" * length
    )


def lock(kind, file, github_output=None):
    value = read_json(file)
    names = (
        {"backend_sha", "manifest_sha256"}
        if kind == "aim-data"
        else {"aim_data_sha", "contract_anchor_sha", "manifest_sha256"}
    )
    require(isinstance(value, dict) and set(value) == names, "lock_keys")
    for key in names:
        require(
            valid_sha(value[key], 64 if key == "manifest_sha256" else 40),
            "lock_sha:" + key,
        )
    if github_output:
        with open(github_output, "a", encoding="utf-8") as output:
            for key in sorted(value):
                output.write(f"{key}={value[key]}\n")
    return value


def operation_tokens(row):
    op, ident = row["operation"], row["id"]
    if op in TOKENS:
        return TOKENS[op]
    if op in {"request-model", "request-bytes"}:
        for prefix, token in (
            ("request-withdraw-", "shared.WithdrawalRequest"),
            ("request-refresh-", "shared.RefreshRequest"),
            ("request-supersede-", "shared.SupersessionRequest"),
        ):
            if ident.startswith(prefix):
                return {token} if op == "request-bytes" else set()
        if ident == "request-approve-v2" and op == "request-bytes":
            return {"shared.PreviewDisclosureRequest", "aim_data.construct_request"}
        return {"shared.PreviewDisclosureRequest"}
    return set()


def safe_root(root):
    root = Path(root).absolute()
    require(root.is_dir() and not root.is_symlink(), "unsafe_root")
    for path in root.rglob("*"):
        require(not path.is_symlink(), "symlink_in_corpus")
    return root


def checked_path(root, relative, prefix, suffix):
    require(
        isinstance(relative, str) and relative.startswith(prefix + "/"), "invalid_path"
    )
    path = Path(relative)
    require(
        not path.is_absolute()
        and all(part not in {"", ".", ".."} for part in path.parts),
        "invalid_path",
    )
    require(path.as_posix() == relative, "invalid_path")
    require(len(path.parts) == 2 and path.suffix == suffix, "invalid_path")
    require(bool(VECTOR_ID.fullmatch(path.stem)), "invalid_path")
    return root / path


def verify_manifest(corpus, expected=None):
    root = safe_root(corpus)
    manifest = read_json(root / "manifest.json")
    require(
        isinstance(manifest, dict)
        and set(manifest) == {"schema_version", "vectors"}
        and type(manifest["schema_version"]) is int
        and manifest["schema_version"] == 1
        and isinstance(manifest["vectors"], list),
        "manifest_schema",
    )
    rows = manifest["vectors"]
    ids = []
    files = {"manifest.json", "manifest.sha256"}
    for row in rows:
        require(isinstance(row, dict), "row_schema")
        base = {"id", "operation", "input_path", "expected", "models"}
        if row.get("expected") == "accept":
            require(
                set(row) == base | {"bytes_path", "byte_length", "sha256"}, "row_schema"
            )
        elif row.get("expected") == "reject":
            require(
                set(row)
                in (
                    base | {"expected_error_type"},
                    base | {"expected_error_type", "expected_error_code"},
                ),
                "row_schema",
            )
            require(
                row["expected_error_type"]
                in {"value_error", "literal_error", "extra_forbidden"},
                "error_type",
            )
            if "expected_error_code" in row:
                require(
                    row["expected_error_type"] == "value_error"
                    and bool(
                        re.fullmatch(r"[a-z][a-z0-9_]*", row["expected_error_code"])
                    ),
                    "error_code",
                )
        else:
            raise GateError("row_expectation")
        ident = row["id"]
        require(
            isinstance(ident, str) and bool(VECTOR_ID.fullmatch(ident)), "vector_id"
        )
        require(
            ident in (ACCEPT if row["expected"] == "accept" else REJECT),
            "vector_id_inventory",
        )
        ids.append(ident)
        models = row["models"]
        require(
            isinstance(models, list)
            and models
            and all(isinstance(m, str) for m in models)
            and models == sorted(set(models))
            and set(models) == operation_tokens(row),
            "operation_models",
        )
        input_path = checked_path(root, row["input_path"], "inputs", ".json")
        require(input_path.stem == ident, "input_id")
        files.add(row["input_path"])
        if row["expected"] == "accept":
            byte_path = checked_path(root, row["bytes_path"], "bytes", ".bin")
            require(byte_path.stem == ident, "bytes_id")
            files.add(row["bytes_path"])
    require(ids == sorted(set(ids)), "row_order")
    actual = {str(path.relative_to(root)) for path in root.rglob("*") if path.is_file()}
    require(actual == files, "corpus_file_set")
    for row in rows:
        read_json(root / row["input_path"])
        if row["expected"] == "accept":
            raw = (root / row["bytes_path"]).read_bytes()
            require(
                type(row["byte_length"]) is int
                and row["byte_length"] >= 0
                and len(raw) == row["byte_length"]
                and valid_sha(row["sha256"], 64)
                and digest(raw) == row["sha256"],
                "byte_digest:" + row["id"],
            )
    manifest_digest = digest((root / "manifest.json").read_bytes())
    require(
        (root / "manifest.sha256").read_bytes()
        == f"{manifest_digest}  manifest.json\n".encode(),
        "manifest_sha_file",
    )
    if expected is not None:
        require(expected == manifest_digest, "lock_manifest_digest")
    return manifest_digest, rows


def git(*args, cwd="."):
    run = subprocess.run(["git", "-C", str(cwd), *args], capture_output=True, text=True)
    require(run.returncode == 0, "git:" + " ".join(args) + ":" + run.stderr.strip())
    return run.stdout.strip()


def event_base(event, cwd="."):
    data = json.loads(Path(event).read_text())
    kind = os.environ.get("GITHUB_EVENT_NAME") or (
        "pull_request" if "pull_request" in data else "push"
    )
    require(
        git("rev-parse", "--is-shallow-repository", cwd=cwd) == "false",
        "shallow_history",
    )
    head = git("rev-parse", "HEAD", cwd=cwd)
    if kind == "pull_request":
        base = data["pull_request"]["base"]["sha"]
        require(valid_sha(base, 40), "event_base")
        git("cat-file", "-e", base + "^{commit}", cwd=cwd)
        return git("merge-base", "HEAD", base, cwd=cwd), head
    require(
        kind == "push"
        and data.get("ref") == "refs/heads/main"
        and data.get("after") == head,
        "event_type",
    )
    base = data["before"]
    if base == "0" * 40:
        return git("hash-object", "-t", "tree", "--stdin", cwd=cwd), head
    require(valid_sha(base, 40), "event_base")
    git("merge-base", "--is-ancestor", base, head, cwd=cwd)
    return base, head


def aim_transition(event, peer, own_lock="preview-contract-backend.lock.json"):
    base, head = event_base(event)
    current = lock("aim-data", own_lock)
    previous_raw = subprocess.run(
        ["git", "show", f"{base}:{own_lock}"], capture_output=True
    )
    if previous_raw.returncode == 0:
        previous = json.loads(previous_raw.stdout)
        if previous == current:
            return "unchanged"
        require(
            previous.get("backend_sha") != current["backend_sha"]
            and previous.get("manifest_sha256") != current["manifest_sha256"],
            "same_manifest_pin_bump",
        )
    require(git("rev-parse", "HEAD", cwd=peer) == current["backend_sha"], "peer_sha")
    corpus = Path(peer) / "tests/fixtures/preview/cross_repo_contract/v1"
    verify_manifest(corpus, current["manifest_sha256"])
    parent_digest = subprocess.run(
        [
            "git",
            "-C",
            str(peer),
            "show",
            "HEAD^:tests/fixtures/preview/cross_repo_contract/v1/manifest.sha256",
        ],
        capture_output=True,
    )
    require(
        parent_digest.returncode != 0
        or parent_digest.stdout != (corpus / "manifest.sha256").read_bytes(),
        "non_anchor_pin",
    )
    return "anchor"


def compare(corpus, left, right):
    _, rows = verify_manifest(corpus)
    a, b = read_json(left), read_json(right)
    require(
        set(a)
        == set(b)
        == {"repo_sha", "manifest_sha256", "results", "coverage_observations"},
        "result_schema",
    )
    require(
        a["manifest_sha256"]
        == b["manifest_sha256"]
        == digest((Path(corpus) / "manifest.json").read_bytes()),
        "result_manifest",
    )
    for output in (a, b):
        require(
            [r["id"] for r in output["results"]] == [r["id"] for r in rows],
            "result_ids",
        )
    for row, lhs, rhs in zip(rows, a["results"], b["results"]):
        require(
            lhs["status"] == rhs["status"] == row["expected"], "status:" + row["id"]
        )
        if row["expected"] == "accept":
            expected_bytes = (Path(corpus) / row["bytes_path"]).read_bytes()
            encoded = (
                base64.urlsafe_b64encode(expected_bytes).rstrip(b"=").decode("ascii")
            )
            require(
                lhs["bytes"] == rhs["bytes"] == encoded
                and lhs["byte_length"] == rhs["byte_length"] == row["byte_length"]
                and lhs["sha256"] == rhs["sha256"] == row["sha256"],
                "bytes:" + row["id"],
            )
        else:
            require(lhs["errors"] == rhs["errors"], "cross_error:" + row["id"])
            for result in (lhs, rhs):
                errors = result["errors"]
                require(
                    isinstance(errors, list)
                    and errors
                    and all(
                        error["type"] == row["expected_error_type"]
                        and error.get("code") == row.get("expected_error_code")
                        for error in errors
                    ),
                    "error:" + row["id"],
                )
    return len(rows)


def allowed_values(annotation):
    origin = get_origin(annotation)
    if origin is Literal:
        return set(get_args(annotation))
    if isinstance(annotation, type) and issubclass(annotation, Enum):
        return {item.value for item in annotation}
    values = set()
    for arg in get_args(annotation):
        values.update(allowed_values(arg))
    return values


def model_registry():
    from app.models.preview_disclosure_schemas import (
        DisclosureBinding,
        PreviewDisclosureRequest,
        PlatformEnvelope,
        SignerKeyEvidence,
    )
    from app.models.dataset_commitment_schemas import (
        DatasetPreviewProofContract,
        DatasetCommitmentContract,
        ProofSibling,
    )

    classes = (
        DisclosureBinding,
        DatasetPreviewProofContract,
        DatasetCommitmentContract,
        PreviewDisclosureRequest,
        PlatformEnvelope,
        ProofSibling,
        SignerKeyEvidence,
    )
    tokens = (
        "shared.DisclosureBinding",
        "shared.PreviewProof",
        "shared.PreviewCommitment",
        "shared.PreviewDisclosureRequest",
        "shared.PlatformEnvelope",
        f"{ProofSibling.__module__}.{ProofSibling.__name__}",
        f"{SignerKeyEvidence.__module__}.{SignerKeyEvidence.__name__}",
    )
    return dict(zip(tokens, classes))


def coverage(result):
    payload = read_json(result)
    require(
        isinstance(payload, dict)
        and set(payload)
        == {"repo_sha", "manifest_sha256", "results", "coverage_observations"},
        "result_schema",
    )
    registry = model_registry()
    accepted = {row["id"] for row in payload["results"] if row["status"] == "accept"}
    seen = {}
    direct = {}
    paths = set()
    for observation in payload["coverage_observations"]:
        require(
            isinstance(observation, dict)
            and set(observation) == {"id", "model", "input_path", "direct", "fields"},
            "observation_schema",
        )
        ident, token = observation["id"], observation["model"]
        require(
            ident in accepted
            and token in registry
            and type(observation["direct"]) is bool
            and isinstance(observation["input_path"], str),
            "observation_target",
        )
        path = observation["input_path"]
        require(path == "" or path.startswith("/"), "observation_pointer")
        require(observation["direct"] == (path == ""), "observation_direct")
        marker = (ident, token, path)
        require(marker not in paths, "duplicate_observation")
        paths.add(marker)
        model = registry[token]
        fields = observation["fields"]
        require(
            isinstance(fields, list)
            and [field["name"] for field in fields] == sorted(model.model_fields),
            "observation_fields",
        )
        for field in fields:
            require(
                set(field)
                == (
                    {"name", "present", "value"}
                    if field["present"]
                    else {"name", "present"}
                )
                and type(field["present"]) is bool,
                "observation_field_schema",
            )
            name = field["name"]
            key = (token, name)
            state = seen.setdefault(
                key, {"present": False, "absent": False, "values": set()}
            )
            state["present" if field["present"] else "absent"] = True
            if field["present"]:
                value = field["value"]
                try:
                    state["values"].add(value)
                except TypeError:
                    pass
                if observation["direct"] and token == "shared.PreviewProof":
                    direct.setdefault(name, set()).add(
                        value
                        if isinstance(value, (str, int, bool, type(None)))
                        else None
                    )
    for token, model in registry.items():
        for name, field in model.model_fields.items():
            state = seen.get(
                (token, name), {"present": False, "absent": False, "values": set()}
            )
            require(state["present"], f"coverage:{token}.{name}:missing_present")
            if not field.is_required():
                require(state["absent"], f"coverage:{token}.{name}:missing_absent")
            for value in allowed_values(field.annotation):
                require(
                    value in state["values"],
                    f"coverage:{token}.{name}:missing_value:{value}",
                )
    for name, values in (
        ("package_profile", {"aim-preview-package-v1", "aim-preview-package-v2"}),
        ("scan_policy", {"aim-preview-policy-v1", "aim-preview-policy-v2"}),
    ):
        for value in values:
            require(
                value in direct.get(name, set()),
                f"coverage:shared.PreviewProof.{name}:missing_direct_value:{value}",
            )
    return len(payload["coverage_observations"])


def deleted_consumers():
    hits = []
    for name in DELETED:
        found = subprocess.run(
            [
                "git",
                "grep",
                "-l",
                "-e",
                name,
                "--",
                ".github",
                "app",
                "scripts",
                "tests",
                ":(exclude)scripts/preview_contract_gate.py",
            ],
            capture_output=True,
            text=True,
        )
        count = len(found.stdout.splitlines())
        print(f"{name}: {count}")
        if count:
            hits.append(name)
    require(not hits, "deleted_consumer:" + ",".join(hits))
    builder = Path("scripts/build_preview_producer_evidence.py").read_text()
    synthetic = Path("tests/run_preview_producer_synthetic.py").read_text()
    golden = Path("tests/test_preview_signing_service.py").read_text()
    node = Path("tests/preview_differential_check.cjs").read_text()
    require(
        "contract_manifest_sha256" in builder
        and "fixture_manifest_sha256" not in builder,
        "evidence_digest_not_migrated",
    )
    require(
        "--contract-corpus" in synthetic and "verify_manifest" in synthetic,
        "synthetic_corpus_not_verified",
    )
    require(
        "test_pinned_differential_corpus_python_and_node" in golden
        and "contract-corpus" in golden,
        "golden_not_migrated",
    )
    require(
        "manifest.json" in node and "bytes/" in node and "inputs/" in node,
        "node_corpus_not_migrated",
    )
    return True


def main():
    parser = argparse.ArgumentParser()
    commands = parser.add_subparsers(dest="command", required=True)
    p = commands.add_parser("lock")
    p.add_argument("--kind", choices=("aim-data", "backend"), required=True)
    p.add_argument("--file", required=True)
    p.add_argument("--github-output")
    p = commands.add_parser("verify-manifest")
    p.add_argument("--corpus", required=True)
    p.add_argument("--expected")
    p = commands.add_parser("aim-transition")
    p.add_argument("--event", required=True)
    p.add_argument("--peer", required=True)
    p = commands.add_parser("compare")
    p.add_argument("--corpus", required=True)
    p.add_argument("--left", required=True)
    p.add_argument("--right", required=True)
    p = commands.add_parser("coverage")
    p.add_argument("--result", required=True)
    commands.add_parser("deleted-consumers")
    args = parser.parse_args()
    try:
        if args.command == "lock":
            print(lock(args.kind, args.file, args.github_output))
        elif args.command == "verify-manifest":
            print(verify_manifest(args.corpus, args.expected)[0])
        elif args.command == "aim-transition":
            print(aim_transition(args.event, args.peer))
        elif args.command == "compare":
            print(compare(args.corpus, args.left, args.right))
        elif args.command == "coverage":
            print(coverage(args.result))
        else:
            deleted_consumers()
    except (GateError, OSError, KeyError, ValueError) as exc:
        parser.exit(1, f"{args.command}: {exc}\n")


if __name__ == "__main__":
    main()
