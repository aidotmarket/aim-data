"""Bounded seller-selected original-byte samples; never reads non-sample files."""
import base64
import hashlib
from types import SimpleNamespace

from app.config import settings
from app.services.source_artifact_resolver import resolve_member_path


def validate_sample_members(members):
    samples = [m for m in members if m["is_sample"]]
    for member in samples:
        if member["role"] != "data":
            raise ValueError(f"is_sample requires data: {member['relative_path']}")
        if member["size_bytes"] > settings.sample_max_file_bytes:
            raise ValueError(f"SAMPLE_MAX_FILE_BYTES={settings.sample_max_file_bytes}: {member['relative_path']}")
    if len(samples) > settings.sample_max_files:
        raise ValueError(f"SAMPLE_MAX_FILES={settings.sample_max_files}")
    if sum(m["size_bytes"] for m in samples) > settings.sample_max_total_bytes:
        raise ValueError(f"SAMPLE_MAX_TOTAL_BYTES={settings.sample_max_total_bytes}")
    return samples


async def upload_samples(*, dataset_id, root_path, version_id, manifest_hash, members, post):
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
        with open(path, "rb") as stream:
            content = stream.read(member["size_bytes"] + 1)
        if len(content) != member["size_bytes"] or hashlib.sha256(content).hexdigest() != member["sha256"]:
            raise ValueError(f"sample_member_changed: {member['relative_path']}")
        result = await post(f"/api/v1/vz/versions/{version_id}/samples/{member['index']}", {
            "manifest_hash": manifest_hash,
            "content_base64": base64.b64encode(content).decode("ascii"),
        })
    return result
