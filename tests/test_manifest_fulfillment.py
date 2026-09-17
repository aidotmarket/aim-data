"""S1717 D sender against the approved wire statement, no listener imports."""
import asyncio
import base64
import hashlib
import json
from pathlib import Path
from types import SimpleNamespace
from unittest.mock import AsyncMock, MagicMock
import uuid

import pytest
from sqlmodel import SQLModel

from app.config import Settings, settings
from app.core.database import get_engine, get_session_context
from app.models.published_manifest import PublishedManifest
from app.services.dataset_manifest import build_manifest
from app.services.fulfillment_service import FulfillmentService, CHUNK_SIZE, ACK_TIMEOUT_S, WINDOW_SIZE
from app.services.manifest_fulfillment import load_plan, ManifestSender
from app.services.trust_channel_client import TrustChannelClient


@pytest.fixture
def retained(tmp_path, monkeypatch):
    monkeypatch.setattr(settings, 'multi_file_datasets_enabled', True)
    monkeypatch.setattr(settings, 'transfer_retry_after_s', 0.001)
    SQLModel.metadata.create_all(get_engine())
    root = tmp_path / 'purchased-directory'
    root.mkdir()
    content = [b'a' * (CHUNK_SIZE + 3), b'', b'xyz' * (CHUNK_SIZE * 2)]
    rows = []
    for i, raw in enumerate(content):
        name = f'{i}.csv'
        (root / name).write_bytes(raw)
        rows.append(dict(index=i, relative_path=name, size_bytes=len(raw),
                         sha256=hashlib.sha256(raw).hexdigest(), detected_type='csv',
                         role='data', is_sample=i == 0))
    manifest = build_manifest(rows)
    version = str(uuid.uuid4())
    with get_session_context() as db:
        db.add(PublishedManifest(listing_version_id=version, manifest_hash=manifest['manifest_hash'],
            dataset_id='deleted-registration', root_path=str(root), members=rows,
            registration_to_published_index={'2': 0, '8': 1, '19': 2}))
        db.commit()
    return SimpleNamespace(root=root, rows=rows, content=content, version=version,
                           hash=manifest['manifest_hash'])


