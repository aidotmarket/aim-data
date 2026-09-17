import io
from pathlib import Path
from unittest.mock import AsyncMock
from uuid import uuid4

import pytest
from fastapi.testclient import TestClient
from sqlmodel import select

from app.main import app
from app.config import settings
from app.core.database import get_session_context
from app.models.dataset import DatasetMember, DatasetRecord
from app.services import processing_queue
from app.services.source_artifact_resolver import resolve_member_path


@pytest.mark.parametrize('enabled', [False, True])
@pytest.mark.parametrize('route', ['upload', 'batch'])
def test_single_file_original_bytes(monkeypatch, enabled, route):
    monkeypatch.setattr(settings, 'multi_file_datasets_enabled', enabled)
    submit = AsyncMock()
    monkeypatch.setattr(processing_queue.get_processing_queue(), 'submit', submit)
    filename = f'{uuid4()}.csv'; body = b'x\n1\n'
    field = 'file' if route == 'upload' else 'files'
    response = TestClient(app).post('/api/datasets/' + route, files=[(field, (filename, io.BytesIO(body), 'text/csv'))])
    assert response.status_code == 202, response.text
    result = response.json()
    dataset_id = result['dataset_id'] if route == 'upload' else result['items'][0]['dataset_id']
    with get_session_context() as session:
        dataset = session.get(DatasetRecord, dataset_id)
        members = session.exec(select(DatasetMember).where(DatasetMember.dataset_id == dataset_id)).all()
        if enabled:
            assert len(members) == 1
            assert dataset.file_type == 'directory' and dataset.processed_path is None
            assert dataset.storage_filename == Path(dataset.root_path).name
            assert dataset.original_filename == Path(dataset.root_path).name
            assert members[0].relative_path == filename and members[0].role == 'data'
            assert not members[0].is_sample
            assert Path(resolve_member_path(dataset, members[0])).read_bytes() == body
            submit.assert_not_called()
        else:
            assert not members and dataset.root_path is None and dataset.file_type == 'csv'
            assert (Path(settings.upload_directory) / dataset.storage_filename).read_bytes() == body
            submit.assert_awaited_once_with(dataset_id)


def test_delete_uploaded_directory(monkeypatch):
    monkeypatch.setattr(settings, 'multi_file_datasets_enabled', True)
    client = TestClient(app)
    response = client.post('/api/datasets/upload',
                           files={'file': (f'{uuid4()}.csv', b'x\n1\n', 'text/csv')})
    assert response.status_code == 202, response.text
    dataset_id = response.json()['dataset_id']
    with get_session_context() as session:
        root = Path(session.get(DatasetRecord, dataset_id).root_path)
        assert root.is_dir()
        assert session.get(DatasetMember, (dataset_id, 0)) is not None
    response = client.delete(f'/api/datasets/{dataset_id}')
    assert response.status_code == 200, response.text
    assert not root.exists()
    with get_session_context() as session:
        assert session.get(DatasetRecord, dataset_id) is None
        assert not session.exec(select(DatasetMember).where(DatasetMember.dataset_id == dataset_id)).all()
