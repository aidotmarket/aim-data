"""Directory PII uses the stored bounded profile; file scans keep their contract."""
from inspect import signature
from types import SimpleNamespace
from unittest.mock import Mock

import pytest
from fastapi import FastAPI
from fastapi.testclient import TestClient

from app.routers import pii
from app.services.processing_service import ProcessingStatus


@pytest.fixture
def scan_client():
    record = SimpleNamespace(
        file_type="directory", original_filename="folder", processed_path=None,
        status=ProcessingStatus.PREVIEW_READY, metadata={},
    )
    processing = Mock()
    processing.get_dataset.return_value = record
    scanner = Mock()
    app = FastAPI()
    app.include_router(pii.router, prefix="/api/pii")
    app.dependency_overrides[pii.get_processing_service] = lambda: processing
    app.dependency_overrides[pii.get_pii_service] = lambda: scanner
    app.dependency_overrides[pii.get_current_user] = lambda: SimpleNamespace(user_id="seller")
    app.dependency_overrides[signature(pii.scan_dataset).parameters["_meter"].default.dependency] = lambda: None
    with TestClient(app) as client:
        yield client, record, processing, scanner


@pytest.mark.parametrize("method", ["get", "post"])
def test_directory_returns_stored_text_scan(scan_client, method):
    client, record, processing, scanner = scan_client
    stored = {"total_columns": 1, "columns_with_pii": 1, "overall_risk": "high",
              "privacy_score": 3, "scan_type": "text_content",
              "scope": "Bounded member previews only; not a whole-set clearance",
              "column_results": [{"column": "document_content", "pii_types": ["EMAIL_ADDRESS"], "risk_level": "high"}]}
    record.metadata = {"directory_profile": {"profiled_members": 1, "pii": stored}}
    response = getattr(client, method)("/api/pii/scan/dir-1")
    assert response.status_code == 200
    assert response.json() == {**stored, "dataset_id": "dir-1", "filename": "folder",
                               "scan_status": "completed", "columns_scanned": 1}
    scanner.scan_structured.assert_not_called()
    processing._save_record.assert_not_called()
    assert record.metadata["directory_profile"]["pii"] == stored


@pytest.mark.parametrize("method", ["get", "post"])
def test_directory_profile_not_run_is_named_conflict(scan_client, method):
    client, _, _, scanner = scan_client
    response = getattr(client, method)("/api/pii/scan/dir-1")
    assert response.status_code == 409
    assert response.json()["detail"].startswith("directory_pii_not_ready:")
    scanner.scan_structured.assert_not_called()


@pytest.mark.parametrize("status", ["timeout", "failed"])
def test_directory_failure_preserves_reason_and_scope(scan_client, status):
    client, record, _, scanner = scan_client
    record.metadata = {"directory_profile": {"profiled_members": 1,
        "pii": {"status": status, "reason": "PII scan unavailable"}}}
    for method in (client.get, client.post):
        response = method("/api/pii/scan/dir-1")
        assert response.status_code == 200
        body = response.json()
        assert body["scan_status"] == status
        assert "status" not in body
        assert body["reason"] == "PII scan unavailable"
        assert body["scope"] == "Bounded member previews only; not a whole-set clearance"
        assert body["privacy_score"] is None
        assert body["overall_risk"] == "unknown"
        assert body["column_results"] == []
    scanner.scan_structured.assert_not_called()


def test_unprofiled_directory_never_claims_privacy_score(scan_client):
    client, record, _, _ = scan_client
    record.metadata = {"directory_profile": {"profiled_members": 0,
        "pii": {"privacy_score": 10, "overall_risk": "none"}}}
    for method in (client.get, client.post):
        assert method("/api/pii/scan/dir-1").json()["privacy_score"] is None


def test_single_file_cached_scan_unchanged(scan_client):
    client, record, _, _ = scan_client
    record.file_type = "csv"
    assert client.get("/api/pii/scan/file-1").status_code == 404
    record.metadata = {"pii_scan": {"overall_risk": "low", "column_results": []}}
    assert client.get("/api/pii/scan/file-1").json() == {
        "dataset_id": "file-1", "filename": "folder", **record.metadata["pii_scan"]}


def test_single_file_post_still_scans_and_saves(scan_client, tmp_path):
    client, record, processing, scanner = scan_client
    record.file_type = "csv"
    record.status = ProcessingStatus.EXTRACTING
    assert client.post("/api/pii/scan/file-1").status_code == 400
    record.status = ProcessingStatus.PREVIEW_READY
    assert client.post("/api/pii/scan/file-1").status_code == 500
    record.processed_path = tmp_path / "data.csv"
    record.processed_path.write_text("x\n1\n")
    scanner.scan_structured.return_value = {"overall_risk": "none", "column_results": []}
    scanner.get_recommendations.return_value = []
    response = client.post("/api/pii/scan/file-1?sample_size=5")
    assert response.status_code == 200
    scanner.scan_structured.assert_called_once_with(filepath=record.processed_path, sample_size=5)
    assert response.json() == {"dataset_id": "file-1", "filename": "folder",
                               **record.metadata["pii_scan"]}
    processing._save_record.assert_called_once()


@pytest.mark.parametrize("method", ["get", "post"])
def test_missing_dataset_unchanged(scan_client, method):
    client, _, processing, _ = scan_client
    processing.get_dataset.return_value = None
    assert getattr(client, method)("/api/pii/scan/missing").status_code == 404
