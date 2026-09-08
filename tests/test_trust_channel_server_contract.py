"""Wire contracts, not proof of backend execution or deployed delivery."""
import asyncio
import ast
import base64
import json
import subprocess
from datetime import datetime
from pathlib import Path
from types import SimpleNamespace
from typing import Annotated, Literal
from unittest.mock import AsyncMock, patch

import pytest
from cryptography.hazmat.primitives.ciphers.aead import AESGCM
from pydantic import BaseModel, Field, HttpUrl, UrlConstraints

from app.services.fulfillment_service import FulfillmentService
from app.services.trust_channel_client import TrustChannelClient
from app.services.trust_channel_protocol import TrafficSession
from tests.fixtures import backend_schemas_58a04603 as backend
from tests.fixtures.backend_complete_ack_58a04603 import complete_ack

BACKEND = '/Users/max/Projects/ai-market/ai-market-backend'
PIN = '58a04603'


class ResponseParams(BaseModel):
    success: bool
    access_url: Annotated[HttpUrl, UrlConstraints(allowed_schemes=['https'])]
    access_token: str | None = None
    expires_at: datetime
    file_hash: str | None = Field(default=None, pattern=r'^[0-9a-fA-F]{64}$')
    file_size_bytes: int | None = None
    error: str | None = None


class ResponseMessage(BaseModel):
    action: Literal['vai.fulfillment.response']
    order_id: str
    listing_id: str
    request_id: str
    parameters: ResponseParams


@pytest.fixture
def wire_client():
    client = TrustChannelClient()
    key, nonce = bytes(range(32)), bytes(range(12))
    client._session = TrafficSession(key, key, nonce)
    frames = []

    async def send(raw):
        frame = json.loads(raw)
        assert frame['sequence'] == len(frames)
        # Independently decode c2s IV and AES-GCM, without client receive helpers.
        tail = bytearray(a ^ b for a, b in zip(nonce[4:], frame['sequence'].to_bytes(8, 'big')))
        tail[0] &= 127
        data = AESGCM(key).decrypt(nonce[:4] + tail,
            base64.b64decode(frame['ciphertext']) + base64.b64decode(frame['auth_tag']), None)
        frames.append(json.loads(data))
    client._ws = SimpleNamespace(send=send, close=AsyncMock())
    return client, frames


def server_payload(frame):
    # Exact spread at trust_websocket.py:1241,1249-1253 (58a04603).
    return {'action': frame['action'], 'message_id': frame['request_id'], **frame['parameters']}


@pytest.mark.asyncio
async def test_local_emitted_frames_validate_pinned_schemas(wire_client, tmp_path):
    client, frames = wire_client
    service = FulfillmentService(client)
    service._save_log = lambda log: None
    service._update_log = lambda *args, **kwargs: None
    path = tmp_path / 'data.csv'
    path.write_bytes(b'a,b\n1,2\n')
    artifact = SimpleNamespace(kind='local', dataset=SimpleNamespace(), local_path=str(path))
    with patch('app.services.fulfillment_service.resolve_source_artifact', return_value=artifact):
        await service._handle_deliver({'request_id': 'delivery-request', 'parameters': {
            'order_id': 'order', 'listing_id': 'listing'}})
    await service._send_error(frames[0]['transfer_id'], 'order', 'TRANSFER_ABORTED', 'synthetic error')
    models = {
        'vai.fulfillment.metadata': backend.FulfillmentMetadataMessage,
        'vai.fulfillment.chunk': backend.FulfillmentChunkMessage,
        'vai.fulfillment.complete': backend.FulfillmentCompleteMessage,
        'vai.fulfillment.error': backend.FulfillmentErrorMessage,
    }
    assert [f['action'] for f in frames] == list(models)
    metadata = backend.FulfillmentMetadataMessage.model_validate(server_payload(frames[0]))
    for frame in frames:
        payload = server_payload(frame)
        models[frame['action']].model_validate(payload)
        assert payload['transfer_id'] == metadata.transfer_id
        if frame['action'] == 'vai.fulfillment.chunk':
            assert (payload['order_id'], payload['listing_id']) == (metadata.order_id, metadata.listing_id)
    # Complete's exact encrypted response envelope also resolves a real waiter.
    task = asyncio.create_task(client.wait_for_action('', frames[2]['request_id']))
    await asyncio.sleep(0)
    client._dispatch(complete_ack(frames[2]['request_id']))
    assert await task == complete_ack(frames[2]['request_id'])


