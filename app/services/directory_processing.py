"""S1717 D2: bounded, deterministic set profiling; original bytes stay untouched.

Registration status is deliberately separate from profiling status. Outcomes live
in metadata_json.directory_profile.members, keyed by the stable member index.
No schema migration and no alteration of the manifest's seven-field preimage.
"""
from __future__ import annotations

import asyncio
import json
import hashlib
import multiprocessing
import tempfile
import time
from datetime import datetime, timezone
from pathlib import Path
from types import SimpleNamespace

from sqlmodel import select

from app.config import settings
from app.core.database import get_session_context
from app.models.dataset import DatasetMember, DatasetRecord

# One event-loop owner per dataset, including simultaneous pipeline entry points.
_running: dict[str, asyncio.Task] = {}


def directory_record(dataset_id):
    if not settings.multi_file_datasets_enabled:
        return None
    with get_session_context() as session:
        record = session.get(DatasetRecord, dataset_id)
        if record and record.file_type == "directory":
            session.expunge(record)
            return record
    return None


def profile_snapshot(dataset_id):
    record = directory_record(dataset_id)
    if record is None:
        raise ValueError("Directory dataset unavailable")
    return json.loads(record.metadata_json).get("directory_profile", {
        "status": "not_started", "reason": "Profiling has not started", "members": {},
    })


def pipeline_status(dataset_id):
    profile = profile_snapshot(dataset_id)
    state = profile["status"]
    return {"dataset_id": dataset_id,
            "status": "running" if state == "running" else "success" if state in ("completed", "profiling_skipped") else "pending" if state == "not_started" else "failed",
            "message": profile.get("summary", profile.get("reason", "")),
            "steps": {"directory_profile": {"status": state, "error": profile.get("reason")}},
            "output_files": {}, "directory_profile": profile}


def _save(dataset_id, profile, *, listing=None):
    with get_session_context() as session:
        record = session.get(DatasetRecord, dataset_id)
        if record is None:
            return
        metadata = json.loads(record.metadata_json or "{}")
        metadata["directory_profile"] = profile
        if listing is not None:
            metadata["listing_metadata"] = listing
        record.metadata_json = json.dumps(metadata, default=str)
        record.status = "extracting" if profile["status"] == "running" else "preview_ready"
        record.updated_at = datetime.now(timezone.utc)
        session.add(record)
        session.commit()


def _worker(send, operation, payload, output_directory, multi_file_datasets_enabled):
    """Spawn-safe, DB-free worker. Parent kills it on deadline or cancellation."""
    try:
        settings.multi_file_datasets_enabled = multi_file_datasets_enabled
        if operation == "member":
            from app.services.processing_service import ProcessingService
            from app.services.source_artifact_resolver import resolve_member_path
            dataset_values, member_values = payload
            dataset, member = SimpleNamespace(**dataset_values), SimpleNamespace(**member_values)
            path = resolve_member_path(dataset, member)
            if path is None:
                raise ValueError("Member source unavailable")
            path = Path(path)
            if path.stat().st_size != member.size_bytes:
                raise ValueError("Member size changed since registration")
            directory = Path(output_directory)
            # Freeze a size-bounded private input. A growing source cannot make
            # an extractor read beyond the accounted input budget.
            snapshot = directory / ("input." + member.detected_type)
            with path.open("rb") as source, snapshot.open("wb") as target:
                remaining = member.size_bytes
                while remaining:
                    block = source.read(min(1024 * 1024, remaining))
                    if not block:
                        raise ValueError("Member size changed since registration")
                    target.write(block)
                    remaining -= len(block)
                if source.read(1):
                    raise ValueError("Member size changed since registration")
            processor = ProcessingService.__new__(ProcessingService)
            result = processor.profile_member(snapshot, member.detected_type, directory)
        elif operation == "documentation":
            from app.services.source_artifact_resolver import resolve_member_path
            dataset_values, member_values, limit = payload
            path = resolve_member_path(SimpleNamespace(**dataset_values), SimpleNamespace(**member_values))
            if path is None:
                raise ValueError("Documentation source unavailable")
            with open(path, "rb") as stream:
                result = stream.read(limit).decode("utf-8", errors="replace")
        elif operation == "pii":
            from app.services.pii_service import get_pii_service
            result = get_pii_service().scan_text_content(payload, sample_size=max(1, len(payload)))
        else:
            raise ValueError("Unknown worker operation")
        send.send((True, result))
    except Exception as exc:
        # Never persist parser exception text containing seller bytes or paths.
        send.send((False, type(exc).__name__))
    finally:
        send.close()


