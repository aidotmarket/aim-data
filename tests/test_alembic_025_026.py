"""Chunk A owns 025 only; 026 will be supplied by chunk C."""
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
    assert scripts.get_heads() == [module.revision]
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
