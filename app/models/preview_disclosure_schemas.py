"""Closed T metadata wire contracts; seller-origin content has no carrier here."""

from typing import Annotated, Literal, Mapping
import unicodedata
from pydantic import Field, model_serializer, model_validator
from app.models.dataset_commitment_schemas import (
    WireModel,
    Code,
    UUIDText,
    Timestamp,
    HexDigest,
    Digest,
    Signature,
    SignerReference,
    SAFE_INTEGER,
    DatasetCommitmentContract,
    DatasetPreviewProofContract,
)
from app.services.dataset_merkle_service import canonical_json_bytes, encode_base64url
from app.services.dataset_canonicalization import CanonicalSchema

FieldName = Annotated[str, Field(min_length=1, max_length=255)]


class DisclosureBinding(WireModel):
    profile: Literal["aim-preview-disclosure-v1"]
    decision: Literal["approve", "withdraw"]
    summary_id: UUIDText
    disclosure_version: UUIDText
    seller_id: UUIDText
    listing_id: UUIDText
    listing_version_id: UUIDText | None
    content_revision: UUIDText
    source_revision: HexDigest
    summary_approval_id: UUIDText
    summary_hash: HexDigest
    render_hash: HexDigest
    selected_fields: list[FieldName] = Field(max_length=25)
    preview_type: Literal["table"] | None
    content_type: Literal["tabular"] | None
    sample_decision: Literal["none", "approved"]
    sample_hash: HexDigest | None
    aggregate_hash: HexDigest
    # Historical records omitted this field and remain v1. Every newly
    # allocated candidate is upgraded explicitly to v2 before seller signing.
    aggregate_hash_profile: Literal[
        "aim-approved-aggregates-v1", "aim-approved-aggregates-v2"
    ] | None = None
    commitment_id: UUIDText | None
    schema_digest: Digest | None
    seller_dataset_version: Code | None
    # CanonicalSchema validates every nested parameter and bound, before signing.
    schema_descriptors: list = Field(max_length=500)
    proof_ids: list[UUIDText] = Field(max_length=100)
    sampled_leaf_list_digest: Digest | None
    scan_attestation_digest: HexDigest | None
    rights_basis_digest: HexDigest | None
    rights_basis_code: (
        Literal["owner", "licensed", "public_domain", "other_authorized"] | None
    )
    public_preview_permission: bool | None
    approved_by: UUIDText
    approved_at: Timestamp
    last_attested_by_seller_at: Timestamp
    update_cadence_days: Annotated[int, Field(gt=0, le=SAFE_INTEGER)] | None
    approval_expires_at: Timestamp | None
    supersedes: UUIDText | None
    request_id: UUIDText
    expected_current_disclosure_id: UUIDText | None
    signer_reference: SignerReference
    signature_algorithm: Literal["ed25519"]
    signature_profile: Literal["aim-preview-disclosure-signature-v1"]

    @model_serializer(mode="wrap")
    def preserve_legacy_aggregate_hash_wire(self, handler):
        result = handler(self)
        if self.aggregate_hash_profile is None:
            result.pop("aggregate_hash_profile", None)
        return result

    @model_validator(mode="after")
    def binding_rules(self):
        if self.supersedes != self.expected_current_disclosure_id:
            raise ValueError("head_mismatch")
        if self.disclosure_version in {
            self.supersedes,
            self.expected_current_disclosure_id,
        }:
            raise ValueError("candidate_not_new")
        if self.decision == "withdraw" and (
            self.sample_decision != "none" or self.supersedes is None
        ):
            raise ValueError("invalid_withdrawal")
        grant = (
            self.sample_hash,
            self.commitment_id,
            self.schema_digest,
            self.seller_dataset_version,
            self.sampled_leaf_list_digest,
            self.scan_attestation_digest,
            self.rights_basis_digest,
            self.rights_basis_code,
            self.public_preview_permission,
            self.preview_type,
            self.content_type,
        )
        if self.sample_decision == "none":
            if (
                any(v is not None for v in grant)
                or self.proof_ids
                or self.schema_descriptors
                or self.selected_fields
            ):
                raise ValueError("none_grant_forbidden")
        else:
            if (
                any(v is None for v in grant)
                or self.public_preview_permission is not True
                or not self.proof_ids
                or not self.selected_fields
            ):
                raise ValueError("approval_evidence_missing")
            schema = CanonicalSchema(self.schema_descriptors)
            def public_types(fields):
                for _, tag, _, params in fields:
                    if tag == "binary":
                        raise ValueError("binary_sample_forbidden")
                    if tag == "array":
                        element = params["element_type"]
                        public_types([["element", element["type"], False, element["type_parameters"]]])
                    if tag == "object":
                        public_types([
                            [field["name"], field["type"], field["nullable"], field["type_parameters"]]
                            for field in params["object_fields"]
                        ])
            public_types(schema.descriptors)
            if (
                schema.descriptors != self.schema_descriptors
                or len(schema.descriptors) > 25
            ):
                raise ValueError("descriptor_mismatch")
            if encode_base64url(schema.digest) != self.schema_digest:
                raise ValueError("schema_digest_mismatch")
            names = [d[0] for d in schema.descriptors]
            if len(set(self.selected_fields)) != len(self.selected_fields) or any(
                f not in names or f != unicodedata.normalize("NFC", f)
                for f in self.selected_fields
            ):
                raise ValueError("selected_field_mismatch")
            if len(set(self.proof_ids)) != len(self.proof_ids):
                raise ValueError("duplicate_proof")
        return self