async def _isolated(operation, payload, deadline):
    if time.monotonic() >= deadline:
        raise TimeoutError
    context = multiprocessing.get_context("spawn")
    receive, send = context.Pipe(duplex=False)
    scratch = tempfile.TemporaryDirectory(prefix="aim-profile-")
    process = context.Process(target=_worker, args=(send, operation, payload, scratch.name, settings.multi_file_datasets_enabled), daemon=True)
    try:
        process.start()
        send.close()
        while not receive.poll():
            remaining = deadline - time.monotonic()
            if remaining <= 0:
                raise TimeoutError
            if not process.is_alive():
                raise ValueError("Worker exited without a result")
            await asyncio.sleep(min(0.01, remaining))
        ok, result = receive.recv()
        if not ok:
            raise ValueError(result)
        return result
    finally:
        if process.is_alive():
            process.kill()
        process.join(timeout=0.2)
        receive.close()
        process.close()
        send.close()
        scratch.cleanup()


async def process_directory(dataset_id):
    if directory_record(dataset_id) is None:
        raise ValueError("Directory dataset unavailable")
    if dataset_id in _running:
        return await asyncio.shield(_running[dataset_id])
    task = asyncio.create_task(_process(dataset_id))
    _running[dataset_id] = task
    task.add_done_callback(lambda done: _running.pop(dataset_id, None))
    try:
        return await asyncio.shield(task)
    finally:
        if task.done():
            _running.pop(dataset_id, None)


