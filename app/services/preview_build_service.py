"""Owner-scoped local jobs. Only metadata persists; private indexes rebuild on restart."""
from contextlib import contextmanager
from datetime import datetime, timezone
import hashlib
import json
from pathlib import Path
import sqlite3
import struct
import threading
from uuid import uuid4

from app.services.dataset_canonicalization import CanonicalSchema, ParsingDeclaration
from app.services.dataset_merkle_service import (
    run_commitment_job, canonical_json_bytes, canonical_rfc3339_utc,
    CommitmentValidationError,
)
from app.services.preview_package_service import (
    CommitmentPreviewBuilder, PublicationStore, CAPS, MEDIA_TYPE,
    directory_fd, limit, sample_hash, PackageError,
)
from app.services.preview_content_policy import (
    POLICY, VERSION, PolicyError, check_text, walk_selection,
)
from app.services.preview_origin_service import validate_url, verify_hosted_package
from app.services.preview_lifecycle import capture_rights, PreviewJournal

AWAITING = 'Prepared locally; marketplace preview submission awaits backend support'
RIGHTS = {
    'owner': 'I own the rights to these complete selected records.',
    'licensed': 'My license permits public preview of these complete selected records.',
    'public_domain': 'These complete selected records are in the public domain.',
    'other_authorized': 'I am authorized to publish these complete selected records.',
}

class BuildError(ValueError):
    def __init__(self, code, status=409):
        self.code, self.status = code, status
        super().__init__(code)


def stamp():
    return canonical_rfc3339_utc(datetime.now(timezone.utc))


def owned_dataset(processing, dataset_id, owner):
    record = processing.get_dataset(dataset_id)
    if record is None:
        raise BuildError('dataset_not_found', 404)
    # Do not treat confirmation, shared batch membership or admin access as ownership.
    if record.metadata.get('preview_owner_id') != owner:
        raise BuildError('dataset_owner_unverified', 403)
    return record


def source_identity(record, upload_root):
    if record.metadata.get('source_type') in {'s3', 'database'}:
        raise BuildError('complete_source_manifest_required')
    path = Path(record.upload_path or '')
    root = Path(upload_root).resolve()
    if not path.is_absolute() or path.resolve() != path or not path.is_relative_to(root):
        raise BuildError('source_not_allowed', 422)
    try:
        info = path.stat()
        if not path.is_file():
            raise OSError
    except OSError:
        raise BuildError('source_unavailable') from None
    value = [record.id, info.st_dev, info.st_ino, info.st_size, str(info.st_mtime_ns), str(info.st_ctime_ns)]
    return path, hashlib.sha256(canonical_json_bytes(value)).hexdigest()


