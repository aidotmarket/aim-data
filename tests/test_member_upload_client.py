"""Sender-only wire tests. Receiver integration is a separate gate."""
import hashlib
from types import SimpleNamespace
from uuid import uuid4

import pytest
from fastapi import HTTPException
from sqlmodel import SQLModel

from app.config import settings
from app.core.database import get_engine, get_session_context
from app.models.dataset import DatasetRecord, DatasetMember
from app.models.published_manifest import PublishedManifest
from app.routers import marketplace_publish as publish
from app.services.marketplace_action_signer import canonical_json_bytes
from app.services.marketplace_push_service import upload_member_chunks
from app.services.sample_upload_client import upload_samples, validate_sample_members


@pytest.fixture
def local_dataset(monkeypatch, tmp_path):
    monkeypatch.setattr(settings, 'multi_file_datasets_enabled', True)
    SQLModel.metadata.create_all(get_engine())
    dataset_id = str(uuid4())
    with get_session_context() as session:
        session.add(DatasetRecord(id=dataset_id, original_filename='set', storage_filename='set',
            root_path=str(tmp_path), file_type='directory'))
        session.flush()
        for index in range(3):
            content = f'{index}\n'.encode()
            name = f'{index}.csv'; (tmp_path / name).write_bytes(content)
            session.add(DatasetMember(dataset_id=dataset_id, index=index, relative_path=name,
                size_bytes=len(content), sha256=hashlib.sha256(content).hexdigest(), detected_type='csv',
                role='data', is_sample=index == 0))
        session.commit()
    return dataset_id


@pytest.mark.asyncio
async def test_chunk_failure_resumes_only_unacknowledged_chunk(monkeypatch):
    monkeypatch.setattr(settings, 'multi_file_datasets_enabled', True)
    monkeypatch.setattr(settings, 'publish_member_chunk', 1000)
    members = [{'index': n} for n in range(2101)]
    seen = []; acknowledged = []
    async def post(path, body):
        seen.append(body['members'][0]['index'])
        assert path == '/api/v1/vz/versions/version/members'
        assert body['members_upload_id'] == 'upload'
        assert len(body['members']) <= 1000
        if len(seen) == 2:
            raise RuntimeError('interrupted')
        return {'status': 'pending_members'}
    def checkpoint(offset, result):
        acknowledged.append(offset)
    with pytest.raises(RuntimeError):
        await upload_member_chunks(version_id='version', members_upload_id='upload', members=members,
            post=post, checkpoint=checkpoint)
    assert acknowledged == [1000]
    result = await upload_member_chunks(version_id='version', members_upload_id='upload', members=members,
        post=post, checkpoint=checkpoint, start_offset=acknowledged[-1])
    assert seen == [0, 1000, 1000, 2000]
    assert acknowledged == [1000, 2000, 2101]
    assert result['status'] == 'pending_members'


def test_snapshot_survives_live_edit_and_distinct_roots(local_dataset):
    local = publish._local_publish_snapshot(local_dataset, 'v1')
    publish._record_local_publish(local, 'listing', 'version', 'pending_members')
    with get_session_context() as session:
        row = session.get(DatasetMember, (local_dataset, 0)); row.is_sample = False
        session.add(row); session.commit()
        retained = session.get(PublishedManifest, ('version', local['manifest']['manifest_hash']))
        assert retained.members[0]['is_sample'] is True
        assert retained.root_path == local['root_path']
    resumed = publish._local_publish_snapshot(local_dataset)
    assert resumed['manifest'] == local['manifest']
    assert resumed['version'].members_upload_id == local['version'].members_upload_id
    other = dict(local, root_path='/another/root')
    publish._record_local_publish(other, 'listing2', 'version2', 'pending_members')
    with get_session_context() as session:
        assert session.get(PublishedManifest, ('version2', local['manifest']['manifest_hash'])).root_path == '/another/root'
    with pytest.raises(HTTPException, match='published_manifest_conflict'):
        publish._record_local_publish(other, 'listing', 'version', 'pending_members')


@pytest.mark.asyncio
async def test_pending_members_status(local_dataset):
    local = publish._local_publish_snapshot(local_dataset)
    publish._record_local_publish(local, 'listing', str(uuid4()), 'pending_members')
    publish._local_progress(local_dataset, 2, 'pending_members')
    result = await publish.publish_status(dataset_id=local_dataset, user=None)
    assert result['status'] == 'pending_members' and result['offset'] == 2


@pytest.mark.asyncio
async def test_sample_upload_original_bytes_only(local_dataset):
    local = publish._local_publish_snapshot(local_dataset)
    calls = []
    async def post(path, body): calls.append((path, body))
    await upload_samples(dataset_id=local_dataset, root_path=local['root_path'], version_id='version',
        manifest_hash=local['manifest']['manifest_hash'], members=local['manifest']['members'], post=post)
    import base64
    assert len(calls) == 1
    assert base64.b64decode(calls[0][1]['content_base64']) == b'0\n'
    assert calls[0][1]['manifest_hash'] == local['manifest']['manifest_hash']


@pytest.mark.asyncio
async def test_sample_cap_preflights_all_before_upload(local_dataset, monkeypatch):
    local = publish._local_publish_snapshot(local_dataset)
    members = local['manifest']['members']
    members[1]['is_sample'] = True; members[1]['size_bytes'] = 11
    monkeypatch.setattr(settings, 'sample_max_file_bytes', 10)
    async def post(*args): pytest.fail('No upload allowed before bounds checked')
    with pytest.raises(ValueError, match='SAMPLE_MAX_FILE_BYTES=10: 1.csv'):
        await upload_samples(dataset_id=local_dataset, root_path=local['root_path'], version_id='v',
            manifest_hash='hash', members=members, post=post)


@pytest.mark.parametrize('bound,value', [('sample_max_files', 1), ('sample_max_total_bytes', 3)])
def test_sample_aggregate_bounds(local_dataset, monkeypatch, bound, value):
    local = publish._local_publish_snapshot(local_dataset)
    members = local['manifest']['members']; members[1]['is_sample'] = True
    monkeypatch.setattr(settings, bound, value)
    with pytest.raises(ValueError, match=bound.upper()): validate_sample_members(members)


@pytest.mark.asyncio
async def test_changed_sample_refused(local_dataset):
    local = publish._local_publish_snapshot(local_dataset)
    from pathlib import Path
    (Path(local['root_path']) / '0.csv').write_bytes(b'X\n')
    async def post(*args): pytest.fail('Changed sample must not upload')
    with pytest.raises(ValueError, match='sample_member_changed: 0.csv'):
        await upload_samples(dataset_id=local_dataset, root_path=local['root_path'], version_id='v',
            manifest_hash='hash', members=local['manifest']['members'], post=post)


def test_worst_case_path_chunk_under_receiver_body_limit():
    members = [dict(index=n, relative_path='\u0800' * 340 + f'{n:04}', size_bytes=0,
        sha256='0'*64, detected_type='csv', role='data', is_sample=False) for n in range(1000)]
    body = {'members_upload_id': str(uuid4()), 'members': members}
    assert len(canonical_json_bytes(body)) < 8 * 1024 * 1024


@pytest.mark.asyncio
async def test_flag_off_upload_unreachable(monkeypatch):
    monkeypatch.setattr(settings, 'multi_file_datasets_enabled', False)
    async def post(*args): pytest.fail('flag off')
    with pytest.raises(Exception, match='multi_file_datasets_disabled'):
        await upload_member_chunks(version_id='v', members_upload_id='u', members=[{}], post=post,
            checkpoint=lambda *args: None)
