"""Complete D8 enumeration and atomic live-member registration.

Only metadata is accumulated (bounded by DATASET_MAX_MEMBERS); file bodies are
streamed twice to establish a stable digest. No profiling runs here.
"""
from __future__ import annotations

import hashlib
import json
import os
import stat
import uuid
from datetime import datetime, timezone
from pathlib import Path

from sqlmodel import select

from app.config import settings
from app.models.dataset import DatasetMember, DatasetRecord
from app.services.dataset_manifest import build_manifest, canonical_path
from app.services.import_service import JUNK_FILES, validate_import_path


class DirectoryRegistrationError(ValueError):
    pass


def require_enabled():
    if not settings.multi_file_datasets_enabled:
        raise DirectoryRegistrationError("Multi-file datasets are disabled")


def _stamp(s):
    return s.st_dev, s.st_ino, s.st_size, s.st_mtime_ns, s.st_ctime_ns


def _digest(fd, max_bytes, entry):
    os.lseek(fd, 0, os.SEEK_SET)
    digest = hashlib.sha256()
    count = 0
    while block := os.read(fd, 1024 * 1024):
        count += len(block)
        if count > max_bytes:
            raise DirectoryRegistrationError(f"DATASET_MAX_BYTES={settings.dataset_max_bytes}: {ascii(entry)}")
        digest.update(block)
    return digest.hexdigest(), count


def stable_read(name, *, dir_fd=None, entry=None, max_bytes=None):
    """Two consecutive stat-bracketed reads must match, including ctime/inode."""
    entry = entry or str(name)
    previous = None
    max_bytes = settings.dataset_max_bytes if max_bytes is None else max_bytes
    try:
        for _ in range(settings.directory_read_max_attempts):
            fd = os.open(name, os.O_RDONLY | os.O_NOFOLLOW | os.O_NONBLOCK, dir_fd=dir_fd)
            try:
                before = os.fstat(fd)
                if not stat.S_ISREG(before.st_mode):
                    raise DirectoryRegistrationError(f"Not a regular file: {ascii(entry)}")
                if before.st_size > max_bytes:
                    raise DirectoryRegistrationError(f"DATASET_MAX_BYTES={settings.dataset_max_bytes}: {ascii(entry)}")
                digest, count = _digest(fd, max_bytes, entry)
                after = os.fstat(fd)
                named = os.stat(name, dir_fd=dir_fd, follow_symlinks=False)
                current = (_stamp(after), digest)
                if _stamp(before) == _stamp(after) == _stamp(named) and count == after.st_size:
                    if previous == current:
                        return digest, after
                    previous = current
                else:
                    previous = None
            finally:
                os.close(fd)
    except OSError as exc:
        raise DirectoryRegistrationError(f"Unreadable entry: {ascii(entry)} ({exc.__class__.__name__})") from None
    raise DirectoryRegistrationError(
        f"DIRECTORY_READ_MAX_ATTEMPTS={settings.directory_read_max_attempts}: changing entry {ascii(entry)}"
    )


def suggested_role(path, detected_type):
    name = path.rsplit("/", 1)[-1].upper()
    if name.startswith(("README", "HOW_TO_USE", "LICENSE")) or "DESCRIPTION" in name:
        return "documentation"
    return "other" if detected_type == "unsupported" else "data"


def _walk(root):
    """Iterative scandir recursion with no Python recursion/depth cutoff.

    Open directory descriptors and O_NOFOLLOW prevent entry replacement from
    turning traversal into a symlink escape. Descriptors live only for the DFS.
    """
    stack = []
    entry_name = str(root)
    try:
        fd = os.open(root, os.O_RDONLY | os.O_DIRECTORY | os.O_NOFOLLOW)
        try:
            iterator = os.scandir(fd)
        except BaseException:
            os.close(fd)
            raise
        stack.append((fd, iterator, ""))
        seen = {}
        while stack:
            parent_fd, entries, prefix = stack[-1]
            entry_name = prefix or str(root)
            try:
                entry = next(entries)
            except StopIteration:
                entries.close()
                os.close(parent_fd)
                stack.pop()
                continue
            entry_name = prefix + entry.name
            # Even excluded entries must have decodable names.
            try:
                entry.name.encode("utf-8")
            except UnicodeError:
                raise DirectoryRegistrationError(f"Non-UTF-8 entry: {ascii(entry_name)}") from None
            if entry.name.startswith(".") or entry.name in JUNK_FILES or entry.is_symlink():
                continue
            canonical = canonical_path(entry_name)
            key = canonical.casefold()
            if key in seen:
                raise DirectoryRegistrationError(f"Path collision: {ascii(seen[key])} and {ascii(entry_name)}")
            seen[key] = entry_name
            info = entry.stat(follow_symlinks=False)
            if stat.S_ISDIR(info.st_mode):
                child_fd = os.open(entry.name, os.O_RDONLY | os.O_DIRECTORY | os.O_NOFOLLOW, dir_fd=parent_fd)
                try:
                    child_entries = os.scandir(child_fd)
                except BaseException:
                    os.close(child_fd)
                    raise
                stack.append((child_fd, child_entries, entry_name + "/"))
            elif stat.S_ISREG(info.st_mode):
                yield parent_fd, entry.name, canonical, info
    except OSError as exc:
        raise DirectoryRegistrationError(f"Unreadable entry: {ascii(entry_name)} ({exc.__class__.__name__})") from None
    finally:
        for fd, entries, _ in reversed(stack):
            entries.close()
            os.close(fd)


