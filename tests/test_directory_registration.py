import hashlib
import os
from pathlib import Path
from unittest.mock import patch

import pytest
from sqlmodel import Session, SQLModel, create_engine, select

from app.models.dataset import DatasetMember, DatasetRecord
from app.services import directory_registration as reg, import_service


@pytest.fixture
def directory_env(tmp_path, monkeypatch):
    monkeypatch.setattr(reg.settings, 'multi_file_datasets_enabled', True)
    monkeypatch.setattr(import_service, 'IMPORT_ROOT', tmp_path.resolve())
    root = tmp_path / 'dataset'
    root.mkdir()
    engine = create_engine('sqlite://')
    SQLModel.metadata.create_all(engine)
    with Session(engine) as session:
        yield root, session


def test_21000_complete_deep_registration(directory_env):
    root, session = directory_env
    nested = root.joinpath(*['deep'] * 8)
    nested.mkdir(parents=True)
    for i in range(20996): (nested / f'{i:05}.csv').write_bytes(b'x\n1\n')
    for name, body in [('LICENSE', b'license'), ('extensionless', b'plain'), ('binary.bin', b'\x00\xff'), ('empty.csv', b'')]:
        (root / name).write_bytes(body)
    row = reg.register_directory(session, root)
    session.commit()
    members = session.exec(select(DatasetMember)).all()
    assert len(members) == 21000
    assert len(session.exec(select(DatasetRecord)).all()) == 1
    by_path = {m.relative_path: m for m in members}
    assert by_path['LICENSE'].role == 'documentation'
    assert by_path['extensionless'].role == by_path['binary.bin'].role == 'other'
    assert by_path['empty.csv'].sha256 == hashlib.sha256(b'').hexdigest()
    assert not any(m.is_sample for m in members)
    assert row.file_type == 'directory' and row.processed_path is None
    assert row.original_filename == row.storage_filename == root.name
    assert row.file_size_bytes == 20996 * 4


def test_reregister_marks_removed_and_preserves_choices(directory_env):
    root, session = directory_env
    (root / 'a.csv').write_bytes(b'a')
    (root / 'b.csv').write_bytes(b'b')
    dataset = reg.register_directory(session, root)
    session.commit()
    member = session.get(DatasetMember, (dataset.id, 0))
    member.is_sample = True
    session.add(member)
    session.commit()
    stamp = member.updated_at
    again = reg.register_directory(session, root)
    assert again.id == dataset.id and member.updated_at == stamp
    (root / 'b.csv').unlink()
    (root / 'a.csv').write_bytes(b'changed')
    reg.register_directory(session, root)
    session.commit()
    assert member.is_sample and member.sha256 == hashlib.sha256(b'changed').hexdigest()
    assert session.get(DatasetMember, (dataset.id, 1)).status == 'removed'
    assert len(session.exec(select(DatasetMember)).all()) == 2


@pytest.mark.parametrize('bound,value', [('dataset_max_members', 1), ('dataset_max_bytes', 1)])
def test_bound_refuses_whole_registration(directory_env, monkeypatch, bound, value):
    root, session = directory_env
    (root / 'a.csv').write_bytes(b'a')
    (root / 'b.csv').write_bytes(b'b')
    monkeypatch.setattr(reg.settings, bound, value)
    with pytest.raises(ValueError, match=bound.upper() + '=1'):
        reg.register_directory(session, root)
    assert not session.exec(select(DatasetRecord)).all()
    assert not session.exec(select(DatasetMember)).all()


def test_hidden_junk_symlink_policy(directory_env):
    root, session = directory_env
    (root / '.hidden').mkdir()
    (root / '.hidden' / 'ignored.csv').write_bytes(b'x')
    (root / 'Thumbs.db').write_bytes(b'x')
    (root / 'real.csv').write_bytes(b'x')
    (root / 'link.csv').symlink_to(root / 'real.csv')
    reg.register_directory(session, root)
    assert [m.relative_path for m in session.exec(select(DatasetMember)).all()] == ['real.csv']


def test_unreadable_entry_refuses_whole_registration(directory_env, monkeypatch):
    root, session = directory_env
    (root / 'no.csv').write_bytes(b'x')
    original = reg.os.open
    def denied(name, *args, **kwargs):
        if name == 'no.csv': raise PermissionError()
        return original(name, *args, **kwargs)
    monkeypatch.setattr(reg.os, 'open', denied)
    with pytest.raises(ValueError, match='Unreadable entry.*no.csv'):
        reg.register_directory(session, root)
    assert not session.exec(select(DatasetRecord)).all()


def test_same_size_mtime_preserving_mutation_refuses(directory_env, monkeypatch):
    root, session = directory_env
    path = root / 'a.csv'
    path.write_bytes(b'a' * 32)
    original = reg._digest
    def mutate(fd, maximum, entry):
        result = original(fd, maximum, entry)
        st = path.stat()
        path.write_bytes(b'b' * 32 if path.read_bytes()[0] == 97 else b'a' * 32)
        os.utime(path, ns=(st.st_atime_ns, st.st_mtime_ns))
        return result
    monkeypatch.setattr(reg, '_digest', mutate)
    with pytest.raises(ValueError, match='DIRECTORY_READ_MAX_ATTEMPTS=3.*a.csv'):
        reg.register_directory(session, root)
    assert not session.exec(select(DatasetRecord)).all()


@pytest.mark.parametrize('names', [('a.csv', 'A.csv'), ('é.csv', 'e\u0301.csv')])
def test_collisions_even_on_normalizing_filesystems(directory_env, monkeypatch, names):
    root, session = directory_env
    for name in names: (root / name).write_bytes(b'x')
    real_scandir = reg.os.scandir
    # APFS cannot hold both names: emulate only readdir's two raw names while
    # retaining real fd-relative stat/read operations for each existing spelling.
    class Entry:
        def __init__(self, name): self.name = name
        def is_symlink(self): return False
        def stat(self, **kwargs): return (root / self.name).stat()
    class Entries:
        def __init__(self): self.items = iter([Entry(n) for n in names])
        def __next__(self): return next(self.items)
        def close(self): pass
    monkeypatch.setattr(reg.os, 'scandir', lambda fd: Entries())
    with pytest.raises(ValueError, match='collision'):
        reg.register_directory(session, root)
    assert not session.exec(select(DatasetRecord)).all()


@pytest.mark.parametrize('name', ['bad\n.csv', 'a' * 250 + '/' + 'b' * 250 + '/' + 'c' * 250 + '/' + 'd' * 250 + '/' + 'e' * 25])
def test_path_refusal(directory_env, name):
    root, session = directory_env
    fd = os.open(root, os.O_RDONLY | os.O_DIRECTORY)
    try:
        parts = name.split('/')
        for part in parts[:-1]:
            os.mkdir(part, dir_fd=fd)
            child = os.open(part, os.O_RDONLY | os.O_DIRECTORY, dir_fd=fd)
            os.close(fd)
            fd = child
        file_fd = os.open(parts[-1], os.O_WRONLY | os.O_CREAT, 0o600, dir_fd=fd)
        os.write(file_fd, b'x')
        os.close(file_fd)
    finally:
        os.close(fd)
    with pytest.raises(ValueError, match='relative_path'):
        reg.register_directory(session, root)
    assert not session.exec(select(DatasetRecord)).all()


def test_flag_off(directory_env, monkeypatch):
    root, session = directory_env
    monkeypatch.setattr(reg.settings, 'multi_file_datasets_enabled', False)
    with pytest.raises(ValueError, match='disabled'): reg.register_directory(session, root)
