import importlib
from typing import get_args, get_origin

import pytest
from pydantic import AliasChoices


@pytest.fixture
def reload_config():
    import app.config

    yield lambda: importlib.reload(app.config)


def _field_env_names(field_name: str) -> tuple[str, str]:
    env_name = field_name.upper()
    return f"AIM_DATA_{env_name}", f"VECTORAIZ_{env_name}"


def _alias_choices(field) -> list[str]:
    alias = field.validation_alias
    assert isinstance(alias, AliasChoices)
    return list(alias.choices)


def _sample_env_value(field_name: str, annotation) -> tuple[str, object]:
    if field_name == "ai_market_aws_account_id":
        return "123456789012", "123456789012"
    if field_name == "ai_market_assume_role_principal_arn":
        arn = "arn:aws:iam::123456789012:role/aim-data-test"
        return arn, arn
    if field_name == "cors_origins":
        return '["https://aim.example"]', ["https://aim.example"]
    if field_name == "allowed_raw_file_dirs":
        return '["/tmp/aim-data-raw"]', ["/tmp/aim-data-raw"]
    if field_name == "hybrid_search_mode":
        return "dense_only", "dense_only"

    origin = get_origin(annotation)
    args = get_args(annotation)
    if annotation is bool:
        return "true", True
    if annotation is int:
        return "17", 17
    if annotation is float:
        return "1.75", 1.75
    if annotation is str or (origin is None and str in args):
        return f"{field_name}-value", f"{field_name}-value"
    if origin is list:
        return '["sample"]', ["sample"]
    if type(None) in args:
        non_none_args = [arg for arg in args if arg is not type(None)]
        if non_none_args and non_none_args[0] is str:
            return f"{field_name}-value", f"{field_name}-value"

    return f"{field_name}-value", f"{field_name}-value"


def _clear_settings_env(monkeypatch, config_mod):
    for name, field in config_mod.Settings.model_fields.items():
        for choice in _alias_choices(field):
            monkeypatch.delenv(choice, raising=False)
        primary, legacy = _field_env_names(name)
        monkeypatch.delenv(primary, raising=False)
        monkeypatch.delenv(legacy, raising=False)
    monkeypatch.delenv("AI_MARKET_AWS_ACCOUNT_ID", raising=False)
    monkeypatch.delenv("AI_MARKET_ASSUME_ROLE_PRINCIPAL_ARN", raising=False)
    monkeypatch.delenv("AIM_DATA_AWS_ACCOUNT_ID", raising=False)
    monkeypatch.delenv("AIM_DATA_AWS_PRINCIPAL_ARN", raising=False)
    monkeypatch.delenv("VECTORAIZ_AWS_PRINCIPAL_ARN", raising=False)
    monkeypatch.delenv("AIM_DATA_VERSION", raising=False)
    monkeypatch.delenv("VECTORAIZ_VERSION", raising=False)


def test_every_settings_field_has_primary_and_legacy_aliases(reload_config):
    mod = reload_config()
    for name, field in mod.Settings.model_fields.items():
        primary, legacy = _field_env_names(name)
        choices = _alias_choices(field)
        assert primary in choices, name
        assert legacy in choices, name


@pytest.mark.parametrize("prefix", ["AIM_DATA", "VECTORAIZ"])
def test_every_settings_field_resolves_from_primary_and_legacy_prefixes(monkeypatch, reload_config, prefix):
    mod = reload_config()
    _clear_settings_env(monkeypatch, mod)

    for name, field in mod.Settings.model_fields.items():
        _clear_settings_env(monkeypatch, mod)
        env_value, expected = _sample_env_value(name, field.annotation)
        monkeypatch.setenv(f"{prefix}_{name.upper()}", env_value)
        mod = reload_config()
        assert getattr(mod.settings, name) == expected, name


def test_aim_data_prefix_wins_when_both_set(monkeypatch, reload_config):
    mod = reload_config()
    _clear_settings_env(monkeypatch, mod)
    monkeypatch.setenv("VECTORAIZ_KEYSTORE_PASSPHRASE", "vec-loses")
    monkeypatch.setenv("AIM_DATA_KEYSTORE_PASSPHRASE", "aim-wins")
    mod = reload_config()
    assert mod.settings.keystore_passphrase == "aim-wins"