class PreviewDisclosureRequest(WireModel):
    profile: Literal["aim-preview-disclosure-v1"]
    summary_id: UUIDText
    binding: DisclosureBinding
    seller_signature: Signature
    commitment: DatasetCommitmentContract | None
    proofs: list[DatasetPreviewProofContract] = Field(max_length=100)

    @model_validator(mode="before")
    @classmethod
    def raw_package_identity(cls, value):
        package_keys = (
            "preview_package_url", "package_media_type", "package_profile",
            "package_byte_ceiling", "scan_policy", "scan_policy_version",
            "scanned_at", "scan_verdict", "signer_reference",
        )
        if isinstance(value, Mapping):
            proofs = value.get("proofs")
            if (
                isinstance(proofs, list)
                and len(proofs) >= 2
                and all(isinstance(proof, Mapping) and all(key in proof for key in package_keys) for proof in proofs)
                and any(tuple(proof[key] for key in package_keys) != tuple(proofs[0][key] for key in package_keys) for proof in proofs[1:])
            ):
                raise ValueError("package_mismatch")
        return value

    @model_validator(mode="after")
    def agreement(self):
        b, c = self.binding, self.commitment
        if b.summary_id != self.summary_id:
            raise ValueError("summary_mismatch")
        if b.sample_decision == "none":
            if c is not None or self.proofs:
                raise ValueError("none_grant_forbidden")
        else:
            if c is None or not self.proofs:
                raise ValueError("commitment_missing")
            proofs = [p.model_dump(mode="json") for p in self.proofs]
            if canonical_json_bytes(proofs) != canonical_json_bytes(
                [p.model_dump(mode="json") for p in c.proofs]
            ):
                raise ValueError("proof_order_mismatch")
            if any(
                getattr(b, k) != getattr(c, k)
                for k in (
                    "listing_id",
                    "commitment_id",
                    "schema_digest",
                    "seller_dataset_version",
                )
            ):
                raise ValueError("commitment_mismatch")
            if (
                c.aim_data_signer_reference != b.signer_reference
                or [p.proof_id for p in self.proofs] != b.proof_ids
            ):
                raise ValueError("signer_or_proof_mismatch")
            if any(
                p.package_profile != "aim-preview-package-v2"
                or p.signer_reference != b.signer_reference
                for p in self.proofs
            ):
                raise ValueError("unsupported_proof")
            packages = {
                (
                    p.preview_package_url,
                    p.package_media_type,
                    p.package_profile,
                    p.package_byte_ceiling,
                    p.scan_policy,
                    p.scan_policy_version,
                    p.scanned_at,
                    p.scan_verdict,
                    p.signer_reference,
                )
                for p in self.proofs
            }
            if len(packages) != 1 or len({p.leaf_index for p in self.proofs}) != len(
                self.proofs
            ):
                raise ValueError("package_mismatch")
            from app.services.preview_content_policy import (
                sampled_leaf_list_digest,
                scan_attestation_digest,
            )
            from app.services.preview_package_service import sample_hash
            from app.services.dataset_merkle_service import (
                verify_inclusion_proof,
                compute_leaf_hash,
            )

            sampled_digest = sampled_leaf_list_digest(proofs)
            if any(p.sampled_leaf_list_digest != sampled_digest for p in self.proofs):
                raise ValueError("sampled_leaf_digest_mismatch")
            if b.sample_hash != sample_hash(
                proofs
            ) or b.sampled_leaf_list_digest != sampled_digest:
                raise ValueError("sample_mismatch")
            if b.scan_attestation_digest != scan_attestation_digest(proofs):
                raise ValueError("scan_mismatch")
            from app.services.preview_signing_service import seller_attestation_digest

            attestation = {
                **{
                    k: getattr(c, k)
                    for k in (
                        "listing_id",
                        "seller_dataset_version",
                        "schema_digest",
                        "dataset_merkle_root",
                        "leaf_count",
                        "signed_at",
                    )
                },
                "sample_hash": b.sample_hash,
                "rights_basis_digest": b.rights_basis_digest,
                "public_preview_permission": b.public_preview_permission,
                "metadata_accuracy_confirmed": True,
            }
            if seller_attestation_digest(attestation) != c.seller_attestation_digest:
                raise ValueError("seller_attestation_mismatch")
            if (
                any(p.scanned_at > c.signed_at for p in self.proofs)
                or c.signed_at > b.approved_at
                or b.last_attested_by_seller_at > b.approved_at
            ):
                raise ValueError("attestation_time_mismatch")
            for p in proofs:
                if not verify_inclusion_proof(
                    compute_leaf_hash(p["base_row_digest"], p["duplicate_ordinal"]),
                    p["leaf_index"],
                    p["tree_size"],
                    p["siblings"],
                    c.dataset_merkle_root,
                ):
                    raise ValueError("proof_invalid")
        if len(canonical_json_bytes(self.model_dump(mode="json"))) > 262144:
            raise ValueError("manifest_limit")
        return self