class WirePeer:
    """Application wire stub uses the real client's response dispatch/correlation."""
    def __init__(self, fixture, verified=()):
        self.fixture = fixture
        self.client = TrustChannelClient()
        self.client.send_action = self.send
        self.messages = []
        self.verified = list(verified)
        self.transfer_id = 'server-open-session'
        self.origin = None
        self.metadata_count = 0
        self.complete_count = 0
        self.hook = None
        self.timeouts = []
        self.total = sum(max(1, (len(raw) + CHUNK_SIZE - 1) // CHUNK_SIZE) for raw in fixture.content)

    def reply(self, request, data, success=True):
        self.client._dispatch({'request_id': request['request_id'], 'success': success,
                               'data': data, 'error': None})

    async def send(self, message):
        self.messages.append(json.loads(json.dumps(message)))
        action = message['action'].rsplit('.', 1)[-1]
        if self.hook and await self.hook(message):
            return
        if action == 'metadata':
            self.metadata_count += 1
            self.origin = None
            self.reply(message, dict(success=True, transfer_id=self.transfer_id,
                                     verified_member_indices=self.verified))
        elif action == 'chunk':
            assert message['transfer_id'] == self.transfer_id
            index = message['chunk_index']
            if self.origin is None:
                self.origin = index
            if (index - self.origin) % 4 == 3 or index == self.total - 1:
                self.reply(message, dict(action='vai.fulfillment.ack', transfer_id=self.transfer_id,
                                         acked_through_index=index, status='continue'))
            else:
                self.reply(message, {'success': True})
        elif action == 'complete':
            self.complete_count += 1
            self.reply(message, {'success': True, 'token_id': 'placeholder-grant'})


async def deliver(peer):
    service = FulfillmentService(peer.client)
    service._save_log = MagicMock()
    service._update_log = MagicMock()
    await service._handle_deliver({'action': 'vai.fulfillment.deliver', 'request_id': 'deliver-id',
        'parameters': {'order_id': 'order-id', 'listing_id': 'listing-id',
                       'purchased_version_id': peer.fixture.version,
                       'manifest_hash': peer.fixture.hash}})
    return service


def actions(peer):
    return [m['action'].rsplit('.', 1)[-1] for m in peer.messages]


def chunks(peer):
    return [m for m in peer.messages if m['action'].endswith('.chunk')]


@pytest.mark.asyncio
async def test_three_member_wire_golden_including_empty_sample_and_global_offsets(retained):
    peer = WirePeer(retained)
    service = await deliver(peer)
    assert actions(peer) == ['metadata'] + ['chunk'] * 9 + ['complete']
    meta = peer.messages[0]
    assert set(meta) == {'action', 'request_id', 'transfer_id', 'order_id', 'listing_id', 'manifest_hash', 'parameters'}
    assert meta['manifest_hash'] == retained.hash
    assert meta['parameters'] == dict(filename='purchased-directory', content_type='application/octet-stream',
        total_bytes=CHUNK_SIZE * 7 + 3, total_chunks=9, chunk_size=65536,
        sha256_hash=retained.hash, hash_algorithm='sha256')
    expected = [(0, 0, 0, CHUNK_SIZE), (1, 0, CHUNK_SIZE, 3), (2, 1, CHUNK_SIZE + 3, 0)]
    expected += [(3+i, 2, CHUNK_SIZE+3+i*CHUNK_SIZE, CHUNK_SIZE) for i in range(6)]
    assert [(m['chunk_index'], m['member_index'], m['byte_offset'], m['payload_length']) for m in chunks(peer)] == expected
    for index, raw in enumerate(retained.content):
        emitted = b''.join(base64.b64decode(m['payload']) for m in chunks(peer) if m['member_index'] == index)
        assert emitted == raw
    assert all(m['chunk_sha256'] == hashlib.sha256(base64.b64decode(m['payload'])).hexdigest() for m in chunks(peer))
    assert peer.messages[-1]['parameters'] == dict(status='fulfilled', file_size_bytes=CHUNK_SIZE*7+3,
                                                   chunk_count=9, sha256_hash=retained.hash)
    assert service._update_log.call_args.args[1] == 'completed'
    assert peer.client._fulfillment_inbox is None


@pytest.mark.asyncio
@pytest.mark.parametrize('verified,expected', [([0], list(range(2, 9))), ([0, 1], list(range(3, 9))),
    ([1], [0, 1, 3, 4, 5, 6, 7, 8]), ([2], [0, 1, 2]), ([0, 1, 2], [])])
async def test_resume_sparse_unaligned_and_fully_verified(retained, verified, expected):
    peer = WirePeer(retained, verified)
    service = await deliver(peer)
    assert [m['chunk_index'] for m in chunks(peer)] == expected
    assert service._update_log.call_args.args[1] == 'completed'
    assert peer.messages[-1]['parameters']['chunk_count'] == 9


@pytest.mark.asyncio
@pytest.mark.parametrize('refusal', ['listener_busy', 'out_of_window', 'member_reset', 'finalizing'])
@pytest.mark.parametrize('stage', ['metadata', 'chunk', 'complete'])
async def test_four_refusals_replay_and_resume(retained, refusal, stage):
    peer = WirePeer(retained)
    injected = False
    async def hook(message):
        nonlocal injected
        if message['action'].endswith('.' + stage) and not injected:
            injected = True
            peer.reply(message, {'success': False, 'error': refusal}, success=False)
            return True
        return False
    peer.hook = hook
    service = await deliver(peer)
    sequence = actions(peer)
    assert injected
    if stage == 'complete' and refusal == 'finalizing':
        assert sequence[-2:] == ['complete', 'complete']
    else:
        position = sequence.index(stage)
        assert sequence[position + 1] == 'metadata'
    assert 'error' not in sequence
    assert service._update_log.call_args.args[1] == 'completed'


@pytest.mark.asyncio
async def test_member_reset_nonboundary_skips_newly_verified_member(retained):
    peer = WirePeer(retained)
    injected = False
    async def hook(message):
        nonlocal injected
        if message['action'].endswith('.chunk') and message['chunk_index'] == 4 and not injected:
            injected = True
            peer.verified = [0, 1]
            peer.reply(message, {'success': False, 'error': 'member_reset'})
            return True
        return False
    peer.hook = hook
    service = await deliver(peer)
    assert [m['chunk_index'] for m in chunks(peer)] == [0, 1, 2, 3, 4, 3, 4, 5, 6, 7, 8]
    assert service._update_log.call_args.args[1] == 'completed'


@pytest.mark.asyncio
async def test_metadata_grant_short_circuits_all_bytes(retained):
    peer = WirePeer(retained)
    async def hook(message):
        peer.reply(message, {'success': True, 'token_id': 'existing-grant'})
        return True
    peer.hook = hook
    service = await deliver(peer)
    assert actions(peer) == ['metadata']
    assert service._update_log.call_args.args[1] == 'completed'


@pytest.mark.asyncio
async def test_missing_manifest_is_permanent_and_names_purchased_version(retained):
    peer = WirePeer(retained)
    peer.fixture.version = 'missing-purchased-version'
    service = await deliver(peer)
    assert actions(peer) == ['error']
    assert peer.messages[0]['parameters']['error_code'] == 'MANIFEST_NOT_FOUND'
    assert 'missing-purchased-version' in peer.messages[0]['parameters']['error_message']
    assert service._update_log.call_args.kwargs['error_code'] == 'MANIFEST_NOT_FOUND'


@pytest.mark.asyncio
@pytest.mark.parametrize('bad_indices', [[True], [99], [1, 1], None, '0'])
async def test_invalid_resume_authority_fails_closed(retained, bad_indices):
    peer = WirePeer(retained)
    async def hook(message):
        if message['action'].endswith('.metadata'):
            peer.reply(message, {'success': True, 'transfer_id': 'server-id', 'verified_member_indices': bad_indices})
            return True
        return False
    peer.hook = hook
    await deliver(peer)
    assert actions(peer) == ['metadata', 'error']


@pytest.mark.asyncio
async def test_changed_same_size_source_never_completes(retained):
    (retained.root / '0.csv').write_bytes(b'b' * len(retained.content[0]))
    peer = WirePeer(retained)
    await deliver(peer)
    assert 'complete' not in actions(peer)
    assert actions(peer)[-1] == 'error'


@pytest.mark.asyncio
async def test_disconnect_replays_metadata_and_adopts_session(retained):
    peer = WirePeer(retained)
    injected = False
    async def hook(message):
        nonlocal injected
        if message['action'].endswith('.chunk') and not injected:
            injected = True
            peer.transfer_id = 'continued-session'
            raise ConnectionError('disconnected')
        return False
    peer.hook = hook
    service = await deliver(peer)
    assert actions(peer)[:4] == ['metadata', 'chunk', 'metadata', 'chunk']
    assert service._update_log.call_args.args[1] == 'completed'


@pytest.mark.asyncio
async def test_budget_bounds_finalizing_and_emits_resumable_error(retained, monkeypatch):
    monkeypatch.setattr(settings, 'transfer_complete_retry_budget_s', .005)
    peer = WirePeer(retained)
    async def hook(message):
        if message['action'].endswith('.complete'):
            peer.reply(message, {'success': False, 'error': 'finalizing'})
            return True
        return False
    peer.hook = hook
    await asyncio.wait_for(deliver(peer), 1)
    assert actions(peer).count('complete') >= 2
    assert actions(peer)[-1] == 'error'
    assert peer.messages[-1]['parameters']['error_code'] == 'TRANSFER_ABORTED'


@pytest.mark.asyncio
async def test_metadata_timeout_retries_without_chunks(retained, monkeypatch):
    monkeypatch.setattr(settings, 'transfer_metadata_wait_s', .005)
    peer = WirePeer(retained)
    dropped = False
    async def hook(message):
        nonlocal dropped
        if message['action'].endswith('.metadata') and not dropped:
            dropped = True
            return True
        return False
    peer.hook = hook
    service = await deliver(peer)
    assert actions(peer)[:3] == ['metadata', 'metadata', 'chunk']
    assert service._update_log.call_args.args[1] == 'completed'


@pytest.mark.asyncio
async def test_unknown_refusal_retains_safe_session_failure(retained):
    peer = WirePeer(retained)
    async def hook(message):
        if message['action'].endswith('.chunk'):
            peer.reply(message, {'success': False, 'error': 'store_timeout'})
            return True
        return False
    peer.hook = hook
    await deliver(peer)
    assert actions(peer) == ['metadata', 'chunk', 'error']


@pytest.mark.asyncio
async def test_inbox_correlates_outer_failures_and_ignores_unrelated():
    client = TrustChannelClient()
    client.send_action = AsyncMock()
    with client.fulfillment_responses('transfer') as inbox:
        await inbox.send({'action': 'vai.fulfillment.chunk', 'request_id': 'chunk-id'})
        client._dispatch({'request_id': 'other-id', 'success': False, 'error': 'member_reset'})
        assert inbox.queue.empty()
        response = {'request_id': 'chunk-id', 'success': False, 'error': 'member_reset'}
        client._dispatch(response)
        assert await inbox.receive(.1) == response
        assert not inbox.pending
    assert client._fulfillment_inbox is None


def test_settings_defaults_and_release_metadata(monkeypatch):
    for name in ['AIM_DATA_VERSION', 'VECTORAIZ_VERSION', 'APP_VERSION', 'AIM_DATA_APP_VERSION', 'VECTORAIZ_APP_VERSION']:
        monkeypatch.delenv(name, raising=False)
    config = Settings(_env_file=None)
    assert config.app_version == '1.25.0'
    assert config.transfer_metadata_wait_s == 30
    assert config.transfer_complete_retry_budget_s == 1800
    assert config.transfer_retry_after_s == 20
    assert WINDOW_SIZE == 4 and ACK_TIMEOUT_S == 30 and CHUNK_SIZE == 65536
    assert 'ARG VERSION=1.25.0' in Path('Dockerfile.customer').read_text()
    assert 'payload["agent_version"] = settings.app_version' in Path('app/routers/marketplace_publish.py').read_text()

async def legacy_messages(service_class, tmp_path, monkeypatch, enabled, extra_fields=False):
    from tests.fixtures.backend_acks_e97d0de4 import complete_ack
    from app.models.dataset import DatasetRecord
    path = tmp_path / 'data.csv'
    path.write_bytes(b'a' * (4 * CHUNK_SIZE + 3))
    dataset = DatasetRecord(id='legacy', original_filename='data.csv', storage_filename='data.csv',
                            file_type='csv', file_size_bytes=path.stat().st_size, listing_id='listing-id')
    monkeypatch.setattr(settings, 'multi_file_datasets_enabled', enabled)
    # Patch the actual function globals, also usable to record the base fixture.
    globals_ = service_class._handle_deliver.__globals__
    monkeypatch.setitem(globals_, 'uuid', SimpleNamespace(uuid4=lambda: '00000000-0000-4000-8000-000000000123'))
    monkeypatch.setitem(globals_, 'resolve_source_artifact', lambda *a, **kw:
                        SimpleNamespace(kind='local', dataset=dataset, local_path=str(path)))
    client = MagicMock(spec=TrustChannelClient)
    client.send_action = AsyncMock()
    async def wait(action, correlation, timeout, *, message=None):
        if message:
            await client.send_action(message)
            return complete_ack(correlation)
        return {'action': 'vai.fulfillment.ack', 'transfer_id': correlation,
                'status': 'continue', 'acked_through_index': 3}
    client.wait_for_action = AsyncMock(side_effect=wait)
    service = service_class(client)
    service._save_log = MagicMock()
    service._update_log = MagicMock()
    params = dict(order_id='order-id', listing_id='listing-id')
    if extra_fields:
        params.update(manifest_hash='ignored-while-flag-off', purchased_version_id='version')
    await service._handle_deliver({'request_id': 'legacy-request', 'parameters': params})
    assert service._update_log.call_args.args[1] == 'completed'
    return [call.args[0] for call in client.send_action.call_args_list]


@pytest.mark.asyncio
@pytest.mark.parametrize('enabled,extra_fields', [(False, False), (True, False), (False, True)])
async def test_shipped_single_file_message_golden(tmp_path, monkeypatch, enabled, extra_fields):
    messages = await legacy_messages(FulfillmentService, tmp_path, monkeypatch, enabled, extra_fields)
    golden = json.loads(Path('tests/fixtures/s1717_d_legacy_messages.json').read_text())
    fingerprints = [hashlib.sha256(json.dumps(m, sort_keys=True, separators=(',', ':')).encode()).hexdigest()
                    for m in messages]
    assert fingerprints == golden['message_sha256']
    assert [m['action'] for m in messages] == golden['actions']

@pytest.mark.asyncio
@pytest.mark.parametrize('recover', [True, False])
async def test_chunk_window_timeout_resends_exact_window_once(retained, monkeypatch, recover):
    import app.services.manifest_fulfillment as module
    monkeypatch.setattr(module, 'ACK_TIMEOUT_S', .02)
    peer = WirePeer(retained)
    count = 0
    async def hook(message):
        nonlocal count
        if message['action'].endswith('.chunk'):
            count += 1
            return count <= 4 or not recover
        return False
    peer.hook = hook
    service = await deliver(peer)
    assert [m['chunk_index'] for m in chunks(peer)][:8] == [0, 1, 2, 3] * 2
    assert chunks(peer)[:4] == chunks(peer)[4:8]
    assert actions(peer)[-1] == ('complete' if recover else 'error')
    assert service._update_log.call_args.args[1] == ('completed' if recover else 'failed')


@pytest.mark.asyncio
async def test_delayed_nonboundary_refusal_is_consumed_without_ack_wait(retained):
    peer = WirePeer(retained)
    injected = False
    async def hook(message):
        nonlocal injected
        if message['action'].endswith('.chunk') and not injected:
            injected = True
            asyncio.get_running_loop().call_later(.005, lambda: peer.reply(message,
                {'success': False, 'error': 'member_reset'}))
            return True
        if injected and peer.metadata_count == 1 and message['action'].endswith('.chunk'):
            return True  # Withhold the ACK; refusal alone must restart metadata.
        return False
    peer.hook = hook
    service = await asyncio.wait_for(deliver(peer), 1)
    assert peer.metadata_count == 2
    assert service._update_log.call_args.args[1] == 'completed'


@pytest.mark.asyncio
async def test_complete_timeout_retries_complete(retained, monkeypatch):
    import app.services.manifest_fulfillment as module
    monkeypatch.setattr(module, 'ACK_TIMEOUT_S', .02)
    peer = WirePeer(retained)
    dropped = False
    async def hook(message):
        nonlocal dropped
        if message['action'].endswith('.complete') and not dropped:
            dropped = True
            return True
        return False
    peer.hook = hook
    service = await deliver(peer)
    assert actions(peer)[-2:] == ['complete', 'complete']
    assert peer.metadata_count == 1
    assert service._update_log.call_args.args[1] == 'completed'


@pytest.mark.asyncio
@pytest.mark.parametrize('mode', ['timeout', 'listener_busy'])
async def test_metadata_retry_budget_exhaustion(retained, monkeypatch, mode):
    monkeypatch.setattr(settings, 'transfer_metadata_wait_s', .005)
    monkeypatch.setattr(settings, 'transfer_complete_retry_budget_s', .02)
    peer = WirePeer(retained)
    async def hook(message):
        if message['action'].endswith('.metadata'):
            if mode == 'listener_busy':
                peer.reply(message, {'success': False, 'error': mode})
            return True
        return False
    peer.hook = hook
    await asyncio.wait_for(deliver(peer), 1)
    assert len(peer.messages) > 2
    assert not chunks(peer)
    assert actions(peer)[-1] == 'error'


@pytest.mark.asyncio
async def test_invalid_ack_is_session_failure_not_reconnect_loop(retained):
    peer = WirePeer(retained)
    async def hook(message):
        if message['action'].endswith('.chunk') and message['chunk_index'] == 3:
            peer.reply(message, {'action': 'vai.fulfillment.ack', 'transfer_id': peer.transfer_id,
                                 'status': 'stop', 'acked_through_index': 3})
            return True
        return False
    peer.hook = hook
    await asyncio.wait_for(deliver(peer), 1)
    assert peer.metadata_count == 1
    assert actions(peer)[-1] == 'error'


@pytest.mark.asyncio
async def test_unmodified_sender_safe_fallback_for_new_values():
    client = TrustChannelClient()
    client.send_action = AsyncMock()
    for code in ['listener_busy', 'out_of_window', 'member_reset', 'finalizing']:
        waiter = asyncio.create_task(client.wait_for_action('', code, message={
            'action': 'vai.fulfillment.complete', 'request_id': code}))
        await asyncio.sleep(0)
        client._dispatch({'request_id': code, 'success': False, 'error': code})
        with pytest.raises(ConnectionError):
            await waiter
        with pytest.raises(ConnectionError):
            FulfillmentService(client)._confirmed_result({'success': True, 'data': {
                'success': False, 'error': code}})


@pytest.mark.asyncio
async def test_manifest_fields_in_unchanged_application_envelope():
    client = TrustChannelClient()
    client._ws = SimpleNamespace(send=AsyncMock())
    client._session = SimpleNamespace(outbound=0, encrypt=lambda value: value)
    metadata = dict(action='vai.fulfillment.metadata', request_id='meta-id', transfer_id='transfer-id',
                    order_id='order-id', listing_id='listing-id', manifest_hash='a'*64,
                    parameters={'filename': 'directory', 'chunk_size': 65536})
    await client.send_action(metadata)
    payload = json.loads(client._ws.send.call_args.args[0])
    assert payload['parameters'] == {k: v for k, v in metadata.items() if k not in ('action', 'request_id')}
    assert payload['parameters']['manifest_hash'] == 'a'*64
    assert payload['parameters']['parameters']['chunk_size'] == 65536
    chunk = dict(action='vai.fulfillment.chunk', request_id='chunk-id', transfer_id='transfer-id',
                 order_id='order-id', listing_id='listing-id', member_index=2, chunk_index=3,
                 byte_offset=7, payload_length=0, payload='', chunk_sha256=hashlib.sha256(b'').hexdigest())
    await client.send_action(chunk)
    assert json.loads(client._ws.send.call_args.args[0])['parameters']['member_index'] == 2


def test_source_symlink_replacement_is_not_followed(retained):
    from app.services.manifest_fulfillment import open_member
    _, plan, _, _ = load_plan(retained.version, retained.hash)
    target = retained.root / '0.csv'
    target.unlink()
    target.symlink_to(retained.root / '2.csv')
    with pytest.raises(OSError):
        open_member(plan[0].path)


def test_source_resolution_keeps_purchased_root_and_ignores_live_registration(retained):
    name, plan, total_chunks, total_bytes = load_plan(retained.version, retained.hash)
    assert [p.index for p in plan] == [0, 1, 2]
    assert all(p.path.parent == retained.root.resolve() for p in plan)
    assert name == 'purchased-directory' and total_chunks == 9
    assert total_bytes == sum(map(len, retained.content))


def test_new_settings_environment_aliases(monkeypatch):
    monkeypatch.setenv('TRANSFER_METADATA_WAIT_S', '17')
    monkeypatch.setenv('AIM_DATA_TRANSFER_COMPLETE_RETRY_BUDGET_S', '123')
    monkeypatch.setenv('VECTORAIZ_TRANSFER_RETRY_AFTER_S', '4')
    config = Settings(_env_file=None)
    assert config.transfer_metadata_wait_s == 17
    assert config.transfer_complete_retry_budget_s == 123
    assert config.transfer_retry_after_s == 4

@pytest.mark.asyncio
@pytest.mark.parametrize('sizes', [
    [128 * CHUNK_SIZE], [384 * CHUNK_SIZE], [5 * CHUNK_SIZE, 130 * CHUNK_SIZE], [1] * 20,
])
async def test_ac_d2_plan_shapes(retained, sizes):
    content = [b'x' * size for size in sizes]
    rows = []
    for i, raw in enumerate(content):
        (retained.root / f'{i}.csv').write_bytes(raw)
        rows.append(dict(index=i, relative_path=f'{i}.csv', size_bytes=len(raw),
                         sha256=hashlib.sha256(raw).hexdigest(), detected_type='csv', role='data', is_sample=False))
    manifest = build_manifest(rows)
    retained.version = str(uuid.uuid4())
    retained.hash = manifest['manifest_hash']
    retained.content = content
    with get_session_context() as db:
        db.add(PublishedManifest(listing_version_id=retained.version, manifest_hash=retained.hash,
            dataset_id='deleted-registration', root_path=str(retained.root), members=rows,
            registration_to_published_index={str(2*i): i for i in range(len(rows))}))
        db.commit()
    peer = WirePeer(retained)
    service = await deliver(peer)
    assert [m['chunk_index'] for m in chunks(peer)] == list(range(peer.total))
    assert service._update_log.call_args.args[1] == 'completed'


@pytest.mark.asyncio
async def test_continuous_completion_repairs_share_one_retry_budget(retained, monkeypatch):
    monkeypatch.setattr(settings, 'transfer_complete_retry_budget_s', .03)
    peer = WirePeer(retained)
    async def hook(message):
        if message['action'].endswith('.complete'):
            peer.reply(message, {'success': False, 'error': 'member_reset'})
            return True
        return False
    peer.hook = hook
    await asyncio.wait_for(deliver(peer), 1)
    assert actions(peer).count('complete') > 1
    assert actions(peer)[-1] == 'error'


def test_agent_version_is_in_actual_local_publish_payload(monkeypatch):
    from app.routers.marketplace_publish import _build_publish_payload, VersionPublishEmit
    monkeypatch.setattr(settings, 'multi_file_datasets_enabled', True)
    monkeypatch.setattr(settings, 'app_version', '1.25.0')
    body = SimpleNamespace(model_dump=lambda **kwargs: {'vz_dataset_id': 'dataset-id'})
    version = VersionPublishEmit(version_label='frozen-version', object_count=1, total_size_bytes=3,
        manifest_hash='a'*64, source_kind='aim_data_local', members_total=1, sample_members_total=0,
        members_upload_id=uuid.uuid4())
    payload = _build_publish_payload(body, None, versions=[version])
    assert payload['agent_version'] == '1.25.0'
    assert 'agent_version' not in payload['versions'][0]

@pytest.mark.asyncio
async def test_verified_missing_local_member_is_not_read_on_resume(retained):
    (retained.root / '0.csv').unlink()
    peer = WirePeer(retained, [0])
    service = await deliver(peer)
    assert [m['chunk_index'] for m in chunks(peer)] == list(range(2, 9))
    assert service._update_log.call_args.args[1] == 'completed'


@pytest.mark.asyncio
async def test_repeated_reset_without_progress_is_bounded(retained, monkeypatch):
    monkeypatch.setattr(settings, 'transfer_complete_retry_budget_s', .02)
    peer = WirePeer(retained)
    async def hook(message):
        if message['action'].endswith('.chunk'):
            peer.reply(message, {'success': False, 'error': 'member_reset'})
            return True
        return False
    peer.hook = hook
    await asyncio.wait_for(deliver(peer), 1)
    assert peer.metadata_count > 1
    assert actions(peer)[-1] == 'error'

@pytest.mark.asyncio
async def test_documentation_excluded_without_renumbering_published_indices(retained):
    rows = [dict(retained.rows[0]), dict(index=1, relative_path='README.md', size_bytes=9,
        sha256=hashlib.sha256(b'document!').hexdigest(), detected_type='txt',
        role='documentation', is_sample=False), dict(retained.rows[1], index=2), dict(retained.rows[2], index=3)]
    # Documentation is deliberately absent locally; it is not purchased data.
    manifest = build_manifest(rows)
    retained.version = str(uuid.uuid4())
    retained.hash = manifest['manifest_hash']
    with get_session_context() as db:
        db.add(PublishedManifest(listing_version_id=retained.version, manifest_hash=retained.hash,
            dataset_id='deleted-registration', root_path=str(retained.root), members=rows,
            registration_to_published_index={'2': 0, '3': 1, '8': 2, '19': 3}))
        db.commit()
    peer = WirePeer(retained)
    service = await deliver(peer)
    assert {m['member_index'] for m in chunks(peer)} == {0, 2, 3}
    assert service._update_log.call_args.args[1] == 'completed'


@pytest.mark.asyncio
@pytest.mark.parametrize('refusal,delay_count', [('member_reset', 0), ('out_of_window', 0),
                                              ('listener_busy', 1), ('finalizing', 1)])
async def test_refusal_retry_pacing(retained, monkeypatch, refusal, delay_count):
    peer = WirePeer(retained)
    observed = []
    original = ManifestSender.pause
    async def pause(self):
        observed.append(settings.transfer_retry_after_s)
        await original(self)
    monkeypatch.setattr(ManifestSender, 'pause', pause)
    injected = False
    async def hook(message):
        nonlocal injected
        if message['action'].endswith('.chunk') and not injected:
            injected = True
            peer.reply(message, {'success': False, 'error': refusal})
            return True
        return False
    peer.hook = hook
    await deliver(peer)
    assert observed == [settings.transfer_retry_after_s] * delay_count
