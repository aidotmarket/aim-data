"""AC7 contract cases; synthetic install copy, not a claimed real-install receipt."""
import hashlib
from pathlib import Path

import pytest
from sqlmodel import Session, SQLModel, create_engine, select

from app.config import settings
from app.models.dataset import DatasetMember, DatasetRecord
from app.services.member_migration import migrate_members
from app.services.source_artifact_resolver import _resolve_file_path
from app.services.dataset_manifest import build_manifest


@pytest.fixture
def install_copy(tmp_path, monkeypatch):
    monkeypatch.setattr(settings, 'multi_file_datasets_enabled', True)
    uploads, processed = tmp_path / 'uploads', tmp_path / 'processed'
    uploads.mkdir(); processed.mkdir()
    monkeypatch.setattr(settings, 'upload_directory', str(uploads))
    monkeypatch.setattr(settings, 'processed_directory', str(processed))
    source = create_engine(f'sqlite:///{tmp_path}/install.db')
    SQLModel.metadata.create_all(source)
    with Session(source) as session:
        for i, kind in enumerate(('explicit', 'standard', 'upload', 'missing')):
            record = DatasetRecord(id=kind, original_filename='sample-' + kind + '.csv',
                                   storage_filename='stored-' + kind + '.csv', file_type='csv',
                                   batch_id='shared', listing_id='listing-' + kind if i % 2 == 0 else None,
                                   status='preview_ready' if i % 2 == 0 else 'uploaded')
            if kind == 'explicit':
                record.processed_path = str(processed / 'chosen.parquet')
                Path(record.processed_path).write_bytes(b'explicit bytes')
                (processed / f'{kind}.parquet').write_bytes(b'not selected')
            if kind == 'standard': (processed / f'{kind}.parquet').write_bytes(b'standard bytes')
            if kind != 'missing': (uploads / record.storage_filename).write_bytes(b'original bytes')
            session.add(record)
        session.commit()
    # Exercise a copied on-disk install, not only an in-memory table.
    import shutil
    shutil.copyfile(tmp_path / 'install.db', tmp_path / 'copy.db')
    engine = create_engine(f'sqlite:///{tmp_path}/copy.db')
    with Session(engine) as session:
        yield session


def test_ac7_legacy_identity_and_bytes_preserved(install_copy):
    session = install_copy
    records = session.exec(select(DatasetRecord)).all()
    before = {d.id: d.model_dump() for d in records}
    selected = {d.id: _resolve_file_path(d, upload_directory=settings.upload_directory,
                                      processed_directory=settings.processed_directory) for d in records}
    assert migrate_members(session) == 4
    session.commit()
    assert migrate_members(session) == 0
    for record in records:
        after = record.model_dump(); after.pop('root_path')
        old = before[record.id]; old.pop('root_path')
        assert after == old
        member = session.get(DatasetMember, (record.id, 0))
        assert member.role == 'data' and not member.is_sample
        assert member.detected_type == record.file_type
        assert _resolve_file_path(record, upload_directory=settings.upload_directory,
                                  processed_directory=settings.processed_directory) == selected[record.id]
        if selected[record.id]:
            bound = Path(record.root_path) / member.relative_path
            assert bound == Path(selected[record.id])
            assert member.sha256 == hashlib.sha256(bound.read_bytes()).hexdigest()
            assert build_manifest([member])['sample_member_count'] == 0
        else:
            assert member.status == 'missing' and member.sha256 is None
            with pytest.raises(ValueError): build_manifest([member])


def test_backfill_flag_off(install_copy, monkeypatch):
    monkeypatch.setattr(settings, 'multi_file_datasets_enabled', False)
    with pytest.raises(ValueError, match='disabled'): migrate_members(install_copy)
    assert not install_copy.exec(select(DatasetMember)).all()


def test_legacy_unknown_type_maps_to_manifest_domain(install_copy):
    record = install_copy.get(DatasetRecord, 'upload')
    record.file_type = 'legacy-binary'
    install_copy.add(record)
    install_copy.commit()
    migrate_members(install_copy)
    member = install_copy.get(DatasetMember, ('upload', 0))
    assert member.detected_type == 'unsupported'
    assert build_manifest([member])['members'][0]['detected_type'] == 'unsupported'
    assert record.file_type == 'legacy-binary'


@pytest.mark.parametrize('reason', ['Unreadable entry', 'changing entry'])
def test_backfill_failure_names_source_and_rolls_back(install_copy, monkeypatch, reason):
    from app.services import member_migration
    from app.services.directory_registration import DirectoryRegistrationError
    original = member_migration.stable_read
    record = install_copy.get(DatasetRecord, 'upload')
    path = _resolve_file_path(record, upload_directory=settings.upload_directory,
                              processed_directory=settings.processed_directory)

    def fail_selected(candidate):
        if Path(candidate) == Path(path):
            raise DirectoryRegistrationError(reason)
        return original(candidate)

    monkeypatch.setattr(member_migration, 'stable_read', fail_selected)
    with pytest.raises(DirectoryRegistrationError) as error:
        migrate_members(install_copy)
    assert record.id in str(error.value) and str(path) in str(error.value)
    assert reason in str(error.value)
    install_copy.rollback()  # Caller retains the existing all-or-nothing transaction.
    assert not install_copy.exec(select(DatasetMember)).all()
    assert all(d.root_path is None for d in install_copy.exec(select(DatasetRecord)).all())
    monkeypatch.setattr(member_migration, 'stable_read', original)
    assert migrate_members(install_copy) == 4