class WithdrawalRequest(PreviewDisclosureRequest):
    @model_validator(mode="after")
    def withdrawal(self):
        if self.binding.decision != "withdraw":
            raise ValueError("invalid_withdrawal")
        return self


class RefreshRequest(PreviewDisclosureRequest):
    @model_validator(mode="after")
    def refresh(self):
        if (
            self.binding.decision != "approve"
            or self.binding.sample_decision != "approved"
            or self.binding.supersedes is None
        ):
            raise ValueError("invalid_refresh")
        return self


class SupersessionRequest(RefreshRequest):
    pass


class SignerKeyEvidence(WireModel):
    key_id: UUIDText
    algorithm: Literal["ed25519"]
    public_key: Digest  # Same 32-byte representation, not a hash here.
    status: Literal["active", "rotated", "revoked"]
    valid_from: Timestamp
    valid_until: Timestamp | None = None
    fingerprint: HexDigest


class PlatformEnvelope(WireModel):
    profile: Literal["aim-preview-platform-envelope-v1"]
    key_id: Code
    signature_algorithm: Literal["ed25519"]
    binding: DisclosureBinding
    seller_signature: Signature
    signer_keys: list[SignerKeyEvidence] = Field(min_length=1, max_length=4)
    signature: Signature

    @model_validator(mode="after")
    def unique_keys(self):
        if len({k.key_id for k in self.signer_keys}) != len(self.signer_keys):
            raise ValueError("duplicate_signer_keys")
        return self


class SellerAttestation(WireModel):
    listing_id: UUIDText
    seller_dataset_version: Code
    schema_digest: Digest
    dataset_merkle_root: Digest
    leaf_count: Annotated[int, Field(ge=1, le=SAFE_INTEGER)]
    sample_hash: HexDigest
    rights_basis_digest: HexDigest
    public_preview_permission: Literal[True]
    metadata_accuracy_confirmed: Literal[True]
    signed_at: Timestamp
