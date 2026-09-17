"""Bounded seller-selected original-byte samples; never reads non-sample files."""
import hashlib
from tempfile import SpooledTemporaryFile
from types import SimpleNamespace

from app.config import settings
from app.services.source_artifact_resolver import resolve_member_path


def validate_sample_members(members):
    samples = [m for m in members if m["is_sample"]]
    for member in samples:
        if member["role"] != "data":
            raise ValueError(f"is_sample requires data: {member['relative_path']}")
        if member["size_bytes"] > settings.sample_max_file_bytes:
            raise ValueError(f"{member['relative_path']}: SAMPLE_MAX_FILE_BYTES: {settings.sample_max_file_bytes}")
    if len(samples) > settings.sample_max_files:
        raise ValueError(f"SAMPLE_MAX_FILES: {settings.sample_max_files}")
    if sum(m["size_bytes"] for m in samples) > settings.sample_max_total_bytes:
        raise ValueError(f"SAMPLE_MAX_TOTAL_BYTES: {settings.sample_max_total_bytes}")
    return samples


async def upload_samples(*, dataset_id, root_path, version_id, members_upload_id, members, post):
    if not settings.multi_file_datasets_enabled:
        raise ValueError("multi_file_datasets_disabled")
    samples = validate_sample_members(members)  # validate ALL before any upload
    result = None
    dataset = SimpleNamespace(id=dataset_id, root_path=root_path)
    for member in samples:
        local = SimpleNamespace(**member, dataset_id=dataset_id, status="current")
        path = resolve_member_path(dataset, local)
        if path is None:
            raise ValueError(f"sample_member_missing: {member['relative_path']}")
        # Freeze and verify before egress; stream the verified snapshot so large
        # samples do not require a base64 expansion or an unbounded memory read.
        with SpooledTemporaryFile(max_size=1024**2, mode="w+b") as frozen:
            digest = hashlib.sha256()
            size = 0
            with open(path, "rb") as stream:
                while chunk := stream.read(min(64 * 1024, member["size_bytes"] + 1 - size)):
                    size += len(chunk)
                    digest.update(chunk)
                    frozen.write(chunk)
                    if size > member["size_bytes"]:
                        break
            if size != member["size_bytes"] or digest.hexdigest() != member["sha256"]:
                raise ValueError(f"sample_member_changed: {member['relative_path']}")
            frozen.seek(0)

            async def content():
                while chunk := frozen.read(64 * 1024):
                    yield chunk

            signed_payload = {
                "version_id": str(version_id),
                "members_upload_id": str(members_upload_id),
                "index": member["index"],
                "size_bytes": member["size_bytes"],
                "sha256": member["sha256"],
            }
            result = await post(
                f"/api/v1/vz/versions/{version_id}/samples/{member['index']}"
                f"?members_upload_id={members_upload_id}",
                signed_payload, action="publish_sample_member", content=content(),
            )
        if result and result.get("status") in ("active", "quarantined", "superseded"):
            break
    return result
