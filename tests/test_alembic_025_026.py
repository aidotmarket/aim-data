"""Behaviour-neutral upgrades on a synthetic AC7-shaped install copy."""
from importlib.util import module_from_spec, spec_from_file_location
from pathlib import Path

import pytest
import sqlalchemy as sa
from alembic.migration import MigrationContext
from alembic.operations import Operations
from alembic.config import Config
from alembic.script import ScriptDirectory


def test_025_chain_and_upgrade_copy(tmp_path):
    path = Path('alembic/versions/025_bq_multi_file_datasets_s1717.py')
    spec = spec_from_file_location('migration025', path)
    module = module_from_spec(spec); spec.loader.exec_module(module)
    assert module.down_revision == '024_bq_data_verification_s1590'
    scripts = ScriptDirectory.from_config(Config('alembic.ini'))
    assert scripts.get_heads() == ["026_bq_published_manifests_s1717"]
    source = sa.create_engine(f'sqlite:///{tmp_path}/install.db')
    with source.begin() as connection:
        connection.exec_driver_sql('CREATE TABLE dataset_records (id VARCHAR(36) PRIMARY KEY, original_filename TEXT, storage_filename TEXT, processed_path TEXT, status TEXT, listing_id TEXT, batch_id TEXT)')
        connection.exec_driver_sql("INSERT INTO dataset_records VALUES ('legacy','sample.csv','stored.csv','/processed/legacy.parquet','preview_ready','listing','shared')")
    import shutil
    shutil.copyfile(tmp_path / 'install.db', tmp_path / 'copy.db')
    engine = sa.create_engine(f'sqlite:///{tmp_path}/copy.db')
    with engine.begin() as connection:
        before = connection.exec_driver_sql('SELECT * FROM dataset_records').all()
        with Operations.context(MigrationContext.configure(connection)):
            module.upgrade()
        rows = connection.exec_driver_sql('SELECT * FROM dataset_records').all()
        assert [tuple(row[:-1]) for row in rows] == [tuple(row) for row in before]
        assert rows[0][-1] is None
        assert connection.exec_driver_sql('SELECT count(*) FROM dataset_members').scalar() == 0
        columns = {x['name'] for x in sa.inspect(connection).get_columns('dataset_members')}
        assert {'dataset_id','index','relative_path','size_bytes','sha256','detected_type','role','is_sample','status','reason','mtime','created_at','updated_at'} == columns
        with pytest.raises(sa.exc.IntegrityError):
            connection.exec_driver_sql('''INSERT INTO dataset_members (dataset_id,"index",relative_path,size_bytes,detected_type,role,is_sample,status,created_at,updated_at) VALUES ('legacy',0,'a',1,'csv','other',1,'current',CURRENT_TIMESTAMP,CURRENT_TIMESTAMP)''')
        with Operations.context(MigrationContext.configure(connection)):
            module.downgrade()
        assert connection.exec_driver_sql('SELECT * FROM dataset_records').all() == before


def test_026_retains_snapshots_without_changing_legacy_rows(tmp_path):
    spec = spec_from_file_location('migration026', Path('alembic/versions/026_bq_published_manifests_s1717.py'))
    module = module_from_spec(spec); spec.loader.exec_module(module)
    assert module.down_revision == '025_bq_multi_file_datasets_s1717'
    engine = sa.create_engine(f'sqlite:///{tmp_path}/install.db')
    with engine.begin() as connection:
        connection.exec_driver_sql('CREATE TABLE dataset_records (id TEXT PRIMARY KEY, listing_id TEXT, batch_id TEXT, processed_path TEXT, original_filename TEXT, storage_filename TEXT, status TEXT)')
        for i, listing in enumerate(('published', None)):
            connection.execute(sa.text('INSERT INTO dataset_records VALUES (:id,:listing,"shared","/processed/a.parquet","sample.csv","stored.csv","preview_ready")'), dict(id=str(i), listing=listing))
        before = connection.exec_driver_sql('SELECT * FROM dataset_records').all()
        with Operations.context(MigrationContext.configure(connection)):
            module.upgrade()
        assert connection.exec_driver_sql('SELECT * FROM dataset_records').all() == before
        columns = {column['name'] for column in sa.inspect(connection).get_columns('published_manifests')}
        assert 'registration_to_published_index' in columns
        connection.exec_driver_sql("INSERT INTO published_manifests (listing_version_id, manifest_hash, dataset_id, root_path, members, created_at) VALUES ('version', 'hash', 'dataset', '/root', '[]', CURRENT_TIMESTAMP)")
        assert connection.exec_driver_sql("SELECT registration_to_published_index FROM published_manifests").scalar() == '{}'
        from app.models.published_manifest import PublishedManifest
        from alembic.autogenerate import compare_metadata
        metadata = sa.MetaData()
        PublishedManifest.__table__.to_metadata(metadata)
        context = MigrationContext.configure(connection, opts={
            'include_object': lambda obj, name, kind, reflected, compare_to:
                kind != 'table' or name == 'published_manifests',
        })
        assert compare_metadata(context, metadata) == []
        assert sa.inspect(connection).get_pk_constraint('published_manifests')['constrained_columns'] == ['listing_version_id', 'manifest_hash']
        with Operations.context(MigrationContext.configure(connection)):
            module.downgrade()
        assert connection.exec_driver_sql('SELECT * FROM dataset_records').all() == before



def test_env_registers_published_manifests_in_fresh_process():
    # A fresh interpreter prevents earlier test imports masking a missing env import.
    import subprocess
    import sys
    subprocess.run([sys.executable, "-c", """
import runpy
from unittest.mock import patch
from alembic import context
from alembic.config import Config
from sqlmodel import SQLModel
assert 'published_manifests' not in SQLModel.metadata.tables
config = Config()
config.set_main_option('sqlalchemy.url', 'sqlite://')
with patch.object(context, 'config', config, create=True), patch.object(context, 'is_offline_mode', return_value=True), patch.object(context, 'configure'), patch.object(context, 'begin_transaction'), patch.object(context, 'run_migrations'):
    runpy.run_path('alembic/env.py')
assert 'published_manifests' in SQLModel.metadata.tables
"""], check=True)
