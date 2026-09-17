"""S1717 D5 local sender. The retained version is the only membership authority.

Application messages only: the Trust Channel envelope and legacy sender are
unchanged. Memory is O(members + four chunks), never O(total chunks/bytes).
"""
from __future__ import annotations

import asyncio
import base64
from dataclasses import dataclass
import hashlib
import os
import stat
from pathlib import Path
import unicodedata

from app.config import settings
from app.core.database import get_session_context
from app.models.published_manifest import PublishedManifest
from app.services.dataset_manifest import build_manifest
from app.services.fulfillment_service import ACK_TIMEOUT_S, CHUNK_SIZE, WINDOW_SIZE


class Refusal(Exception):
    def __init__(self, code):
        self.code = code if isinstance(code, str) else "delivery_rejected"
        super().__init__(str(code))


class MissingManifest(Exception):
    pass


@dataclass(frozen=True)
class Member:
    index: int
    path: Path
    size: int
    sha256: str
    start_chunk: int
    start_byte: int
    chunks: int
    root: Path


def load_plan(version_id, manifest_hash):
    with get_session_context() as session:
        retained = session.get(PublishedManifest, (version_id, manifest_hash))
        if retained is None:
            raise MissingManifest()
        manifest = build_manifest(retained.members)
        if manifest['manifest_hash'] != manifest_hash:
            raise ValueError('Retained manifest hash mismatch')
        rows = manifest['members']
        if len(rows) > settings.dataset_max_members:
            raise ValueError(f'DATASET_MAX_MEMBERS: {settings.dataset_max_members}')
        if [m['index'] for m in rows] != list(range(len(rows))):
            raise ValueError('Retained published indices are not dense')
        # C retained the dense rows themselves. The inverse mapping binds each
        # published row to its registration identity; never consult live rows.
        mapping = retained.registration_to_published_index
        if (len(mapping) != len(rows) or set(mapping.values()) != set(range(len(rows)))
                or any(type(v) is not int for v in mapping.values())):
            raise ValueError('Retained registration mapping is invalid')
        source_indices = {published: registration for registration, published in mapping.items()}
        sources = {source_indices[row["index"]]: row for row in rows}
        root = Path(retained.root_path)
    if not root.is_absolute():
        raise ValueError('Retained root must be absolute')
    plan = []
    chunk_offset = byte_offset = 0
    for row in rows:
        if row['role'] != 'data':
            continue
        # Samples remain paid data members. Documentation/other are excluded.
        source = sources[source_indices[row['index']]]
        current = root / source['relative_path']
        size = row['size_bytes']
        if size > settings.transfer_max_member_bytes:
            raise ValueError(f'TRANSFER_MAX_MEMBER_BYTES: {settings.transfer_max_member_bytes}')
        count = max(1, (size + CHUNK_SIZE - 1) // CHUNK_SIZE)
        plan.append(Member(row['index'], current, size, row['sha256'], chunk_offset, byte_offset, count, root))
        chunk_offset += count
        byte_offset += size
    if not plan or byte_offset > settings.dataset_max_bytes:
        raise ValueError(f'DATASET_MAX_BYTES: {settings.dataset_max_bytes}; empty or oversized data set')
    return root.name, plan, chunk_offset, byte_offset


def result_of(message):
    """Preserve both response levels, including actionless failed responses."""
    if not isinstance(message, dict):
        raise Refusal('malformed_response')
    data = message.get('data', message)
    if not isinstance(data, dict):
        raise Refusal(message.get('error') or 'malformed_response')
    error = data.get('error') or message.get('error')
    if error or message.get('success') is False or data.get('success') is False:
        raise Refusal(error or 'delivery_rejected')
    if 'data' in message and message.get('success') is not True:
        raise Refusal('malformed_response')
    return data


def source_path(member, directories):
    """Resolve NFC names only for unverified members, without live DB rows."""
    current = member.root
    if current.is_symlink() or not current.is_dir():
        raise ValueError('Retained source root is unavailable')
    for segment in member.path.relative_to(member.root).parts:
        if current not in directories:
            names = {}
            for entry in current.iterdir():
                name = unicodedata.normalize('NFC', entry.name)
                if name in names:
                    raise ValueError('Ambiguous source path')
                names[name] = entry
            directories[current] = names
        current = directories[current].get(segment)
        if current is None or current.is_symlink():
            raise ValueError('Retained member is missing or symlinked')
    return current


def open_member(path):
    """Open the frozen absolute path without following any replaced symlink."""
    directory = os.open(path.anchor, os.O_RDONLY | os.O_DIRECTORY)
    try:
        for segment in path.parts[1:-1]:
            child = os.open(segment, os.O_RDONLY | os.O_DIRECTORY | os.O_NOFOLLOW, dir_fd=directory)
            os.close(directory)
            directory = child
        descriptor = os.open(path.name, os.O_RDONLY | os.O_NOFOLLOW | os.O_NONBLOCK, dir_fd=directory)
        if not stat.S_ISREG(os.fstat(descriptor).st_mode):
            os.close(descriptor)
            raise ValueError('Retained member is not a regular file')
        return os.fdopen(descriptor, 'rb')
    finally:
        os.close(directory)


class ManifestSender:
    def __init__(self, service, entry, params, plan_info):
        self.service, self.entry, self.params = service, entry, params
        self.name, self.plan, self.total_chunks, self.total_bytes = plan_info
        self.transfer_id = entry.transfer_id
        self.loop = asyncio.get_running_loop()
        self.retry_deadline = None
        self.completion_started = False
        self.sent = 0
        self.highwater = -1

    def remaining(self, timeout):
        if self.retry_deadline is None:
            return timeout
        remaining = self.retry_deadline - self.loop.time()
        if remaining <= 0:
            raise TimeoutError('TRANSFER_COMPLETE_RETRY_BUDGET_S exhausted')
        return min(timeout, remaining)

    def start_budget(self):
        if self.retry_deadline is None:
            self.retry_deadline = self.loop.time() + settings.transfer_complete_retry_budget_s

    async def pause(self):
        self.start_budget()
        await asyncio.sleep(self.remaining(settings.transfer_retry_after_s))
        self.remaining(ACK_TIMEOUT_S)

    def message(self, action, **fields):
        return dict(action=f'vai.fulfillment.{action}', transfer_id=self.transfer_id,
                    order_id=self.entry.order_id, **fields)

    async def exchange(self, inbox, message, timeout):
        inbox.clear()
        deadline = self.loop.time() + self.remaining(timeout)
        await asyncio.wait_for(inbox.send(message), max(0, deadline - self.loop.time()))
        return result_of(await inbox.receive(max(0, deadline - self.loop.time())))

    async def run(self, inbox):
        need_metadata = True
        verified = set()
        while True:
            stage = 'metadata' if need_metadata else 'complete'
            try:
                if need_metadata:
                    data = await self.exchange(inbox, self.message(
                        'metadata', listing_id=self.entry.listing_id,
                        manifest_hash=self.params['manifest_hash'], parameters={
                            'filename': self.name, 'content_type': 'application/octet-stream',
                            'total_bytes': self.total_bytes, 'total_chunks': self.total_chunks,
                            'chunk_size': CHUNK_SIZE, 'sha256_hash': self.params['manifest_hash'],
                            'hash_algorithm': 'sha256',
                        }), settings.transfer_metadata_wait_s)
                    if self.has_grant(data):
                        return
                    transfer_id = data.get('transfer_id')
                    indices = data.get('verified_member_indices')
                    allowed = {m.index for m in self.plan}
                    if (not isinstance(transfer_id, str) or not transfer_id
                            or not isinstance(indices, list)
                            or any(type(i) is not int or i not in allowed for i in indices)
                            or len(indices) != len(set(indices))):
                        raise Refusal('invalid_metadata_response')
                    self.transfer_id = inbox.transfer_id = transfer_id
                    self.entry.transfer_id = transfer_id
                    self.service._save_log(self.entry)
                    verified = set(indices)
                    stage = 'chunk'
                    await self.stream(inbox, verified)
                    need_metadata = False
                    if not self.completion_started:
                        self.retry_deadline = None
                    self.completion_started = True
                    self.start_budget()
                stage = 'complete'
                data = await self.exchange(inbox, self.message('complete', parameters={
                    'status': 'fulfilled', 'file_size_bytes': self.total_bytes,
                    'chunk_count': self.total_chunks, 'sha256_hash': self.params['manifest_hash'],
                }), ACK_TIMEOUT_S)
                if not self.has_grant(data):
                    raise Refusal('completion_not_confirmed')
                return
            except Refusal as exc:
                if exc.code not in {'listener_busy', 'out_of_window', 'member_reset', 'finalizing'}:
                    raise
                self.start_budget()
                if exc.code in {'listener_busy', 'finalizing'}:
                    await self.pause()
                need_metadata = not (stage == 'complete' and exc.code == 'finalizing')
            except TimeoutError:
                # Chunk windows have their own one-resend rule, then fail safely.
                if stage == 'chunk':
                    raise
                self.start_budget()
                self.remaining(ACK_TIMEOUT_S)
                if stage == 'complete':
                    await self.pause()
                # Metadata timeout already spent TRANSFER_METADATA_WAIT_S; replay now.
                need_metadata = stage == 'metadata'
            except ConnectionError:
                # The existing channel run loop owns reconnect/handshake. Never
                # stream after reconnect until metadata returns server authority.
                await self.pause()
                need_metadata = True

    @staticmethod
    def has_grant(data):
        return data.get('success') is True and isinstance(data.get('token_id'), str) and bool(data['token_id'])

    def chunks(self, verified):
        directories = {}
        for member in self.plan:
            if member.index in verified:
                continue
            digest = hashlib.sha256()
            with open_member(source_path(member, directories)) as stream:
                if os.fstat(stream.fileno()).st_size != member.size:
                    raise ValueError("Retained member size changed")
                for local_index in range(member.chunks):
                    size = min(CHUNK_SIZE, member.size - local_index * CHUNK_SIZE)
                    raw = stream.read(size)
                    if len(raw) != size:
                        raise ValueError('Retained member truncated during delivery')
                    digest.update(raw)
                    yield self.message('chunk', listing_id=self.entry.listing_id,
                        member_index=member.index, chunk_index=member.start_chunk + local_index,
                        byte_offset=member.start_byte + local_index * CHUNK_SIZE,
                        payload_length=len(raw), chunk_sha256=hashlib.sha256(raw).hexdigest(),
                        payload=base64.b64encode(raw).decode('ascii'))
                if stream.read(1) or digest.hexdigest() != member.sha256:
                    raise ValueError('Retained member bytes changed; completion withheld')

    async def stream(self, inbox, verified):
        window = []
        origin = None
        for chunk in self.chunks(verified):
            index = chunk['chunk_index']
            if origin is None:
                origin = index
            window.append(chunk)
            boundary = (index - origin) % WINDOW_SIZE == WINDOW_SIZE - 1 or index == self.total_chunks - 1
            if boundary or len(window) == WINDOW_SIZE:
                await self.window(inbox, window, boundary)
                window = []
        if window:
            # A verified suffix (or holes) can remove a cadence index. The
            # existing per-chunk results confirm acceptance, never durability.
            await self.window(inbox, window, False)

    async def window(self, inbox, messages, require_ack):
        expected = messages[-1]['chunk_index']
        for attempt in range(2):
            inbox.clear()
            deadline = self.loop.time() + self.remaining(ACK_TIMEOUT_S)
            pending = set()
            ack_needed = require_ack

            def consume(reply):
                nonlocal ack_needed
                data = result_of(reply)
                pending.discard(reply.get('request_id'))
                if data.get('action') == 'vai.fulfillment.ack':
                    if data.get('transfer_id') != self.transfer_id:
                        raise Refusal('wrong_ack_transfer')
                    try:
                        self.service._validate_ack(data, expected, self.transfer_id)
                    except ConnectionError as exc:
                        raise Refusal('invalid_ack') from exc
                    pending.clear()
                    ack_needed = False
                elif data.get('success') is not True:
                    raise Refusal('invalid_chunk_response')

            try:
                for message in messages:
                    await asyncio.wait_for(inbox.send(message), max(0, deadline - self.loop.time()))
                    pending.add(message['request_id'])
                    # Surface fast non-boundary refusals before sending more bytes.
                    await asyncio.sleep(0)
                    while not inbox.queue.empty():
                        consume(await inbox.receive(max(0, deadline - self.loop.time())))
                while pending or ack_needed:
                    consume(await inbox.receive(max(0, deadline - self.loop.time())))
                self.sent += len(messages)
                if expected > self.highwater:
                    self.highwater = expected
                    if not self.completion_started:
                        self.retry_deadline = None
                return
            except TimeoutError:
                if attempt:
                    raise TimeoutError('No ACK after manifest window retry')


async def deliver_manifest(service, entry, params):
    version = params.get('purchased_version_id')
    try:
        plan = await asyncio.to_thread(load_plan, version, params['manifest_hash'])
    except MissingManifest:
        message = f'No retained manifest for purchased_version_id={version}'
        await service._send_error(entry.transfer_id, entry.order_id, 'MANIFEST_NOT_FOUND', message)
        service._update_log(entry, 'failed', error_code='MANIFEST_NOT_FOUND', error_message=message)
        return
    sender = ManifestSender(service, entry, params, plan)
    entry.status = 'uploading'
    entry.file_size_bytes = sender.total_bytes
    service._save_log(entry)
    try:
        with service._client.fulfillment_responses(entry.transfer_id) as inbox:
            await sender.run(inbox)
    except (Refusal, TimeoutError, ConnectionError, OSError, ValueError) as exc:
        await service._send_error(sender.transfer_id, entry.order_id, 'TRANSFER_ABORTED',
                                  'Manifest transfer interrupted; order resumable')
        service._update_log(entry, 'failed', error_code='TRANSFER_ABORTED', error_message=type(exc).__name__)
        return
    service._update_log(entry, 'completed', chunks_sent=sender.sent)
