import copy
import hashlib
import json
from pathlib import Path

import pytest
from app.services.dataset_manifest import build_manifest, canonical_json_bytes, canonical_path

FIXTURE = Path(__file__).parent / 'fixtures/multi_file_datasets/manifest_golden.json'


def test_golden():
    raw = FIXTURE.read_bytes()
    assert hashlib.sha256(raw).hexdigest() == 'ca7c2cf2fb22d95b22cc5d9bf8fd78309ad784c2e104d76ee8e852b1ac40aba0'
    value = json.loads(raw)
    assert build_manifest(value['members'])['manifest_hash'] == value['manifest_hash']
    assert hashlib.sha256(canonical_json_bytes(value['members'])).hexdigest() == value['manifest_hash']
    assert raw == canonical_json_bytes(value) + b'\n'


@pytest.mark.parametrize('field,value', [('size_bytes', 1), ('relative_path', 'other.csv'), ('sha256', '1' * 64), ('role', 'other'), ('is_sample', True), ('index', 7)])
def test_tampering(field, value):
    fixture = json.loads(FIXTURE.read_bytes())
    rows = copy.deepcopy(fixture['members'])
    rows[0][field] = value
    assert build_manifest(rows)['manifest_hash'] != fixture['manifest_hash']


def test_order_and_scalars():
    rows = json.loads(FIXTURE.read_bytes())['members']
    assert build_manifest(rows)['manifest_hash'] != build_manifest(rows[::-1])['manifest_hash']
    expected = build_manifest(rows)
    rows[0]['status'] = 'current'
    rows[0]['member_count'] = 500
    assert build_manifest(rows) == expected
    assert expected['member_count'] == 2 and expected['data_member_count'] == 1
    assert expected['sample_member_count'] == expected['total_data_bytes'] == 0


@pytest.mark.parametrize('path', ['/abs', '../file', './file', 'a//b', 'a\\b', 'a\x00b', 'a\x7fb', 'é' * 513, '\udcff'])
def test_invalid_path(path):
    with pytest.raises(ValueError): canonical_path(path)


def test_unicode_canonicalization():
    assert canonical_path('e\u0301.csv') == 'é.csv'
    assert canonical_json_bytes({'é': 'é'}) == '{"é":"é"}'.encode()


def test_config_defaults_and_aliases(monkeypatch):
    from app.config import Settings
    for prefix in ('AIM_DATA_', 'VECTORAIZ_'):
        monkeypatch.setenv(prefix + 'MULTI_FILE_DATASETS_ENABLED', 'true')
        monkeypatch.setenv(prefix + 'DATASET_MAX_MEMBERS', '21001')
        config = Settings(_env_file=None)
        assert config.multi_file_datasets_enabled and config.dataset_max_members == 21001
        monkeypatch.delenv(prefix + 'MULTI_FILE_DATASETS_ENABLED')
        monkeypatch.delenv(prefix + 'DATASET_MAX_MEMBERS')
    config = Settings(_env_file=None)
    assert config.multi_file_datasets_enabled is False
    assert config.dataset_max_members == 50000 and config.dataset_max_bytes == 64 * 1024**3
    assert config.directory_read_max_attempts == 3
