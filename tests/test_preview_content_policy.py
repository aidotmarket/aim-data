import pytest

from app.services.preview_content_policy import (
    PreviewContentPolicy,
    PreviewPolicyError,
    PreviewRightsBasis,
)


RIGHTS = PreviewRightsBasis("seller owns this data", True, "seller_owned")


@pytest.mark.parametrize(
    "value,code",
    [
        ("person@example.com", "personal_data_detected"),
        ("AKIAIOSFODNN7EXAMPLE", "credential_detected"),
        ("-----BEGIN PRIVATE KEY-----", "private_key_detected"),
        ("postgresql://user:pass@host/db", "connection_string_detected"),
        ("<script>alert(1)</script>", "executable_markup_detected"),
        ("Sub AutoOpen()\nEnd Sub", "macro_detected"),
        ("=HYPERLINK(\"x\")", "spreadsheet_formula_detected"),
        ("bad\x01control", "control_character_detected"),
    ],
)
def test_policy_fixtures_have_distinct_stable_codes(value, code):
    with pytest.raises(PreviewPolicyError) as exc:
        PreviewContentPolicy(require_presidio=False).scan_rows([{"value": value}], rights_basis=RIGHTS)
    assert exc.value.code == code


def test_rights_basis_and_long_copyright_uncertainty_fail_closed():
    policy = PreviewContentPolicy(require_presidio=False)
    with pytest.raises(PreviewPolicyError) as missing:
        policy.scan_rows([{"value": "lawful short value"}], rights_basis=PreviewRightsBasis("", True, "seller_owned"))
    assert missing.value.code == "rights_basis_missing"
    with pytest.raises(PreviewPolicyError) as long_text:
        policy.scan_rows([{"value": "A" * 1001}], rights_basis=RIGHTS)
    assert long_text.value.code == "copyright_excerpt_uncertain"


def test_lawful_bounded_row_passes_without_a_clearance_badge():
    result = PreviewContentPolicy(require_presidio=False).scan_rows(
        [{"category": "aggregate", "count": 12}], rights_basis=RIGHTS
    )
    assert result.verdict == "passed"
    assert result.policy == "aim-preview-policy-v1"
