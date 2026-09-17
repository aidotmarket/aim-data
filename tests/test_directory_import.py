from types import SimpleNamespace
from unittest.mock import AsyncMock, Mock

import pytest
from fastapi import FastAPI
from fastapi.testclient import TestClient
from sqlmodel import select

from app.config import settings
from app.core.database import get_session_context
from app.models.dataset import DatasetMember
from app.routers import imports
from app.services import import_service


@pytest.mark.parametrize('enabled', [False, True])
def test_directory_import_flag(tmp_path, monkeypatch, enabled):
    monkeypatch.setattr(settings, 'multi_file_datasets_enabled', enabled)
    monkeypatch.setattr(import_service, 'IMPORT_ROOT', tmp_path.resolve())
    root = tmp_path / 'directory'; root.mkdir()
    (root / 'a.csv').write_bytes(b'x')
    (root / 'b.csv').write_bytes(b'y')
    svc = import_service.ImportService()
    legacy_job = import_service.ImportJob(job_id='legacy', files=[
        import_service.ImportFileEntry('a.csv', str(root / 'a.csv'), 1),
        import_service.ImportFileEntry('b.csv', str(root / 'b.csv'), 1)])
    svc.start_import = Mock(return_value=legacy_job)
    svc.run_import = AsyncMock()
    app = FastAPI(); app.include_router(imports.router, prefix='/imports')
    app.dependency_overrides[imports.get_current_user] = lambda: SimpleNamespace(user_id='seller')
    app.dependency_overrides[imports.get_import_service] = lambda: svc
    with TestClient(app) as client:
        scanned = client.post('/imports/scan', json={'path': str(root)}).json()
        assert scanned['total_files'] == 2
        response = client.post('/imports/start', json={'path': str(root), 'files': ['a.csv','b.csv']})
    assert response.status_code == 200, response.text
    if enabled:
        svc.start_import.assert_not_called(); svc.run_import.assert_not_called()
        with get_session_context() as session:
            assert len(session.exec(select(DatasetMember).where(
                DatasetMember.dataset_id == response.json()['dataset_id'])).all()) == 2
    else:
        svc.start_import.assert_called_once_with(str(root), ['a.csv','b.csv'])
        svc.run_import.assert_awaited_once_with(legacy_job)
        assert response.json() == {'job_id':'legacy','total_files':2,'total_bytes':0,'status':'running'}