def scan_directory(path):
    require_enabled()
    root = validate_import_path(str(path))
    return root, _scan_root(root)


def _scan_root(root):
    from app.services.processing_service import PROCESSABLE_TYPES
    rows = []
    total = 0
    walk = _walk(root)
    try:
        for fd, name, path, info in walk:
            if len(rows) >= settings.dataset_max_members:
                raise DirectoryRegistrationError(f"DATASET_MAX_MEMBERS={settings.dataset_max_members}: {ascii(path)}")
            digest, info = stable_read(name, dir_fd=fd, entry=path, max_bytes=settings.dataset_max_bytes - total)
            total += info.st_size
            detected = Path(name).suffix.lower().lstrip(".")
            detected = detected if detected in PROCESSABLE_TYPES else "unsupported"
            rows.append(dict(relative_path=path, size_bytes=info.st_size, sha256=digest,
                             detected_type=detected, role=suggested_role(path, detected), is_sample=False,
                             status="unsupported" if detected == "unsupported" else "current",
                             reason="unsupported_type" if detected == "unsupported" else None,
                             mtime=datetime.fromtimestamp(info.st_mtime, timezone.utc)))
    finally:
        walk.close()
    return sorted(rows, key=lambda row: row["relative_path"])


def refresh_directory_metadata(session, dataset):
    members = session.exec(select(DatasetMember).where(
        DatasetMember.dataset_id == dataset.id, DatasetMember.status != "removed"
    ).order_by(DatasetMember.index)).all()
    metadata = json.loads(dataset.metadata_json or "{}")
    if any(m.status == "missing" for m in members):
        metadata.pop("directory", None)
    else:
        manifest = build_manifest(members)
        metadata["directory"] = {k: v for k, v in manifest.items() if k != "members"}
        dataset.file_size_bytes = manifest["total_data_bytes"]
    dataset.metadata_json = json.dumps(metadata)
    session.add(dataset)


def _store(session, root, rows, dataset=None):
    if dataset is None:
        dataset = DatasetRecord(id=str(uuid.uuid4()), original_filename=root.name,
                                storage_filename=root.name, file_type="directory", root_path=str(root))
        session.add(dataset)
        session.flush()
    else:
        dataset.root_path = str(root)
        dataset.file_type = "directory"
        dataset.original_filename = root.name
        dataset.storage_filename = root.name
        dataset.processed_path = None
    old = {m.relative_path: m for m in session.exec(select(DatasetMember).where(DatasetMember.dataset_id == dataset.id)).all()}
    next_index = max((m.index for m in old.values()), default=-1) + 1
    now = datetime.now(timezone.utc)
    present = set()
    for values in rows:
        path = values["relative_path"]
        present.add(path)
        member = old.get(path)
        if member is None:
            member = DatasetMember(dataset_id=dataset.id, index=next_index, **values)
            next_index += 1
            session.add(member)
        else:
            # Preserve the seller's role and sample choices, including on return.
            changed = False
            for key, value in values.items():
                if key in ("role", "is_sample"):
                    continue
                old_value = getattr(member, key)
                if key == "mtime" and old_value is not None:
                    old_value = old_value.replace(tzinfo=timezone.utc)
                if old_value != value:
                    setattr(member, key, value)
                    changed = True
            if changed:
                member.updated_at = now
                session.add(member)
    for path, member in old.items():
        if path not in present and member.status != "removed":
            member.status = "removed"
            member.updated_at = now
            session.add(member)
    session.flush()
    refresh_directory_metadata(session, dataset)
    session.flush()
    return dataset


def register_directory(session, path, *, dataset_id=None):
    """Caller owns the transaction; traversal refuses before any database write."""
    root, rows = scan_directory(path)
    if dataset_id:
        dataset = session.get(DatasetRecord, dataset_id)
        if dataset is None:
            raise DirectoryRegistrationError("Dataset not found")
    else:
        dataset = session.exec(select(DatasetRecord).where(
            DatasetRecord.root_path == str(root), DatasetRecord.file_type == "directory"
        )).first()
    return _store(session, root, rows, dataset)


def register_uploaded_file(session, dataset_id, upload_path, original_filename):
    """Internal upload path; never expose a caller-controlled validation bypass."""
    require_enabled()
    dataset = session.get(DatasetRecord, dataset_id)
    if dataset is None:
        raise DirectoryRegistrationError("Dataset not found")
    source = Path(upload_path)
    upload_root = Path(settings.upload_directory).resolve()
    if not source.resolve().is_relative_to(upload_root) or source.is_symlink():
        raise DirectoryRegistrationError("Upload outside storage directory")
    filename = canonical_path(original_filename)
    if "/" in filename:
        raise DirectoryRegistrationError("Upload filename must be a basename")
    root = upload_root / dataset.id
    root.mkdir(exist_ok=False)
    target = root / filename
    try:
        source.rename(target)
        digest, info = stable_read(target)
        values = dict(relative_path=filename, size_bytes=info.st_size, sha256=digest,
                      detected_type=dataset.file_type, role="data", is_sample=False,
                      status="current", reason=None, mtime=datetime.fromtimestamp(info.st_mtime, timezone.utc))
        return _store(session, root, [values], dataset)
    except BaseException:
        if target.exists():
            target.rename(source)
        root.rmdir()
        raise
