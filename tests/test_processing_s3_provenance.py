"""S1681/S1710: processing must retain registered S3 publish provenance."""

from uuid import uuid4

import pytest

from app.config import settings
from app.models.dataset import DatasetStatus
from app.services.duckdb_service import DuckDBService
from app.services.processing_service import DatasetRecord, ProcessingService


PROVENANCE = {
    "source_type": "s3",
    "source_connection_id": "test-s3-connection",
    "source_object_key": "seller/data.csv",
    "content_type": "text/csv",
}


@pytest.fixture
def dataset(tmp_path, monkeypatch):
    monkeypatch.setattr(settings, "data_directory", str(tmp_path))
    monkeypatch.setattr(settings, "upload_directory", str(tmp_path / "uploads"))
    monkeypatch.setattr(settings, "processed_directory", str(tmp_path / "processed"))
    service = ProcessingService()
    record = DatasetRecord(str(uuid4()), "data.csv", "csv")
    record.upload_path = service.upload_dir / f"{record.id}.csv"
    record.upload_path.write_text("id,name\n1,Alice\n2,Bob\n")
    # Keep this test local: PII/quality analysis is independent of extraction.
    monkeypatch.setattr(service, "_run_post_extract_analysis", lambda record: None)
    return service, record


@pytest.mark.asyncio
@pytest.mark.parametrize("with_provenance", [True, False])
@pytest.mark.parametrize("extraction_error", [False, True])
async def test_csv_provenance_persisted(
    dataset, monkeypatch, with_provenance, extraction_error
):
    service, record = dataset
    record.metadata = dict(PROVENANCE) if with_provenance else {}
    if extraction_error:

        def fail_metadata(*args, **kwargs):
            raise RuntimeError("metadata unavailable")

        monkeypatch.setattr(DuckDBService, "get_file_metadata", fail_metadata)
    service._save_record(record, record.upload_path.name)

    result = await service.process_file(record.id)
    persisted = service.get_dataset(record.id)

    assert result.status == DatasetStatus.PREVIEW_READY
    assert result.processed_path.exists()
    assert persisted.metadata == result.metadata
    for key, value in PROVENANCE.items():
        if with_provenance:
            assert persisted.metadata[key] == value
        else:
            assert key not in persisted.metadata
    if extraction_error:
        assert persisted.metadata["extraction_error"] == {
            "code": "METADATA_EXTRACTION_FAILED",
            "type": "RuntimeError",
        }
    else:
        assert persisted.metadata["row_count"] == 2
        assert persisted.metadata["column_count"] == 2


@pytest.mark.asyncio
@pytest.mark.parametrize(
    "outcome",
    ["error", "cancel", "streaming", "fallback", "document", "text", "spreadsheet"],
)
async def test_provenance_survives_processing_exits(dataset, monkeypatch, outcome):
    service, record = dataset
    record.metadata = dict(PROVENANCE)
    record.file_type = {"document": "docx", "text": "txt", "spreadsheet": "xlsx"}.get(
        outcome, "csv"
    )
    service._save_record(record, record.upload_path.name)

    def replace_metadata(record, *args):
        record.metadata = {"extracted": True, "content_type": "extracted/type"}
        if outcome in {"error", "fallback"}:
            raise RuntimeError("extraction failed")

    def fallback_metadata(record, *args):
        record.metadata = {"extracted": True}

    monkeypatch.setattr(
        service,
        "_extract_in_memory",
        fallback_metadata if outcome == "fallback" else replace_metadata,
    )
    monkeypatch.setattr(service, "_extract_streaming", replace_metadata)
    monkeypatch.setattr(
        service, "_is_large_file", lambda record: outcome in {"streaming", "fallback"}
    )
    cancellations = iter([False, outcome == "cancel"])
    monkeypatch.setattr(
        service, "_is_cancelled", lambda dataset_id: next(cancellations)
    )

    result = await service.process_file(record.id)

    assert result.status == {
        "error": DatasetStatus.ERROR,
        "cancel": DatasetStatus.CANCELLED,
    }.get(outcome, DatasetStatus.PREVIEW_READY)
    persisted = service.get_dataset(record.id)
    assert persisted.metadata["extracted"] is True
    for key, value in PROVENANCE.items():
        assert persisted.metadata[key] == value
