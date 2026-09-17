"""Additive conformance fixtures; synthetic bytes(range(32)) key only."""

import hashlib
import json
from pathlib import Path

from app.services.dataset_merkle_service import canonical_json_bytes, encode_base64url
from app.services.dataset_canonicalization import CanonicalSchema
from app.services.preview_signing_service import (
    disclosure_bytes, platform_envelope_bytes, public_bytes,
)
from tests.preview_fixture_factory import material, platform_material, test_key

FIXTURE = Path("tests/fixtures/aim_preview_differential_v1.json")


def preimage(vector):
    kind, value = vector["kind"], vector["input"]
    if kind == "disclosure":
        return disclosure_bytes(value)
    if kind == "platform-envelope":
        return platform_envelope_bytes(value)
    if kind == "jcs":
        # Primitive conformance only: not an extra legal wire object/domain.
        return canonical_json_bytes(value)
    raise AssertionError(kind)


def differential_corpus():
    key = test_key()
    vectors = []

    def add(name, kind, value):
        row = dict(name=name, kind=kind, input=value)
        message = preimage(row)
        row.update(signed_bytes_hex=message.hex(),
                   signed_bytes_sha256=hashlib.sha256(message).hexdigest(),
                   signature=encode_base64url(key.sign(message)),
                   public_key=encode_base64url(public_bytes(key.public_key())))
        vectors.append(row)

    names = ["café", "東京", "😀", 'quote"back\\slash',
             "controls\x00\b\t\n\f\r\x1f", "separators\u2028\u2029"]
    _, _, binding = material()
    schema = CanonicalSchema([[name, "string", False, {}] for name in names])
    binding.update(schema_descriptors=schema.descriptors,
                   selected_fields=[d[0] for d in schema.descriptors],
                   schema_digest=encode_base64url(schema.digest),
                   update_cadence_days=2**53 - 1)
    add("unicode-disclosure", "disclosure", binding)
    _, envelope, _, _ = platform_material()
    envelope["binding"] = binding
    envelope["seller_signature"] = encode_base64url(key.sign(disclosure_bytes(binding)))
    add("unicode-envelope", "platform-envelope", envelope)
    add("unicode-object-keys", "jcs", {name: name for name in names})
    add("supplementary-order-admitted", "jcs", {"😀": "second", "𐀀": "first", "a": "ASCII"})
    for integer in (0, -1, 2**53 - 1, -(2**53 - 1)):
        add("integer-" + str(integer), "jcs", {"value": integer})
    rejected = []
    for name, value, error in (
        ("unsafe-positive", {"value": 2**53}, "unsafe_integer"),
        ("unsafe-negative", {"value": -(2**53)}, "unsafe_integer"),
        ("float-positive", {"value": 0.5}, "invalid_metadata"),
        ("float-negative", {"value": -1.25}, "invalid_metadata"),
        ("supplementary-order", {"\ue000": 0, "𐀀": 1}, "noncanonical_key_order"),
        ("supplementary-order-nested", [{"\ufffd": 0, "😀": 1}], "noncanonical_key_order"),
    ):
        rejected.append(dict(name=name, kind="jcs", input=value, error=error))
    return dict(fixture_notice="SYNTHETIC TEST KEY ONLY. jcs vectors test primitive admission, not wire field admission.",
                valid=vectors, must_reject=rejected)


if __name__ == "__main__":
    corpus = differential_corpus()
    raw = (json.dumps(corpus, ensure_ascii=False, indent=2) + "\n").encode()
    FIXTURE.write_bytes(raw)
    FIXTURE.with_suffix(".sha256").write_text(hashlib.sha256(raw).hexdigest() + "  " + FIXTURE.name + "\n")
