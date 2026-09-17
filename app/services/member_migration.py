"""D1a explicit, flag-gated backfill; never rewrite legacy row contracts."""
from datetime import datetime, timezone
from pathlib import Path

from sqlmodel import select

from app.config import settings
from app.models.dataset import DatasetMember, DatasetRecord
from app.services.directory_registration import require_enabled, stable_read
from app.services.dataset_manifest import canonical_path
from app.services.source_artifact_resolver import _resolve_file_path


def migrate_members(session):
    """Caller commits. Preserve ids, listing ids, batch ids, paths and statuses.

    Legacy rows keep file_type and processed_path, so legacy delivery continues
    selecting its artifact exactly as before. root_path binds the next publish.
    """
    require_enabled()
    count = 0
    for dataset in session.exec(select(DatasetRecord).order_by(DatasetRecord.id)).all():
        if dataset.file_type == "directory" or session.exec(select(DatasetMember).where(
            DatasetMember.dataset_id == dataset.id
        ).limit(1)).first() is not None:
            continue
        path = _resolve_file_path(dataset, upload_directory=settings.upload_directory,
                                  processed_directory=settings.processed_directory)
        # Missing rows have no invented digest; retain the first recorded locator.
        candidate = Path(path or dataset.processed_path or
                         str(Path(settings.upload_directory) / dataset.storage_filename))
        values = dict(size_bytes=0, sha256=None, status="missing", reason="source_missing", mtime=None)
        if path is not None:
            digest, info = stable_read(path)
            values = dict(size_bytes=info.st_size, sha256=digest, status="current", reason=None,
                          mtime=datetime.fromtimestamp(info.st_mtime, timezone.utc))
        session.add(DatasetMember(dataset_id=dataset.id, index=0,
                                  relative_path=canonical_path(candidate.name),
                                  detected_type=dataset.file_type, role="data", is_sample=False, **values))
        dataset.root_path = str(candidate.parent)
        session.add(dataset)
        count += 1
    session.flush()
    return count
