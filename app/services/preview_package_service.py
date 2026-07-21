"""Seller-hostable preview package construction and customer-side validation."""
from __future__ import annotations

import ipaddress
import json
import socket
from dataclasses import dataclass
from typing import Any, Awaitable, Callable, Mapping, Sequence
from urllib.parse import urlsplit
from uuid import NAMESPACE_URL, UUID, uuid5

import httpx

from app.services.dataset_canonicalization import decode_base64url, encode_base64url, jcs_canonical_bytes
from app.services.dataset_merkle_service import MAX_TREE_SIZE, DatasetMerkleCommitment


PACKAGE_PROFILE = "aim-preview-package-v1"
PACKAGE_MEDIA_TYPE = "application/vnd.aim.preview+json"
MAX_PREVIEW_ROWS = 50
MAX_CANONICAL_ROW_BYTES = 5_120
MAX_PACKAGE_BYTES = 128 * 1024
MAX_PROOF_SIBLINGS = 63


class PreviewPackageError(ValueError):
    """A stable package/origin failure that never includes package content."""

    def __init__(self, code: str) -> None:
        super().__init__(code)
        self.code = code


@dataclass(frozen=True)
class PreviewPackage:
    body: Mapping[str, Any]
    encoded: bytes
    canonical_row_bytes: int


def _public_row(canonical_row: Sequence[Sequence[Any]]) -> dict[str, Any]:
    def unwrap(tag: str, value: Any) -> Any:
        if tag == "binary":
            raise PreviewPackageError("binary_preview_forbidden")
        if tag == "array":
            return [unwrap(str(child[0]), child[1]) for child in value]
        if tag == "object":
            return {str(child[0]): unwrap(str(child[1]), child[2]) for child in value if child[1] != "missing"}
        return value

    result: dict[str, Any] = {}
    for name, tag, value in canonical_row:
        if tag != "missing":
            result[str(name)] = unwrap(str(tag), value)
    return result