class PreviewBuildService:
    def __init__(self, root, processing, upload_root):
        self.root = Path(root).absolute()
        with directory_fd(self.root, private=True):
            pass
        self.processing, self.upload_root = processing, upload_root
        self.lock = threading.RLock()
        self.live = {}
        self.db_path = self.root / 'jobs.sqlite'
        if self.db_path.is_symlink():
            raise BuildError('unsafe_directory')
        with self.db() as db:
            db.execute('CREATE TABLE IF NOT EXISTS jobs (id TEXT PRIMARY KEY, owner TEXT NOT NULL, dataset TEXT NOT NULL, payload TEXT NOT NULL)')
        self.db_path.chmod(0o600)
        self.journal = PreviewJournal(self.root / 'candidates.sqlite')

    @contextmanager
    def db(self):
        db = sqlite3.connect(self.db_path)
        try:
            yield db
            db.commit()
        finally:
            db.close()

    def save(self, job):
        with self.db() as db:
            db.execute('INSERT OR REPLACE INTO jobs VALUES (?,?,?,?)',
                       (job['id'], job['owner'], job['dataset_id'], json.dumps(job)))

    def load(self, job_id, owner):
        with self.db() as db:
            row = db.execute('SELECT owner,payload FROM jobs WHERE id=?', (job_id,)).fetchone()
        if not row:
            raise BuildError('job_not_found', 404)
        if row[0] != owner:
            raise BuildError('job_owner_mismatch', 403)
        job = json.loads(row[1])
        owned_dataset(self.processing, job['dataset_id'], owner)
        return job

    def latest(self, dataset_id, owner):
        owned_dataset(self.processing, dataset_id, owner)
        with self.db() as db:
            row = db.execute('SELECT id FROM jobs WHERE dataset=? AND owner=? ORDER BY rowid DESC LIMIT 1', (dataset_id, owner)).fetchone()
        return self.status(row[0], owner) if row else None

    def create(self, body, owner):
        record = owned_dataset(self.processing, body.dataset_id, owner)
        path, version = source_identity(record, self.upload_root)
        declarations = record.metadata.get('commitment_declarations') or {}
        parsing = body.parsing.model_dump() if body.parsing else declarations.get('parsing')
        descriptors = body.schema_descriptors if body.schema_descriptors is not None else declarations.get('schema_descriptors')
        if not parsing or not descriptors:
            raise BuildError('parsing_declaration_required', 422)
        declaration = ParsingDeclaration(**parsing)
        declaration.validate()
        schema = CanonicalSchema(descriptors)
        expected = {'.csv': 'csv', '.tsv': 'tsv', '.json': 'json-array', '.ndjson': 'ndjson', '.jsonl': 'ndjson', '.parquet': 'parquet'}
        if expected.get(path.suffix.lower()) != declaration.format:
            raise BuildError('unsupported_format', 422)
        with self.lock:
            if self.live:
                raise BuildError('job_already_running')
            job = dict(id=str(uuid4()), owner=owner, dataset_id=record.id, source_version=version,
                       parsing=parsing, descriptors=schema.descriptors, state='building', code=None,
                       progress=dict(phase='reading', records=0, canonical_bytes=0, elapsed_seconds=0),
                       indices=[], display_columns=[], selection_bytes=0, selection_sizes={}, policy=None, publication=None,
                       origin=None, receipts=[], candidate=None, prepared=False,
                       commitment_id=str(uuid4()), disclosure_version=str(uuid4()), proof_ids=[],
                       created_at=stamp())
            self.save(job)
            self.start(job)
        return self.status(job['id'], owner)

    def start(self, job):
        if self.live:
            raise BuildError('job_already_running')
        record = owned_dataset(self.processing, job['dataset_id'], job['owner'])
        path, version = source_identity(record, self.upload_root)
        if version != job['source_version']:
            raise BuildError('source_changed')
        cancel = threading.Event()
        live = {'cancel': cancel, 'builder': None, 'package': None}
        self.live[job['id']] = live

        def progress(value):
            with self.lock:
                current = self.load(job['id'], job['owner'])
                current['progress'] = value
                self.save(current)

        def review(tree, result):
            with self.lock:
                current = self.load(job['id'], job['owner'])
                if cancel.is_set():
                    return
                live['builder'] = CommitmentPreviewBuilder(tree, current['descriptors'])
                current['commitment'] = result['commitment']
                if current['state'] == 'building':
                    current['state'] = 'ready'
                self.save(current)
            # Holding the 2a context retains its process-wide flock and cleanup.
            cancel.wait()

        def run():
            try:
                run_commitment_job([path], ParsingDeclaration(**job['parsing']), job['descriptors'],
                                   self.root / 'worker', cancel=cancel, progress=progress, local_review=review)
            except Exception as exc:
                with self.lock:
                    current = self.load(job['id'], job['owner'])
                    if not cancel.is_set():
                        current.update(state='failed', code=exc.code if isinstance(exc, CommitmentValidationError) else 'build_failed')
                        self.save(current)
            finally:
                with self.lock:
                    self.live.pop(job['id'], None)
        thread = threading.Thread(target=run, daemon=True)
        live['thread'] = thread
        thread.start()

    def status(self, job_id, owner):
        with self.lock:
            job = self.load(job_id, owner)
            if job['state'] not in {'cancelled', 'failed', 'retired', 'withdrawn'} and job_id not in self.live:
                self.start(job)
            live = self.live.get(job_id)
            signing = {'fingerprint': None, 'code': None}
            if job.get('receipts'):
                try:
                    signing['fingerprint'] = self.signer(job).signer_reference[37:]
                except Exception:
                    signing['code'] = 'signing_authority_unavailable'
            return {
                'job_id': job_id, 'dataset_id': job['dataset_id'], 'source_version': job['source_version'],
                'state': job['state'], 'code': job['code'], 'progress': job['progress'],
                'review_ready': bool(live and live['builder']),
                'columns': [d[0] for d in job['descriptors']],
                'selection': {'leaf_indices': job['indices'], 'display_columns': job['display_columns'],
                              'rows': len(job['indices']), 'fields': len(job['descriptors']), 'canonical_bytes': job['selection_bytes'], 'row_sizes': job.get('selection_sizes', {})},
                'caps': CAPS, 'commitment': job.get('commitment'), 'policy': job['policy'],
                'publication': job['publication'], 'origin': job['origin'], 'receipts': job['receipts'],
                'candidate': job['candidate'], 'signing': signing, 'outcome': AWAITING if job['prepared'] else None,
            }

    def active(self, job):
        record = owned_dataset(self.processing, job['dataset_id'], job['owner'])
        _, version = source_identity(record, self.upload_root)
        if version != job['source_version']:
            job.update(state='failed', code='source_changed')
            self.save(job)
            if job['id'] in self.live:
                self.live[job['id']]['cancel'].set()
            raise BuildError('source_changed')
        live = self.live.get(job['id'])
        if not live or not live['builder']:
            raise BuildError('review_recovering')
        if job['state'] in {'cancelled', 'failed', 'retired', 'withdrawn'}:
            raise BuildError('job_inactive')
        return live

    def cancel(self, job_id, owner):
        with self.lock:
            job = self.load(job_id, owner)
            if job['publication']:
                raise BuildError('withdraw_required')
            job.update(state='cancelled', policy=None)
            self.save(job)
            live = self.live.get(job_id)
            if live:
                live['cancel'].set()
        if live:
            live['thread'].join(timeout=5)
        return self.status(job_id, owner)

    def row(self, builder, index):
        # Inspect length first: never materialize an 8MiB ineligible record in the API.
        if type(index) is not int or not 0 <= index < builder.tree.count:
            raise BuildError('invalid_selection', 422)
        with (builder.tree.directory / 'index').open('rb') as f:
            f.seek(index * 56)
            _, size, _, _ = struct.unpack('>QIQ32s4x', f.read(56))
        code = None
        if len(builder.schema.descriptors) > CAPS['fields']:
            code = 'fields_limit'
        elif size > CAPS['canonical_bytes']:
            code = 'canonical_bytes_limit'
        # Binary at any depth is ineligible even if a value is absent/null.
        elif '"binary"' in json.dumps(builder.schema.descriptors):
            code = 'binary_selection'
        if code:
            return {'leaf_index': index, 'canonical_bytes': size, 'code': code, 'cells': None}
        entry = builder._entry(index)
        try:
            from app.services.preview_package_service import _logical_record
            scan_row = _logical_record(json.loads(builder.schema.canonical_row(entry['row'])), builder.schema.descriptors, for_scan=True)
            for text, numeric in walk_selection([scan_row]):
                check_text(text, numeric)
        except PolicyError as exc:
            code = str(exc)
        return {'leaf_index': index, 'canonical_bytes': size, 'code': code, 'cells': entry['row']}

    def rows(self, job_id, owner, start, count):
        with self.lock:
            job = self.load(job_id, owner)
            builder = self.active(job)['builder']
            if start < 0 or not 1 <= count <= 25:
                raise BuildError('invalid_page', 422)
            rows, size = [], 0
            for index in range(start, min(start + count, builder.tree.count)):
                row = self.row(builder, index)
                cost = len(canonical_json_bytes(row))
                if rows and size + cost > 300000:
                    break
                rows.append(row)
                size += cost
            return {'items': rows, 'total': builder.tree.count, 'next': start + len(rows) if start + len(rows) < builder.tree.count else None}

    def select(self, job_id, owner, body):
        with self.lock:
            job = self.load(job_id, owner)
            builder = self.active(job)['builder']
            if job['publication'] or job['candidate']:
                raise BuildError('replace_preview_required')
            indices, columns = body.leaf_indices, body.display_columns
            limit('rows', len(indices))
            limit('fields', len(builder.schema.descriptors))
            limit('fields', len(columns))
            if indices != sorted(set(indices)) or not indices or len(columns) != len(set(columns)) or not columns or set(columns) - {d[0] for d in builder.schema.descriptors}:
                raise BuildError('invalid_selection', 422)
            size, sizes = 0, {}
            for index in indices:
                row = self.row(builder, index)
                if row['code']:
                    raise BuildError(row['code'], 422)
                size += row['canonical_bytes']
                sizes[index] = row['canonical_bytes']
                limit('canonical_bytes', size)
            job.update(indices=indices, display_columns=columns, selection_bytes=size, selection_sizes=sizes,
                       proof_ids=[str(uuid4()) for _ in indices], state='selected', policy=None)
            self.live[job_id]['package'] = None
            self.save(job)
            return self.status(job_id, owner)

    def scan(self, job_id, owner, consent):
        with self.lock:
            job = self.load(job_id, owner)
            live = self.active(job)
            if job['publication'] or job['candidate']:
                raise BuildError('replace_preview_required')
            if not consent.public_preview_permission or not consent.restricted_content_confirmed:
                raise BuildError('approval_required', 422)
            rights = capture_rights(RIGHTS[consent.rights_basis], consent.rights_basis, True)
            job['progress']['phase'] = 'scanning'
            self.save(job)
            try:
                package = live['builder'].prepare(
                    job['indices'], proof_ids=job['proof_ids'], commitment_id=job['commitment_id'],
                    disclosure_version=job['disclosure_version'], scanned_at=stamp(),
                    rights_confirmed=True, public_preview_permission=True, restricted_content_confirmed=True,
                    manifest_bytes=0, package_url='https://preview.example.invalid/' + 'x' * 2016)
                live['package'] = package
                job.update(state='scanned', rights=rights, scan=package.scan,
                           policy={'policy': POLICY, 'version': VERSION, 'passed': True,
                                   'sampled_leaf_list_digest': package.scan['sampled_leaf_list_digest'], 'reason_codes': []})
            except PolicyError as exc:
                live['package'] = None
                job.update(state='selected', policy={'policy': POLICY, 'version': VERSION, 'passed': False, 'reason_codes': [str(exc)]})
            job['progress']['phase'] = 'ready'
            self.save(job)
            return self.status(job_id, owner)

    def store(self, job):
        return PublicationStore(self.root / 'publications' / job['owner'].encode().hex(),
                                self.root / 'publication-journals' / job['owner'].encode().hex())

    def package(self, job_id, owner, destination):
        with self.lock:
            job = self.load(job_id, owner)
            live = self.active(job)
            if job['publication'] and job['publication']['destination'] != destination:
                raise BuildError('replace_preview_required')
            if job['publication']:
                self.download(job_id, owner)
                return self.status(job_id, owner)
            if not live['package']:
                raise BuildError('rescan_required')
            store = self.store(job)
            result = store.export(live['package'])
            relative = store.path(result['disclosure_version'], result['sample_hash'])
            job.update(state='packaged', publication={**result, 'destination': destination,
                       'relative_path': relative, 'local_directory': str(store.public_root)})
            self.save(job)
            return self.status(job_id, owner)

    def download(self, job_id, owner):
        with self.lock:
            job = self.load(job_id, owner)
            if not job['publication'] or job['state'] in {'withdrawn', 'retired'}:
                raise BuildError('package_unavailable')
            pub = job['publication']
            code, payload = self.store(job).read(pub['disclosure_version'], pub['sample_hash'])
            if code != 200:
                raise BuildError('package_unavailable')
            return payload

    def origin_check(self, job_id, owner, url):
        with self.lock:
            job = self.load(job_id, owner)
            self.active(job)
            pub = job['publication']
            if not pub or job['candidate']:
                raise BuildError('package_required')
            parsed = validate_url(url)
            if parsed.path.lstrip('/') != pub['relative_path']:
                raise BuildError('origin_path_mismatch', 422)
            receipts = verify_hosted_package(url, origin='https://ai.market', expected_sha256=pub['package_sha256'], expected_bytes=pub['byte_count'])
            job.update(state='hosted', origin=url, receipts=receipts)
            self.save(job)
            return self.status(job_id, owner)

    def approve_metadata(self, body, owner):
        # Human-approved browser projection digest, never a platform P1 allocation.
        # This private, labelled fixture context cannot enter any live endpoint.
        with self.lock:
            record = owned_dataset(self.processing, body.dataset_id, owner)
            _, version = source_identity(record, self.upload_root)
            old = record.metadata.get('preview_local_approval')
            if old and old['owner'] == owner and old['digest'] == body.approved_metadata_digest and old['references']['source_revision'] == version:
                return {'kind': 'local_metadata_approval', 'approval_id': old['references']['summary_approval_id']}
            references = {key: str(uuid4()) for key in ('summary_id','summary_approval_id','content_revision','listing_id')}
            # Local listing identity remains fixture-only until T allocates its binding.
            references.update(listing_version_id=None, source_revision=version,
                              summary_hash=body.approved_metadata_digest, render_hash=body.approved_metadata_digest,
                              aggregate_hash=body.approved_metadata_digest)
            record.metadata['preview_local_approval'] = {'kind': 'local_metadata_approval', 'owner': owner,
                                                       'digest': body.approved_metadata_digest, 'references': references}
            self.processing._save_record(record, record.upload_path.name)
            return {'kind': 'local_metadata_approval', 'approval_id': references['summary_approval_id']}

    def signer(self, job):
        from datetime import timedelta
        from app.config import settings
        from app.core.crypto import DeviceCrypto
        from app.services.registration_service import read_preview_registration_evidence
        from app.services.preview_signing_service import PreviewSigningService
        evidence_path = self.root / 'registration-evidence.json'
        evidence = read_preview_registration_evidence(evidence_path)
        if evidence.seller_id != job['owner']:
            raise BuildError('registration_owner_mismatch', 403)
        return PreviewSigningService(
            DeviceCrypto(settings.keystore_path, settings.keystore_passphrase),
            install_id=evidence.install_id, seller_id=job['owner'],
            evidence_reader=lambda: read_preview_registration_evidence(evidence_path),
            evidence_max_age=timedelta(hours=1))

    def candidate(self, job_id, owner, consent):
        from app.services.preview_signing_service import (
            LocalCandidate, construct_request, seller_attestation_digest, request_digest,
        )
        from app.services.preview_content_policy import scan_attestation_digest
        from app.services.dataset_merkle_service import encode_base64url
        with self.lock:
            job = self.load(job_id, owner)
            live = self.active(job)
            if not consent.metadata_accuracy_confirmed or not consent.public_preview_permission or not consent.restricted_content_confirmed:
                raise BuildError('approval_required', 422)
            if not job.get('rights') or job['rights']['rights_basis_code'] != consent.rights_basis:
                raise BuildError('rescan_required')
            if job['candidate']:
                return self.status(job_id, owner)  # immutable identical retry
            if job['state'] != 'hosted' or not job['receipts']:
                raise BuildError('origin_check_required')
            record = owned_dataset(self.processing, job['dataset_id'], owner)
            context = record.metadata.get('preview_local_approval')
            if not context or context.get('owner') != owner:
                raise BuildError('metadata_approval_required')
            p1 = context['references']
            signer = self.signer(job)
            reference = signer.signer_reference
            now, dummy = stamp(), encode_base64url(bytes(64))
            c = dict(commitment_id=job['commitment_id'], listing_id=p1['listing_id'],
                     seller_dataset_version=job['source_version'], schema_digest=job['commitment']['schema_digest'],
                     dataset_merkle_root=job['commitment']['dataset_merkle_root'], leaf_count=job['commitment']['leaf_count'],
                     aim_data_signer_reference=reference, seller_signature=dummy, signed_at=now)
            pub = job['publication']
            c['seller_attestation_digest'] = seller_attestation_digest({
                **{k:c[k] for k in ('listing_id','seller_dataset_version','schema_digest','dataset_merkle_root','leaf_count','signed_at')},
                'sample_hash':pub['sample_hash'], 'rights_basis_digest':job['rights']['rights_basis_digest'],
                'public_preview_permission':True, 'metadata_accuracy_confirmed':True})
            proofs = [dict(proof_id=pid, **live['builder'].tree.proof(index),
                           preview_package_url=job['origin'], package_media_type=MEDIA_TYPE,
                           package_profile='aim-preview-package-v2', package_byte_ceiling=1048576,
                           **job['scan'], signer_reference=reference, signature_algorithm='ed25519', signature=dummy)
                      for index,pid in zip(job['indices'],job['proof_ids'])]
            c['proofs'] = proofs
            proofs = [signer.sign_proof(c,p) for p in proofs]
            c['proofs'] = proofs
            c = signer.sign_commitment(c)
            binding = dict(profile='aim-preview-disclosure-v1',decision='approve', **p1,
                           disclosure_version=job['disclosure_version'], seller_id=owner,
                           selected_fields=job['display_columns'], preview_type='table',content_type='tabular',
                           sample_decision='approved',sample_hash=pub['sample_hash'],commitment_id=job['commitment_id'],
                           schema_digest=c['schema_digest'],seller_dataset_version=job['source_version'],
                           schema_descriptors=job['descriptors'],proof_ids=job['proof_ids'],
                           sampled_leaf_list_digest=job['scan']['sampled_leaf_list_digest'],
                           scan_attestation_digest=scan_attestation_digest(proofs),**job['rights'],
                           approved_by=owner,approved_at=now,last_attested_by_seller_at=now,update_cadence_days=None,
                           approval_expires_at=None,supersedes=None,request_id=str(uuid4()),expected_current_disclosure_id=None,
                           signer_reference=reference,signature_algorithm='ed25519',signature_profile='aim-preview-disclosure-signature-v1')
            candidate = LocalCandidate.validate(binding)
            request = construct_request(candidate,c,proofs,signer=signer,approved_p1=p1)
            self.freeze(job,candidate,request)
            job['approval_digest'] = context['digest']
            self.save(job)
            return self.status(job_id,owner)

    def freeze(self, job, candidate, request):
        from app.services.preview_signing_service import request_digest
        key = self.journal.start(candidate)
        for state in ('selected','scanned','hosted','ready_to_sign'):
            self.journal.transition(key,state)
        self.journal.freeze(key,request)
        job.update(state='signed_candidate', journal_key=list(key),
                   candidate={'kind':'fixture_candidate','request_digest':request_digest(request),
                              'key_fingerprint':request['binding']['signer_reference'][37:],
                              'sample_hash':request['binding']['sample_hash'],
                              'disclosure_version':request['binding']['disclosure_version']})
        self.save(job)

    def submit(self, job_id, owner):
        # Intentionally has no transport or submit_preview_request call.
        with self.lock:
            job=self.load(job_id,owner)
            self.active(job)
            if not job['candidate']:
                raise BuildError('signed_candidate_required')
            record=owned_dataset(self.processing,job['dataset_id'],owner)
            if (record.metadata.get('preview_local_approval') or {}).get('digest') != job.get('approval_digest'):
                raise BuildError('metadata_approval_changed')
            self.journal.read(tuple(job['journal_key']))
            job.update(prepared=True)
            self.save(job)
            return self.status(job_id,owner)

    def withdraw(self, job_id, owner):
        from app.services.preview_lifecycle import withdrawal_candidate
        from app.services.preview_signing_service import construct_request
        with self.lock:
            job=self.load(job_id,owner)
            pub=job['publication']
            if not pub:
                raise BuildError('package_required')
            if job['candidate'] and job['state'] not in {'withdrawn','retired'}:
                old=json.loads(self.journal.read(tuple(job['journal_key']))['candidate'])
                candidate=withdrawal_candidate(old,disclosure_version=str(uuid4()),request_id=str(uuid4()),approved_at=stamp())
                p1={k:old[k] for k in ('summary_id','summary_approval_id','summary_hash','render_hash','aggregate_hash','content_revision','source_revision','listing_id','listing_version_id')}
                req=construct_request(candidate,None,[],signer=self.signer(job),approved_p1=p1)
                self.freeze(job,candidate,req)
            self.store(job).retire(pub['disclosure_version'],pub['sample_hash'])
            job.update(state='withdrawn',prepared=False,code='external_retirement_pending',receipts=[])
            self.save(job)  # Persist pending before GET/OPTIONS, including failed retries.
            if job_id in self.live:
                self.live[job_id]['cancel'].set()
            if job['origin']:
                receipts=verify_hosted_package(job['origin'],origin='https://ai.market',expected_sha256=pub['package_sha256'],expected_bytes=pub['byte_count'],retired=True)
                from app.services.preview_lifecycle import validate_retirement_receipts
                validate_retirement_receipts(receipts,url=job['origin'],origin='https://ai.market')
                job.update(state='retired',code=None,receipts=receipts)
                self.save(job)
            return self.status(job_id,owner)

    def refresh(self, job_id, owner, consent):
        # Rebuild/rescan with fresh immutable package identities. Never re-use old approval.
        with self.lock:
            job=self.load(job_id,owner)
            if not consent.metadata_accuracy_confirmed or not consent.public_preview_permission or not consent.restricted_content_confirmed:
                raise BuildError('approval_required',422)
            if job['state'] not in {'retired','withdrawn','cancelled','failed'}:
                raise BuildError('retire_previous_preview_first')
            from app.models.preview_build_schemas import CreateBuild
            body=CreateBuild(dataset_id=job['dataset_id'],parsing=job['parsing'],schema_descriptors=job['descriptors'])
        return self.create(body,owner)
