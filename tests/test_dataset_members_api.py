from pathlib import Path
from types import SimpleNamespace

import pytest
from fastapi import FastAPI
from fastapi.testclient import TestClient
from sqlmodel import select

from app.config import settings
from app.core.database import get_session_context
from app.models.dataset import DatasetMember, DatasetRecord
from app.routers import datasets
from app.services import import_service, directory_registration as reg


@pytest.fixture
def client(tmp_path, monkeypatch):
    monkeypatch.setattr(settings, 'multi_file_datasets_enabled', True)
    monkeypatch.setattr(import_service, 'IMPORT_ROOT', tmp_path.resolve())
    app = FastAPI()
    app.include_router(datasets.router, prefix='/datasets')
    app.dependency_overrides[datasets.get_current_user] = lambda: SimpleNamespace(user_id='seller')
    with TestClient(app) as client:
        yield client


def register(client, root):
    response = client.post('/datasets/register-directory', json={'path': str(root)})
    assert response.status_code == 200, response.text
    return response.json()['dataset_id']


def test_role_defaults_edit_sample_freeze(client, tmp_path):
    root = tmp_path / 'roles'; root.mkdir()
    names = ['README.md', 'HOW_TO_USE.txt', 'MY_DESCRIPTION.txt', 'LICENSE', 'binary.bin', 'sample.csv']
    for name in names: (root / name).write_bytes(b'x')
    dataset_id = register(client, root)
    url = f'/datasets/{dataset_id}/members'
    page = client.get(url).json()
    members = {m['relative_path']: m for m in page['members']}
    assert all(members[n]['role'] == 'documentation' for n in names[:4])
    assert members['binary.bin']['role'] == 'other'
    assert members['sample.csv']['role'] == 'data'
    assert not any(m['is_sample'] for m in members.values())
    doc = f"{url}/{members['README.md']['index']}"
    data = f"{url}/{members['sample.csv']['index']}"
    assert client.patch(doc, json={'is_sample': True}).status_code == 422
    assert client.patch(data, json={'is_sample': True}).json()['is_sample'] is True
    changed = client.patch(data, json={'role': 'documentation'}).json()
    assert changed['role'] == 'documentation' and changed['is_sample'] is False
    assert client.patch(data, json={'role': 'other', 'is_sample': True}).status_code == 422
    with get_session_context() as session:
        record = session.get(DatasetRecord, dataset_id); record.listing_id = 'published'
        session.add(record); session.commit()
    assert client.patch(data, json={'role': 'data'}).status_code == 409
    assert client.get(url).json()['editable'] is False


def test_paging_filters_and_idempotency(client, tmp_path):
    root = tmp_path / 'pages'; root.mkdir()
    for i in range(103): (root / f'{i:03}.csv').write_bytes(b'')
    dataset_id = register(client, root)
    assert register(client, root) == dataset_id
    url = f'/datasets/{dataset_id}/members'
    assert len(client.get(url).json()['members']) == 100
    result = client.get(url + '?page=2&role=data&status=current').json()
    assert result['total'] == 103 and len(result['members']) == 3
    assert result['members'][0]['index'] == 100
    assert client.get(url + '?role=documentation').json()['total'] == 0
    assert client.get(url + '?page=0').status_code == 422
    assert client.get(url + '?role=bad').status_code == 422


def test_flag_off_all_new_endpoints(client, monkeypatch):
    monkeypatch.setattr(settings, 'multi_file_datasets_enabled', False)
    assert client.post('/datasets/register-directory', json={'path': '/imports'}).status_code == 404
    assert client.get('/datasets/anything/members').status_code == 404
    assert client.patch('/datasets/anything/members/0', json={'role': 'data'}).status_code == 404


def test_non_utf8_refused_whole_and_named(client, tmp_path, monkeypatch):
    root = tmp_path / 'invalid'; root.mkdir()
    # readdir returns surrogateescaped names on Linux. APFS refuses creation;
    # inject that exact readdir result so the refusal is portable.
    class Entries:
        def __init__(self): self.items = iter([SimpleNamespace(name='bad\udcff.csv')])
        def __next__(self): return next(self.items)
        def close(self): pass
    monkeypatch.setattr(reg.os, 'scandir', lambda fd: Entries())
    response = client.post('/datasets/register-directory', json={'path': str(root)})
    assert response.status_code == 422
    assert 'Non-UTF-8' in response.text and 'bad' in response.text
    with get_session_context() as session:
        assert not session.exec(select(DatasetRecord).where(DatasetRecord.root_path == str(root))).all()


@pytest.mark.parametrize('patch', [{}, {'role': None}, {'is_sample': None}, {'is_sample': 'true'}, {'role': 'bad'}])
def test_invalid_patch(client, patch):
    assert client.patch('/datasets/missing/members/0', json=patch).status_code == 422