@pytest.mark.asyncio
@pytest.mark.parametrize('rate_limits, expected', [(0, 'completed'), (1, 'completed'), (2, 'failed')])
async def test_s3_wire_schema_complete_ack_and_bounded_retry(wire_client, rate_limits, expected):
    client, frames = wire_client
    service = FulfillmentService(client)
    statuses, times = [], []
    service._save_log = lambda log: None
    service._update_log = lambda log, status, **kw: statuses.append(status)
    original_send = client._ws.send

    async def send(raw):
        await original_send(raw)
        times.append(asyncio.get_running_loop().time())
        if len(frames) <= rate_limits:
            client._handle_rate_limit({'type': 'rate_limit', 'retry_after_ms': 30})
        else:
            client._dispatch(complete_ack(frames[-1]['request_id']))
    client._ws.send = send
    with patch('app.services.fulfillment_service.S3BrokerClient') as broker:
        broker.return_value.presign_object.return_value = {'url': 'https://example.org/data', 'expires_in': 300}
        await service._deliver_s3_object(
            SimpleNamespace(request_id='s3-request'), SimpleNamespace(metadata_json='{"sha256_hash":"' + 'ab'*32 + '"}'),
            SimpleNamespace(status='verified', role_arn='synthetic', region='us-east-1', bucket='synthetic'),
            SimpleNamespace(size_bytes=10, object_key='data'), 'transfer', 'order', 'listing')
    assert statuses == [expected]
    assert len(frames) == (1 if not rate_limits else 2)
    for frame in frames:
        # Validate the actual application payload after the server spread.
        message = server_payload(frame)
        parsed = ResponseMessage.model_validate(message)
        assert (parsed.order_id, parsed.listing_id, parsed.request_id) == ('order', 'listing', 's3-request')
    if rate_limits:
        assert times[1] - times[0] >= .025
        assert frames[0] == frames[1]
    assert not client._waiters and not client._response_waiters


def test_pinned_whitelist_and_known_registry_gap():
    if not Path(BACKEND).exists():
        pytest.skip("Optional live pin cross-check requires the backend object repository")
    # Read commit objects, never the moving backend worktree; no companion dependency.
    def source(path):
        return subprocess.check_output(['rtk', 'proxy', 'git', '-C', BACKEND, 'show', f'{PIN}:{path}'], text=True)
    tree = ast.parse(source('app/api/v1/endpoints/trust_websocket.py'))
    allowed = next(ast.literal_eval(node.value) for node in tree.body
                   if isinstance(node, ast.AnnAssign) and getattr(node.target, 'id', '') == 'TRUST_CHANNEL_ALLOWED_ACTIONS')
    assert 'vai.fulfillment.response' in allowed
    definitions = ast.parse(source('app/actions/definitions.py'))
    names = [kw.value.value for node in ast.walk(definitions) if isinstance(node, ast.Call)
             for kw in node.keywords if kw.arg == 'name' and isinstance(kw.value, ast.Constant)]
    assert 'vai.fulfillment.response' not in names  # Explicit known limitation, not a positive dispatch claim.
    assert Path(backend.__file__).read_text() == source('app/schemas/fulfillment.py')


@pytest.mark.asyncio
@pytest.mark.parametrize('value', [None, -1, '30', True, float('inf'), float('nan')])
async def test_malformed_rate_limit_is_ignored(wire_client, value):
    client, _ = wire_client
    client._handle_rate_limit({'type': 'rate_limit', 'retry_after_ms': value})
    client._handle_rate_limit({'type': 'rate_limit'})
    assert client._send_resume_at == 0
    assert client._session is not None
