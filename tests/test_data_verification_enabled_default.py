"""Regression coverage for the data_verification_enabled() default and opt-out.

Since v1.22.10 the feature is on when neither variable is set. Explicit
DATA_VERIFICATION_ENABLED wins over the AIM_DATA_ alias.
"""

import pytest

from app.services.data_verification_local_service import data_verification_enabled


@pytest.mark.parametrize(
    ("legacy", "alias", "expected"),
    [
        (None, None, True),
        (None, "false", False),
        (None, "true", True),
        ("false", "true", False),
        ("true", "false", True),
        (None, "0", False),
        (None, "off", False),
    ],
)
def test_data_verification_enabled_default_and_opt_out(monkeypatch, legacy, alias, expected):
    monkeypatch.delenv("DATA_VERIFICATION_ENABLED", raising=False)
    monkeypatch.delenv("AIM_DATA_DATA_VERIFICATION_ENABLED", raising=False)
    if legacy is not None:
        monkeypatch.setenv("DATA_VERIFICATION_ENABLED", legacy)
    if alias is not None:
        monkeypatch.setenv("AIM_DATA_DATA_VERIFICATION_ENABLED", alias)
    assert data_verification_enabled() is expected
