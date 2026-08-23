from __future__ import annotations

from pathlib import Path

import pytest

import draft.link_index_maintenance as _maintenance
from draft.diagnostic_codes import CheckCode
from draft.errors import DraftError
from draft.link_index import (
    LinkIndexContent,
    LinkIndexScan,
    LinkIndexScanStats,
)
from draft.link_index_maintenance import (
    LinkIndexAuthority,
    LinkIndexBaseline,
    LinkIndexMaintenanceResult,
    PreparedLinkIndexUpdate,
    commit_link_index_update,
    maintain_link_index,
    next_link_index_revision,
    prepare_link_index_update,
    select_link_index_authority,
)
from draft.models import (
    LinkIndexResult,
    OperationKind,
    OperationPhase,
    ResultWarning,
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


# ---------------------------------------------------------------------------
# maintenance 状态表（spec §9/§14/§15）：prepare_link_index_update 决策
# ---------------------------------------------------------------------------


def _scan(changed: bool) -> LinkIndexScan:
    content = LinkIndexContent(files={}, backlinks={}, directories={})
    return LinkIndexScan(
        content=content,
        changed=changed,
        stats=LinkIndexScanStats(read_files=0, read_bytes=0, parsed_files=0),
    )


def _stub_authority_and_baseline(
    monkeypatch: pytest.MonkeyPatch,
    *,
    revision: str | None,
    result: LinkIndexResult | None,
    baseline_index: object | None,
) -> None:
    authority = LinkIndexAuthority(revision, result, None)
    baseline = LinkIndexBaseline(authority=authority, index=baseline_index)
    monkeypatch.setattr(_maintenance, "read_link_index_authority", lambda root: authority)
    monkeypatch.setattr(_maintenance, "load_link_index_baseline", lambda root: baseline)


def test_prepare_absent_returns_full_build_candidate(
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    _stub_authority_and_baseline(
        monkeypatch, revision=None, result=None, baseline_index=None
    )
    monkeypatch.setattr(
        _maintenance,
        "scan_link_index",
        lambda root, baseline, known_documents=(): _scan(changed=True),
    )

    prepared = prepare_link_index_update(Path("/draft"))

    assert prepared.observed_revision is None
    assert prepared.scan is not None
    assert prepared.requires_rebuild is False


def test_prepare_fresh_returns_no_write_candidate(
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    _stub_authority_and_baseline(
        monkeypatch, revision="5", result=LinkIndexResult.PUBLISHED, baseline_index=object()
    )
    monkeypatch.setattr(
        _maintenance,
        "scan_link_index",
        lambda root, baseline, known_documents=(): _scan(changed=False),
    )

    prepared = prepare_link_index_update(Path("/draft"))

    assert prepared.observed_revision == "5"
    assert prepared.scan is None
    assert prepared.requires_rebuild is False


def test_prepare_stale_returns_incremental_candidate(
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    _stub_authority_and_baseline(
        monkeypatch, revision="5", result=LinkIndexResult.PUBLISHED, baseline_index=object()
    )
    monkeypatch.setattr(
        _maintenance,
        "scan_link_index",
        lambda root, baseline, known_documents=(): _scan(changed=True),
    )

    prepared = prepare_link_index_update(Path("/draft"))

    assert prepared.observed_revision == "5"
    assert prepared.scan is not None
    assert prepared.requires_rebuild is False


def test_prepare_rebuild_required_returns_full_build_candidate(
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    _stub_authority_and_baseline(
        monkeypatch, revision="5", result=LinkIndexResult.REBUILD_REQUIRED, baseline_index=None
    )
    monkeypatch.setattr(
        _maintenance,
        "scan_link_index",
        lambda root, baseline, known_documents=(): _scan(changed=True),
    )

    prepared = prepare_link_index_update(Path("/draft"))

    assert prepared.observed_revision == "5"
    assert prepared.scan is not None
    assert prepared.requires_rebuild is False


def test_prepare_scan_failure_degrades_to_rebuild_required(
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    _stub_authority_and_baseline(
        monkeypatch, revision="5", result=LinkIndexResult.PUBLISHED, baseline_index=object()
    )

    def fail_scan(
        root: Path, baseline: LinkIndexBaseline, *, known_documents: object = ()
    ) -> LinkIndexScan:
        raise DraftError("operation.index_update_failed", root)

    monkeypatch.setattr(_maintenance, "scan_link_index", fail_scan)

    prepared = prepare_link_index_update(Path("/draft"))

    assert prepared.observed_revision == "5"
    assert prepared.scan is None
    assert prepared.requires_rebuild is True


def test_prepare_unstable_scan_degrades_to_rebuild_required(
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    _stub_authority_and_baseline(
        monkeypatch, revision="5", result=LinkIndexResult.PUBLISHED, baseline_index=object()
    )

    def fail_scan(
        root: Path, baseline: LinkIndexBaseline, *, known_documents: object = ()
    ) -> LinkIndexScan:
        raise DraftError("operation.external_change", root)

    monkeypatch.setattr(_maintenance, "scan_link_index", fail_scan)

    prepared = prepare_link_index_update(Path("/draft"))

    assert prepared.requires_rebuild is True


def test_prepare_journal_artifact_contradiction_propagates(
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    authority = LinkIndexAuthority("5", LinkIndexResult.PUBLISHED, "a" * 64)
    monkeypatch.setattr(_maintenance, "read_link_index_authority", lambda root: authority)

    def fail_baseline(root: Path) -> LinkIndexBaseline:
        raise DraftError(CheckCode.OPERATION_INCONSISTENT_STATE.value, root)

    monkeypatch.setattr(_maintenance, "load_link_index_baseline", fail_baseline)

    with pytest.raises(DraftError) as caught:
        prepare_link_index_update(Path("/draft"))
    assert caught.value.code == CheckCode.OPERATION_INCONSISTENT_STATE.value


# ---------------------------------------------------------------------------
# commit_link_index_update：并发令牌与发布/降级（spec §15/§10/§11）
# ---------------------------------------------------------------------------


def _allocating_journal() -> Journal:
    return Journal(
        operation_id="a" * 32,
        kind=OperationKind.LINK_INDEX_UPDATE,
        phase=OperationPhase.ALLOCATING,
        root_state=RootState.REGISTERED,
        observations=EMPTY_OBSERVATIONS,
        mutations=(),
        remove_empty_directories=(),
        metadata={},
        directory=Path("/draft/.draft/operations/" + "a" * 32),
        link_index_revision=None,
        link_index_result=None,
        link_index_sha256=None,
    )


def _commit_stubs(
    monkeypatch: pytest.MonkeyPatch,
    *,
    authority: LinkIndexAuthority,
) -> None:
    monkeypatch.setattr(_maintenance, "acquire_lock", lambda root, operation_id: None)
    monkeypatch.setattr(_maintenance, "release_lock", lambda root, operation_id: None)
    monkeypatch.setattr(_maintenance, "read_link_index_authority", lambda root: authority)
    monkeypatch.setattr(
        _maintenance, "preallocate_journal", lambda root, **kwargs: _allocating_journal()
    )
    monkeypatch.setattr(_maintenance, "write_journal", lambda journal: None)


def test_commit_no_change_does_not_allocate_operation(
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    prepared = PreparedLinkIndexUpdate(observed_revision="5", scan=None, requires_rebuild=False)
    called: dict[str, bool] = {"acquire": False, "preallocate": False}

    def acquire(root: Path, operation_id: str) -> None:
        called["acquire"] = True

    def preallocate(root: Path, **kwargs: object) -> Journal:
        called["preallocate"] = True
        return _allocating_journal()

    monkeypatch.setattr(_maintenance, "acquire_lock", acquire)
    monkeypatch.setattr(_maintenance, "preallocate_journal", preallocate)

    result = commit_link_index_update(Path("/draft"), prepared)

    assert result == LinkIndexMaintenanceResult(index=None, warnings=(), published=False)
    assert not called["acquire"]
    assert not called["preallocate"]


def test_commit_absent_publishes_first_revision(
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    prepared = PreparedLinkIndexUpdate(
        observed_revision=None, scan=_scan(changed=True), requires_rebuild=False
    )
    _commit_stubs(monkeypatch, authority=LinkIndexAuthority(None, None, None))
    monkeypatch.setattr(_maintenance, "stage_link_index", lambda journal, index: "d" * 64)
    monkeypatch.setattr(
        _maintenance, "publish_staged_link_index", lambda root, journal, expected_sha256: None
    )
    monkeypatch.setattr(_maintenance, "remove_rebuild_flag", lambda root: None)

    result = commit_link_index_update(Path("/draft"), prepared)

    assert result.published is True
    assert result.index is not None
    assert result.index.revision == "1"
    assert result.warnings == ()


def test_commit_stale_publishes_numeric_next_revision(
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    prepared = PreparedLinkIndexUpdate(
        observed_revision="5", scan=_scan(changed=True), requires_rebuild=False
    )
    _commit_stubs(
        monkeypatch,
        authority=LinkIndexAuthority("5", LinkIndexResult.PUBLISHED, "a" * 64),
    )
    monkeypatch.setattr(_maintenance, "stage_link_index", lambda journal, index: "d" * 64)
    monkeypatch.setattr(
        _maintenance, "publish_staged_link_index", lambda root, journal, expected_sha256: None
    )
    monkeypatch.setattr(_maintenance, "remove_rebuild_flag", lambda root: None)

    result = commit_link_index_update(Path("/draft"), prepared)

    assert result.published is True
    assert result.index is not None
    assert result.index.revision == "6"


def test_commit_revision_advanced_abandons_candidate(
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    prepared = PreparedLinkIndexUpdate(
        observed_revision="5", scan=_scan(changed=True), requires_rebuild=False
    )
    writes: list[Journal] = []
    monkeypatch.setattr(_maintenance, "acquire_lock", lambda root, operation_id: None)
    monkeypatch.setattr(_maintenance, "release_lock", lambda root, operation_id: None)
    monkeypatch.setattr(
        _maintenance,
        "read_link_index_authority",
        lambda root: LinkIndexAuthority("6", LinkIndexResult.PUBLISHED, "b" * 64),
    )
    monkeypatch.setattr(
        _maintenance, "preallocate_journal", lambda root, **kwargs: _allocating_journal()
    )
    monkeypatch.setattr(_maintenance, "write_journal", writes.append)

    def fail_stage(journal: Journal, index: object) -> str:
        raise AssertionError("stage must not be called when revision advanced")

    monkeypatch.setattr(_maintenance, "stage_link_index", fail_stage)

    result = commit_link_index_update(Path("/draft"), prepared)

    assert result == LinkIndexMaintenanceResult(index=None, warnings=(), published=False)
    # 已预分配的空 allocating journal 写 rolled_back（spec §15 / 纪律 #3）。
    assert len(writes) == 1
    assert writes[0].phase is OperationPhase.ROLLED_BACK


def test_commit_rebuild_required_records_degradation_warning(
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    prepared = PreparedLinkIndexUpdate(observed_revision="5", scan=None, requires_rebuild=True)
    _commit_stubs(
        monkeypatch,
        authority=LinkIndexAuthority("5", LinkIndexResult.PUBLISHED, "a" * 64),
    )
    monkeypatch.setattr(_maintenance, "ensure_rebuild_flag", lambda root: None)
    monkeypatch.setattr(
        _maintenance, "stage_link_index", lambda journal, index: (_ for _ in ()).throw(
            AssertionError("stage must not be called on rebuild path")
        )
    )

    result = commit_link_index_update(Path("/draft"), prepared)

    assert result.published is True
    assert result.index is None
    assert result.warnings == (ResultWarning("link_index.rebuild_required"),)


def test_commit_rebuild_required_flag_failure_warning_order(
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    prepared = PreparedLinkIndexUpdate(observed_revision="5", scan=None, requires_rebuild=True)
    _commit_stubs(
        monkeypatch,
        authority=LinkIndexAuthority("5", LinkIndexResult.PUBLISHED, "a" * 64),
    )
    monkeypatch.setattr(
        _maintenance,
        "ensure_rebuild_flag",
        lambda root: ResultWarning("link_index.rebuild_flag_update_failed"),
    )

    result = commit_link_index_update(Path("/draft"), prepared)

    assert result.warnings == (
        ResultWarning("link_index.rebuild_required"),
        ResultWarning("link_index.rebuild_flag_update_failed"),
    )


def test_commit_lock_held_keeps_in_process_index(
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    prepared = PreparedLinkIndexUpdate(
        observed_revision=None, scan=_scan(changed=True), requires_rebuild=False
    )

    def acquire_locked(root: Path, operation_id: str) -> None:
        raise DraftError(CheckCode.OPERATION_RECOVERY_REQUIRED.value, root)

    monkeypatch.setattr(_maintenance, "acquire_lock", acquire_locked)

    result = commit_link_index_update(Path("/draft"), prepared)

    assert result.published is False
    assert result.index is not None
    assert result.index.revision == "1"
    assert result.warnings == ()


# ---------------------------------------------------------------------------
# maintain_link_index：扫描前读锁（spec §13/§14）
# ---------------------------------------------------------------------------


def test_maintain_lock_held_returns_unpublished_without_scan(
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    monkeypatch.setattr(_maintenance, "read_lock", lambda root: "f" * 32)
    scanned: dict[str, bool] = {"called": False}

    def scan_spy(
        root: Path, baseline: LinkIndexBaseline, *, known_documents: object = ()
    ) -> LinkIndexScan:
        scanned["called"] = True
        return _scan(changed=True)

    monkeypatch.setattr(_maintenance, "scan_link_index", scan_spy)

    result = maintain_link_index(Path("/draft"))

    assert result == LinkIndexMaintenanceResult(index=None, warnings=(), published=False)
    assert not scanned["called"]