async def _process(dataset_id):
    started = time.monotonic()
    deadline = started + settings.profile_timeout_s
    record = directory_record(dataset_id)
    with get_session_context() as session:
        members = session.exec(select(DatasetMember).where(
            DatasetMember.dataset_id == dataset_id, DatasetMember.status != "removed"
        ).order_by(DatasetMember.sha256, DatasetMember.relative_path)).all()
        values = [member.model_dump() for member in members]
    # Local cache identity only; never a manifest or a published commitment.
    source_key = hashlib.sha256(json.dumps({
        "root": record.root_path,
        "members": [[m[k] for k in ("index", "relative_path", "sha256", "size_bytes", "role", "status")] for m in values],
        "policy": [settings.profile_max_members, settings.profile_max_member_bytes,
                   settings.profile_max_total_bytes, settings.profile_timeout_s,
                   settings.profile_docs_context_bytes],
    }, sort_keys=True).encode()).hexdigest()
    previous = json.loads(record.metadata_json or "{}").get("directory_profile", {})
    if previous.get("source_key") == source_key and previous.get("status") in ("completed", "profiling_skipped"):
        return pipeline_status(dataset_id)
    data = [member for member in values if member["role"] == "data"]
    profile = {"status": "running", "source_key": source_key, "reason": None, "profiled_members": 0,
               "total_data_members": len(data), "profiled_bytes": 0, "read_bytes": 0,
               "members": {}, "schema_count": 0}
    _save(dataset_id, profile)
    results, texts, documentation = [], [], []
    attempted = 0
    schemas = set()
    listing = None
    from app.services.processing_service import PROCESSABLE_TYPES
    from app.services.listing_metadata_service import get_listing_metadata_service
    author = get_listing_metadata_service()
    try:
        for member in data:
            outcome = {"status": "pending", "reason": None, "relative_path": member["relative_path"]}
            profile["members"][str(member["index"])] = outcome
            reason = None
            if member["detected_type"] not in PROCESSABLE_TYPES:
                reason = "unsupported_type"
            elif member["status"] == "missing":
                reason = "parse_failed"
            elif member["size_bytes"] > settings.profile_max_member_bytes:
                reason = f"too_large: PROFILE_MAX_MEMBER_BYTES={settings.profile_max_member_bytes}"
            elif attempted >= settings.profile_max_members:
                outcome.update(status="too_large", reason=f"member limit {settings.profile_max_members} reached")
                continue
            elif profile["read_bytes"] + member["size_bytes"] > settings.profile_max_total_bytes:
                reason = f"too_large: PROFILE_MAX_TOTAL_BYTES={settings.profile_max_total_bytes}"
            elif time.monotonic() >= deadline:
                reason = f"timeout: PROFILE_TIMEOUT_S={settings.profile_timeout_s}"
            if reason:
                outcome.update(status=reason.split(":")[0], reason=reason)
                continue
            attempted += 1
            profile["read_bytes"] += member["size_bytes"]
            try:
                result = await _isolated("member", (record.model_dump(), member), deadline)
                # A successful parse with zero rows is terminal too.
                outcome.update(status="profiled", profile=result)
                results.append(result)
                profile["profiled_members"] += 1
                profile["profiled_bytes"] += member["size_bytes"]
                columns = result.get("columns", [])
                schemas.add(json.dumps(columns, sort_keys=True, default=str))
                sample_rows = result.get("sample_rows", [])
                if any(value is not None and str(value).strip()
                       for row in sample_rows for value in row.values()):
                    texts.append(json.dumps(sample_rows, default=str)[:10000])
            except TimeoutError:
                outcome.update(status="timeout", reason=f"timeout: PROFILE_TIMEOUT_S={settings.profile_timeout_s}")
            except Exception:
                outcome.update(status="parse_failed", reason="parse_failed: Member extraction failed")
        # Documentation is inert UTF-8 quoted context, bounded across the set.
        # It shares the byte budget, with a separate PROFILE_MAX_MEMBERS cap.
        docs_left = min(settings.profile_docs_context_bytes, settings.profile_max_total_bytes - profile["read_bytes"])
        docs_attempted = 0
        for member in values:
            if docs_attempted >= settings.profile_max_members:
                break
            if member["role"] != "documentation" or docs_left <= 0 or time.monotonic() >= deadline:
                continue
            if member["size_bytes"] > settings.profile_max_member_bytes:
                continue
            limit = min(docs_left, member["size_bytes"])
            if not limit:
                continue
            docs_attempted += 1
            try:
                text = await _isolated("documentation", (record.model_dump(), member, limit), deadline)
                documentation.append({"relative_path": member["relative_path"], "quoted_text": text})
            except Exception:
                pass
            docs_left -= limit
            profile["read_bytes"] += limit
        profile["schema_count"] = len(schemas)
        profile["row_count"] = sum(r.get("row_count", 0) or 0 for r in results)
        profile["column_count"] = max((r.get("column_count", 0) or 0 for r in results), default=0)
        try:
            profile["pii"] = await _isolated("pii", texts, deadline)
            profile["pii"]["scope"] = "Bounded member previews only; not a whole-set clearance"
            if profile["profiled_members"] == 0 or not texts:
                profile["pii"]["privacy_score"] = None
        except TimeoutError:
            profile["pii"] = {"status": "timeout", "reason": f"PROFILE_TIMEOUT_S={settings.profile_timeout_s}"}
        except Exception:
            profile["pii"] = {"status": "failed", "reason": "PII scan unavailable"}
        _finish(profile)
        remaining = deadline - time.monotonic()
        if remaining > 0:
            try:
                listing = await asyncio.wait_for(author.author_directory_metadata(record, profile, documentation), remaining)
            except TimeoutError:
                profile["metadata_reason"] = f"timeout: PROFILE_TIMEOUT_S={settings.profile_timeout_s}"
            except Exception:
                profile["metadata_reason"] = "Metadata authoring unavailable"
    finally:
        _finish(profile)
        if listing is None:
            listing = author.directory_metadata_fallback(record, profile).model_dump()
        profile["elapsed_seconds"] = round(time.monotonic() - started, 3)
        _save(dataset_id, profile, listing=listing)
    return pipeline_status(dataset_id)


def _finish(profile):
    for member in profile["members"].values():
        if member["status"] == "pending":
            member.update(status="parse_failed", reason="parse_failed: Profiling interrupted before extraction completed")
    n, total = profile["profiled_members"], profile["total_data_members"]
    profile["status"] = "completed" if n else "profiling_skipped"
    profile["summary"] = f"profiled on {n} of {total} files" if n else f"not profiled (0 of {total} files)"
    profile["reason"] = None if n else "No data member could be profiled within the configured limits; see member reasons"
