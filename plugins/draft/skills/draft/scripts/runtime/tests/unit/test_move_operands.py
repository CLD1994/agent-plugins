from __future__ import annotations


def test_path_internal_is_registered_with_rendering() -> None:
    from draft.diagnostic_codes import CheckCode
    from draft.diagnostics import KNOWN_CHECK_CODES

    assert CheckCode.PATH_INTERNAL.value in KNOWN_CHECK_CODES
