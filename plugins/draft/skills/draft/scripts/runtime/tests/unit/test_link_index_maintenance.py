from __future__ import annotations

from pathlib import Path

import pytest

from draft.diagnostic_codes import CheckCode
from draft.errors import DraftError
from draft.link_index_maintenance import (
    LinkIndexAuthority,
    LinkIndexBaseline,
    next_link_index_revision,
    select_link_index_authority,
)
from draft.models import (
    LinkIndexResult,
    OperationKind,
    OperationPhase,
    RootState,
)
from draft.observations import EMPTY_OBSERVATIONS
from draft.operations import Journal


def _journal(
    *,
    revision: str | None,
    result: LinkIndexResult | None,
    sha256: str | None,
    phase: OperationPhase,
) -> Journal:
    return Journal(
        operation_id="a" * 32,
        kind=OperationKind.CREATE,
        phase=phase,
        root_state=RootState.REGISTERED,
        observations=EMPTY_OBSERVATIONS,
        mutations=(),
        remove_empty_directories=(),
        metadata={},
        directory=Path("/draft/.draft/operations/aaaaaaaaaaaaaaaaaaaaaaaaaaaaaaaa"),
        link_index_revision=revision,
        link_index_result=result,
        link_index_sha256=sha256,
    )


def _published(revision: str) -> Journal:
    return _journal(
        revision=revision,
        result=LinkIndexResult.PUBLISHED,
        sha256="a" * 64,
        phase=OperationPhase.LINK_INDEX_PUBLISHED,
    )


def _rebuild(revision: str) -> Journal:
    return _journal(
        revision=revision,
        result=LinkIndexResult.REBUILD_REQUIRED,
        sha256=None,
        phase=OperationPhase.LINK_INDEX_REBUILD_REQUIRED,
    )


# ---------------------------------------------------------------------------
# select_link_index_authority：数值最大 revision 胜出（spec §3）
# ---------------------------------------------------------------------------


def test_selects_numeric_maximum_revision() -> None:
    authority = select_link_index_authority((_published("9"), _rebuild("10")))
    assert authority.revision == "10"
    assert authority.result is LinkIndexResult.REBUILD_REQUIRED
    assert authority.sha256 is None


def test_absent_when_no_link_index_results() -> None:
    authority = select_link_index_authority(
        (
            _journal(
                revision=None,
                result=None,
                sha256=None,
                phase=OperationPhase.COMPLETED,
            ),
            _journal(
                revision=None,
                result=None,
                sha256=None,
                phase=OperationPhase.PREPARED,
            ),
        )
    )
    assert authority == LinkIndexAuthority(None, None, None)


def test_compares_huge_revision_numerically_not_lexicographically() -> None:
    big = "9" * 100
    authority = select_link_index_authority((_published("99"), _published(big)))
    assert authority.revision == big


def test_duplicate_revision_identical_result_accepted() -> None:
    authority = select_link_index_authority(
        (
            _published("3"),
            _journal(
                revision="3",
                result=LinkIndexResult.PUBLISHED,
                sha256="a" * 64,
                phase=OperationPhase.COMPLETED,
            ),
        )
    )
    assert authority.revision == "3"
    assert authority.result is LinkIndexResult.PUBLISHED


def test_conflicting_result_same_revision_is_inconsistent_state() -> None:
    with pytest.raises(DraftError) as caught:
        select_link_index_authority((_published("3"), _rebuild("3")))
    assert caught.value.code == CheckCode.OPERATION_INCONSISTENT_STATE.value


def test_conflicting_sha256_same_revision_is_inconsistent_state() -> None:
    with pytest.raises(DraftError) as caught:
        select_link_index_authority(
            (
                _published("3"),
                _journal(
                    revision="3",
                    result=LinkIndexResult.PUBLISHED,
                    sha256="b" * 64,
                    phase=OperationPhase.LINK_INDEX_PUBLISHED,
                ),
            )
        )
    assert caught.value.code == CheckCode.OPERATION_INCONSISTENT_STATE.value


def test_non_result_terminal_journal_ignored() -> None:
    authority = select_link_index_authority(
        (
            _journal(
                revision=None,
                result=None,
                sha256=None,
                phase=OperationPhase.COMPLETED,
            ),
            _published("5"),
        )
    )
    assert authority.revision == "5"
    assert authority.result is LinkIndexResult.PUBLISHED


def test_illegal_revision_format_in_non_terminal_rejected() -> None:
    with pytest.raises(DraftError) as caught:
        select_link_index_authority(
            (
                _journal(
                    revision="01",
                    result=None,
                    sha256=None,
                    phase=OperationPhase.LINK_INDEX_UPDATING,
                ),
            )
        )
    assert caught.value.code == CheckCode.OPERATION_JOURNAL_CORRUPT.value


def test_rebuild_required_and_published_mixed_numeric_max_wins() -> None:
    authority = select_link_index_authority((_published("7"), _rebuild("8")))
    assert authority.revision == "8"
    assert authority.result is LinkIndexResult.REBUILD_REQUIRED


# ---------------------------------------------------------------------------
# next_link_index_revision：absent 返回 "1"，否则数值 +1（spec §3）
# ---------------------------------------------------------------------------


def test_next_revision_absent_returns_one() -> None:
    assert next_link_index_revision(LinkIndexAuthority(None, None, None)) == "1"


def test_next_revision_increments_numerically() -> None:
    assert next_link_index_revision(LinkIndexAuthority("9", None, None)) == "10"


def test_next_revision_increments_huge_decimal() -> None:
    assert next_link_index_revision(LinkIndexAuthority("9" * 100, None, None)) == (
        "1" + "0" * 100
    )


def test_link_index_baseline_is_frozen_pair() -> None:
    authority = LinkIndexAuthority("1", LinkIndexResult.PUBLISHED, "a" * 64)
    baseline = LinkIndexBaseline(authority=authority, index=None)
    assert baseline.authority is authority
    assert baseline.index is None