class PreviewPackageService:
    def build(
        self,
        commitment: DatasetMerkleCommitment,
        *,
        commitment_id: UUID | str,
        selected_leaf_indices: Sequence[int],
        proof_ids: Sequence[UUID | str] | None = None,
    ) -> PreviewPackage:
        if not selected_leaf_indices:
            raise PreviewPackageError("empty_preview_selection")
        if len(selected_leaf_indices) > MAX_PREVIEW_ROWS:
            raise PreviewPackageError("preview_row_limit_exceeded")
        if len(set(selected_leaf_indices)) != len(selected_leaf_indices):
            raise PreviewPackageError("duplicate_leaf_index")
        if proof_ids is None:
            proof_ids = [
                uuid5(NAMESPACE_URL, f"aim-preview-proof:{commitment_id}:{index}")
                for index in range(len(selected_leaf_indices))
            ]
        if len(proof_ids) != len(selected_leaf_indices) or len({str(item) for item in proof_ids}) != len(proof_ids):
            raise PreviewPackageError("proof_id_mismatch")
        entries: list[dict[str, Any]] = []
        canonical_size = 0
        for proof_id, index in zip(proof_ids, selected_leaf_indices):
            if isinstance(index, bool) or not isinstance(index, int) or not 0 <= index < commitment.leaf_count:
                raise PreviewPackageError("invalid_leaf_index")
            leaf = commitment.leaves[index]
            row = _public_row(leaf.record.canonical_row)
            canonical_size += len(jcs_canonical_bytes(row))
            if canonical_size > MAX_CANONICAL_ROW_BYTES:
                raise PreviewPackageError("preview_row_bytes_exceeded")
            siblings = commitment.proof_for_index(index)
            if len(siblings) > MAX_PROOF_SIBLINGS:
                raise PreviewPackageError("proof_bound_exceeded")
            entries.append(
                {
                    "proof_id": str(proof_id),
                    "row": row,
                    "base_row_digest": encode_base64url(leaf.record.base_row_digest),
                    "duplicate_ordinal": leaf.duplicate_ordinal,
                    "leaf_index": leaf.leaf_index,
                    "tree_size": commitment.leaf_count,
                    "siblings": siblings,
                }
            )
        body = {
            "profile": PACKAGE_PROFILE,
            "commitment_id": str(commitment_id),
            "schema_digest": encode_base64url(commitment.schema_digest),
            "entries": entries,
        }
        encoded = jcs_canonical_bytes(body)
        if len(encoded) > MAX_PACKAGE_BYTES:
            raise PreviewPackageError("preview_package_bytes_exceeded")
        return PreviewPackage(body, encoded, canonical_size)

    @staticmethod
    def validate_package(body: Any, *, expected_commitment_id: UUID | str | None = None) -> PreviewPackage:
        if not isinstance(body, dict) or set(body) != {"profile", "commitment_id", "schema_digest", "entries"}:
            raise PreviewPackageError("preview_package_shape_invalid")
        if body["profile"] != PACKAGE_PROFILE:
            raise PreviewPackageError("preview_package_profile_invalid")
        try:
            UUID(str(body["commitment_id"]))
            decode_base64url(body["schema_digest"], expected_size=32)
        except (ValueError, TypeError) as exc:
            raise PreviewPackageError("preview_package_identifier_invalid") from exc
        if expected_commitment_id is not None and body["commitment_id"] != str(expected_commitment_id):
            raise PreviewPackageError("preview_commitment_mismatch")
        entries = body["entries"]
        if not isinstance(entries, list) or not 1 <= len(entries) <= MAX_PREVIEW_ROWS:
            raise PreviewPackageError("preview_row_limit_exceeded")
        expected_keys = {
            "proof_id",
            "row",
            "base_row_digest",
            "duplicate_ordinal",
            "leaf_index",
            "tree_size",
            "siblings",
        }
        proof_ids: set[str] = set()
        leaf_indices: set[int] = set()
        leaf_identities: set[tuple[str, int]] = set()
        canonical_size = 0
        tree_size: int | None = None
        for entry in entries:
            if not isinstance(entry, dict) or set(entry) != expected_keys or not isinstance(entry["row"], dict):
                raise PreviewPackageError("preview_entry_shape_invalid")
            proof_id = str(entry["proof_id"])
            try:
                UUID(proof_id)
                decode_base64url(entry["base_row_digest"], expected_size=32)
            except (ValueError, TypeError) as exc:
                raise PreviewPackageError("preview_entry_identifier_invalid") from exc
            if proof_id in proof_ids:
                raise PreviewPackageError("duplicate_proof_id")
            proof_ids.add(proof_id)
            siblings = entry["siblings"]
            if not isinstance(siblings, list) or len(siblings) > MAX_PROOF_SIBLINGS:
                raise PreviewPackageError("proof_bound_exceeded")
            ordinal, leaf_index, entry_tree_size = (
                entry["duplicate_ordinal"],
                entry["leaf_index"],
                entry["tree_size"],
            )
            if (
                isinstance(ordinal, bool)
                or isinstance(leaf_index, bool)
                or isinstance(entry_tree_size, bool)
                or not all(isinstance(value, int) for value in (ordinal, leaf_index, entry_tree_size))
                or not 1 <= entry_tree_size <= MAX_TREE_SIZE
                or not 0 <= leaf_index < entry_tree_size
                or not 0 <= ordinal < entry_tree_size
            ):
                raise PreviewPackageError("preview_entry_tree_invalid")
            if tree_size is None:
                tree_size = entry_tree_size
            elif entry_tree_size != tree_size:
                raise PreviewPackageError("preview_entry_tree_mismatch")
            if leaf_index in leaf_indices:
                raise PreviewPackageError("duplicate_leaf_index")
            leaf_indices.add(leaf_index)
            leaf_identity = (entry["base_row_digest"], ordinal)
            if leaf_identity in leaf_identities:
                raise PreviewPackageError("duplicate_leaf_identity")
            leaf_identities.add(leaf_identity)
            for sibling in siblings:
                if not isinstance(sibling, dict) or set(sibling) != {"hash", "direction"}:
                    raise PreviewPackageError("preview_sibling_shape_invalid")
                if sibling["direction"] not in {"left", "right"}:
                    raise PreviewPackageError("preview_sibling_direction_invalid")
                try:
                    decode_base64url(sibling["hash"], expected_size=32)
                except (ValueError, TypeError) as exc:
                    raise PreviewPackageError("preview_sibling_hash_invalid") from exc
            canonical_size += len(jcs_canonical_bytes(entry["row"]))
            if canonical_size > MAX_CANONICAL_ROW_BYTES:
                raise PreviewPackageError("preview_row_bytes_exceeded")
        encoded = jcs_canonical_bytes(body)
        if len(encoded) > MAX_PACKAGE_BYTES:
            raise PreviewPackageError("preview_package_bytes_exceeded")
        return PreviewPackage(body, encoded, canonical_size)

    async def validate_origin(
        self,
        url: str,
        *,
        expected_commitment_id: UUID | str,
        viewer_origin: str = "https://ai.market",
        client: httpx.AsyncClient | None = None,
        resolver: Callable[[str], Awaitable[Sequence[str]]] | None = None,
    ) -> PreviewPackage:
        parsed = urlsplit(url)
        if parsed.scheme != "https" or not parsed.hostname or parsed.username or parsed.password:
            raise PreviewPackageError("preview_origin_https_required")
        if parsed.query or parsed.fragment:
            raise PreviewPackageError("preview_origin_query_forbidden")
        addresses = await resolver(parsed.hostname) if resolver else await self._resolve(parsed.hostname)
        if not addresses or any(not self._is_public_ip(address) for address in addresses):
            raise PreviewPackageError("preview_origin_not_public")
        owned_client = client is None
        request_client = client or httpx.AsyncClient(timeout=10.0, follow_redirects=False)
        try:
            async with request_client.stream(
                "GET",
                url,
                headers={"Accept": PACKAGE_MEDIA_TYPE, "Origin": viewer_origin},
            ) as response:
                if response.history or response.is_redirect:
                    raise PreviewPackageError("preview_origin_redirect")
                if response.status_code != 200:
                    raise PreviewPackageError("preview_origin_unavailable")
                media_type = response.headers.get("content-type", "").split(";", 1)[0].strip().lower()
                if media_type != PACKAGE_MEDIA_TYPE:
                    raise PreviewPackageError("preview_origin_media_type_invalid")
                if response.headers.get("content-encoding", "identity").lower() not in {"", "identity"}:
                    raise PreviewPackageError("preview_origin_encoding_forbidden")
                cors = response.headers.get("access-control-allow-origin")
                if cors not in {"*", viewer_origin}:
                    raise PreviewPackageError("preview_origin_cors_invalid")
                if response.headers.get("access-control-allow-credentials", "").lower() == "true":
                    raise PreviewPackageError("preview_origin_credentials_forbidden")
                if "set-cookie" in response.headers:
                    raise PreviewPackageError("preview_origin_cookie_forbidden")
                captured = bytearray()
                async for chunk in response.aiter_bytes():
                    captured.extend(chunk)
                    if len(captured) > MAX_PACKAGE_BYTES:
                        raise PreviewPackageError("preview_package_bytes_exceeded")
        except PreviewPackageError:
            raise
        except (httpx.HTTPError, OSError) as exc:
            raise PreviewPackageError("preview_origin_unavailable") from exc
        finally:
            if owned_client:
                await request_client.aclose()
        try:
            body = json.loads(captured)
        except (UnicodeDecodeError, json.JSONDecodeError) as exc:
            raise PreviewPackageError("preview_package_json_invalid") from exc
        return self.validate_package(body, expected_commitment_id=expected_commitment_id)

    @staticmethod
    async def _resolve(hostname: str) -> Sequence[str]:
        import asyncio

        loop = asyncio.get_running_loop()
        try:
            result = await loop.getaddrinfo(hostname, None, type=socket.SOCK_STREAM)
        except socket.gaierror as exc:
            raise PreviewPackageError("preview_origin_unavailable") from exc
        return sorted({item[4][0] for item in result})

    @staticmethod
    def _is_public_ip(value: str) -> bool:
        try:
            address = ipaddress.ip_address(value)
        except ValueError:
            return False
        return bool(address.is_global and not address.is_private and not address.is_loopback and not address.is_link_local)
